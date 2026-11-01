"""Resolve public GitHub PRs and prepare the exact local merge being tested."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import urllib.error
import urllib.request

from .system import LabError, Runner


@dataclass(frozen=True)
class PullRequest:
    owner: str
    repo: str
    number: int

    @classmethod
    def parse(cls, value):
        match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/(awx|awx-plugins)/pull/([1-9][0-9]*)/?", value)
        if not match:
            raise LabError("Use a GitHub PR URL: https://github.com/<owner>/awx/pull/123 or .../awx-plugins/pull/123")
        return cls(match[1], match[2], int(match[3]))

    @property
    def url(self):
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def remote(self):
        return f"https://github.com/{self.owner}/{self.repo}.git"


def github_pr(pr: PullRequest):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "awx-lab/0.1", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            value = json.load(response)
    except urllib.error.HTTPError as error:
        raise LabError(f"GitHub returned HTTP {error.code} for {pr.url}; check the URL and API rate limit (GITHUB_TOKEN is optional).") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise LabError(f"Cannot read PR metadata: {error}") from error
    if value.get("base", {}).get("repo", {}).get("private"):
        raise LabError("This version supports public repositories only; private build credentials are not forwarded.")
    if value.get("state") != "open":
        raise LabError(f"PR is {value.get('state', 'unknown')}; provide an open PR.")
    return value


def git(runner: Runner, path: Path, *args, log_output=True):
    return runner.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                       "-c", "user.name=AWX Lab", "-c", "user.email=awx-lab@localhost", *args],
                      cwd=path, capture=True, timeout=900, sensitive=not log_output,
                      env={"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}).stdout.strip()


def clone(runner, remote, destination):
    git(runner, destination.parent, "clone", "--filter=blob:none", "--no-checkout", "--", remote, str(destination))
    trust_checkout(destination)


def checkout_marker(path):
    # Stored outside the source mount. Older labs lack it and must be refreshed
    # before any host Git command reads their possibly build-modified .git config.
    return path.parent / f".{path.name}-isolated-build-v1"


def trust_checkout(path):
    marker = checkout_marker(path)
    if marker.is_symlink():
        raise LabError(f"Refusing a symlinked checkout marker: {marker}")
    marker.write_text("isolated-build-v1\n")
    marker.chmod(0o600)


def checkout_is_trusted(path):
    marker = checkout_marker(path)
    return not marker.is_symlink() and marker.is_file() and marker.read_text() == "isolated-build-v1\n"


def restore_checkout_modes(runner, path):
    """Keep Git's file modes when a private workspace is copied into an image."""
    directories = {path}
    # NUL-separated metadata should not turn the human-readable run log binary.
    for entry in git(runner, path, "ls-files", "--stage", "-z", log_output=False).split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, _, stage = metadata.split()
        if stage != "0" or mode not in ("100644", "100755", "120000"):
            continue
        target = path / name
        if mode != "120000":
            if target.is_symlink():
                raise LabError(f"Expected a regular tracked source file: {target}")
            target.chmod(int(mode, 8) & 0o777)
        parent = target.parent
        while parent != path:
            directories.add(parent)
            parent = parent.parent
    for directory in directories:
        directory.chmod(0o755)
    # The enclosing run directory remains private; .git and reports are untouched.


def prepare_checkout(runner, remote, path, reuse=False, switch_remote=False):
    if not reuse:
        clone(runner, remote, path)
        return
    if path.is_symlink() or not (path / ".git").is_dir() or (path / ".git").is_symlink():
        raise LabError(f"Cannot refresh managed checkout at {path}; create a new lab.")
    if not checkout_is_trusted(path):
        # One-time migration of a checkout built by older releases. Do not ask
        # Git to read its config before discarding it.
        shutil.rmtree(path)
        clone(runner, remote, path)
        return
    previous = git(runner, path, "remote", "get-url", "origin")
    if previous != remote:
        public_repo = r"https://github\.com/[A-Za-z0-9_.-]+/(awx|awx-plugins)\.git"
        old, new = re.fullmatch(public_repo, previous), re.fullmatch(public_repo, remote)
        if not switch_remote or not old or not new or old[1] != new[1]:
            raise LabError(f"Unexpected source remote at {path}; create a new lab.")
        git(runner, path, "remote", "set-url", "origin", remote)
    # These are disposable, CLI-owned source copies, never the user's project.
    # Remove generated Dockerfiles/wheels and recover from a previous merge conflict.
    git(runner, path, "reset", "--hard", "HEAD")
    git(runner, path, "clean", "-ffdqx")


def merge_pr(runner, pr, path, metadata=None, reuse=False, switch_remote=False):
    metadata = metadata if metadata is not None else github_pr(pr)
    base_ref = metadata["base"]["ref"]
    expected_head = metadata["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise LabError("GitHub returned an invalid PR head SHA.")
    prepare_checkout(runner, pr.remote, path, reuse, switch_remote)
    git(runner, path, "check-ref-format", f"refs/heads/{base_ref}")
    git(runner, path, "fetch", "origin", f"refs/heads/{base_ref}")
    base_sha = git(runner, path, "rev-parse", "FETCH_HEAD")
    git(runner, path, "checkout", "--detach", base_sha)
    git(runner, path, "fetch", "origin", f"refs/pull/{pr.number}/head")
    head_sha = git(runner, path, "rev-parse", "FETCH_HEAD")
    if head_sha != expected_head:
        raise LabError("The PR changed while it was being fetched. Run the command again to test the new revision.")
    # Keep the local merge deterministic across repeated tests of the same revisions.
    date = git(runner, path, "show", "-s", "--format=%cI", head_sha)
    previous = runner.env.copy()
    runner.env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    try:
        git(runner, path, "merge", "--no-ff", "--no-edit", head_sha)
    except LabError as error:
        raise LabError(f"Cannot merge {pr.url} into {base_ref}. The checkout is retained at {path}.\n{error}") from error
    finally:
        runner.env = previous
    restore_checkout_modes(runner, path)
    return {"url": pr.url, "base_ref": base_ref, "base_sha": base_sha,
            "head_sha": head_sha, "merge_sha": git(runner, path, "rev-parse", "HEAD")}


def checkout_ref(runner, remote, ref, path, reuse=False, switch_remote=False):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref) or ".." in ref:
        raise LabError("Invalid Git ref; use a branch, tag or commit SHA.")
    prepare_checkout(runner, remote, path, reuse, switch_remote)
    git(runner, path, "fetch", "origin", ref)
    git(runner, path, "checkout", "--detach", "FETCH_HEAD")
    restore_checkout_modes(runner, path)
    return {"remote": remote, "ref": ref, "sha": git(runner, path, "rev-parse", "HEAD")}
