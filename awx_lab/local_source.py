"""Read a developer checkout and snapshot it into a disposable build checkout."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tomllib

from .source import checkout_is_trusted, git, trust_checkout
from .system import LabError, Runner


def read_git(runner, path, *args, check=True):
    # No optional index refreshes or output containing source filenames/content.
    # Respect the user's ignore configuration when enumerating untracked files.
    return runner.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null", *args],
                      cwd=path, capture=True, sensitive=True, check=check)


@dataclass(frozen=True)
class LocalSource:
    path: Path
    repo: str
    branch: str
    head_sha: str

    def selection(self):
        return {"path": str(self.path), "repo": self.repo, "branch": self.branch,
                "head_sha": self.head_sha}

    @classmethod
    def inspect(cls, cwd, branch=None, runner=None):
        runner = runner or Runner()
        overrides = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")
        if any(os.environ.get(key) for key in overrides):
            raise LabError("Unset Git directory/index/object overrides before using --local.")
        result = read_git(runner, cwd, "rev-parse", "--show-toplevel", check=False)
        if result.returncode:
            raise LabError("--local must run inside an AWX or awx-plugins Git working directory.")
        path = Path(result.stdout.rstrip("\n")).resolve()
        current = read_git(runner, path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if current.returncode:
            raise LabError("Detached HEAD: check out a local branch before using --local.")
        current = current.stdout.rstrip("\n")
        branch = current if branch in (None, "") else branch
        valid = read_git(runner, path, "check-ref-format", f"refs/heads/{branch}", check=False)
        exists = read_git(runner, path, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
        if valid.returncode or exists.returncode:
            raise LabError(f"Local branch {branch!r} does not exist or has no commits in {path}.")
        if branch != current:
            raise LabError(f"Requested branch {branch!r}, but {current!r} is checked out. Switch branches and rerun --local.")
        if read_git(runner, path, "ls-files", "--unmerged", "-z").stdout:
            raise LabError("Resolve the checkout's merge conflicts before using --local.")
        if read_git(runner, path, "config", "--bool", "core.sparseCheckout", check=False).stdout.strip() == "true":
            raise LabError("--local requires a full checkout; sparse checkouts are not supported.")
        entries = read_git(runner, path, "ls-files", "--stage", "-z").stdout.split("\0")
        if any(entry.startswith("160000 ") for entry in entries):
            raise LabError("--local does not yet support Git submodules.")
        head = read_git(runner, path, "rev-parse", "HEAD").stdout.strip()
        tracked = {entry.split("\t", 1)[1] for entry in entries if entry}
        awx_files = {"awx/__init__.py", "Makefile", "tools/scripts/scm_version.py",
                     "requirements/requirements_git.txt"}
        if awx_files <= tracked:
            repo = "awx"
        elif "pyproject.toml" in tracked:
            # Inspect committed project identity, even if the working manifest is
            # temporarily invalid during development. The build validates edits.
            try:
                manifest = tomllib.loads(read_git(runner, path, "show", "HEAD:pyproject.toml").stdout)
            except (LabError, tomllib.TOMLDecodeError):
                manifest = {}
            if manifest.get("project", {}).get("name") != "awx-plugins-core":
                raise LabError("Unsupported repository: --local needs an AWX or awx-plugins checkout.")
            repo = "awx-plugins"
        else:
            raise LabError("Unsupported repository: --local needs an AWX or awx-plugins checkout.")
        return cls(path, repo, branch, head)


def working_files(runner, root, destination=None):
    """Hash/copy tracked and non-ignored untracked files without following links."""
    names = read_git(runner, root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
    manifest = []
    for name in sorted(set(names.rstrip("\0").split("\0")) - {""}):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or any(part.lower() == ".git" for part in relative.parts):
            raise LabError(f"Unsupported snapshot path: {name!r}")
        source = root / relative
        if any((root / parent).is_symlink() for parent in relative.parents):
            raise LabError(f"Snapshot path has a symlink directory: {name!r}")
        try:
            info = source.lstat()
        except FileNotFoundError:
            continue  # A working-tree deletion must remain deleted in the image.
        target = destination / relative if destination else None
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(info.st_mode):
            link = os.readlink(source)
            resolved = source.resolve()
            if Path(link).is_absolute() or not resolved.is_relative_to(root) or ".git" in resolved.relative_to(root).parts:
                raise LabError(f"Local symlink {name!r} must point inside the checkout, outside .git.")
            digest = hashlib.sha256(os.fsencode(link)).hexdigest()
            mode = "120000"
            if target:
                target.symlink_to(link)
        elif stat.S_ISREG(info.st_mode):
            mode = "100755" if info.st_mode & stat.S_IXUSR else "100644"
            digest = hashlib.sha256()
            # O_NOFOLLOW also rejects a file changed into a link during copying;
            # O_NONBLOCK prevents a replacement FIFO from hanging the command.
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise LabError(f"Unsupported local file: {name!r}")
                output = target.open("wb") if target else None
                try:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        if output:
                            output.write(chunk)
                finally:
                    if output:
                        output.close()
            if target:
                target.chmod(0o755 if mode == "100755" else 0o644)
            digest = digest.hexdigest()
        else:
            raise LabError(f"Unsupported local path {name!r}; nested repositories and special files are not supported.")
        manifest.append((name, mode, digest))
    return manifest


def snapshot_local(runner, source, destination):
    """Keep Git history for versioning, but never use the developer's index/refs."""
    root = source.path
    destination = destination.resolve() if not destination.is_symlink() else destination
    if destination.is_symlink() or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise LabError("The local checkout and managed build directory must be separate.")
    if LocalSource.inspect(root, source.branch, runner) != source:
        raise LabError("The local branch changed before snapshotting. Rerun --local.")
    if destination.exists() and (not (destination / ".git").is_dir() or (destination / ".git").is_symlink()):
        raise LabError(f"Refusing to replace an unmanaged build directory: {destination}")
    if destination.exists() and not checkout_is_trusted(destination):
        # Previous versions mounted this checkout read/write in a build helper.
        # Remove its Git config before running any Git command against it.
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if not (destination / ".git").exists():
        git(runner, destination, "init")
        git(runner, destination, "remote", "add", "origin", f"https://github.com/ansible/{source.repo}.git")
    # Fetch locally instead of copying .git, hooks, configuration, or hardlinks.
    # Mirror tag refs explicitly as well, so AWX/setuptools-scm can derive its
    # version even when the local branch is the only fetched branch.
    git(runner, destination, "fetch", "--prune", "--no-recurse-submodules", root.as_uri(),
        f"refs/heads/{source.branch}", "+refs/tags/*:refs/tags/*")
    if git(runner, destination, "rev-parse", "FETCH_HEAD") != source.head_sha:
        raise LabError("The local branch changed while reading its history. Rerun --local.")
    for child in destination.iterdir():
        if child.name != ".git":
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    git(runner, destination, "update-ref", "--no-deref", "HEAD", source.head_sha)
    git(runner, destination, "read-tree", "HEAD")
    manifest = working_files(runner, root, destination)
    if working_files(runner, root) != manifest or LocalSource.inspect(root, source.branch, runner) != source:
        raise LabError("Local files changed while being copied. Save your edits and rerun --local.")
    # Only enumerated files were copied. Force-add keeps tracked files that now
    # match ignore rules; generated/ignored untracked files were never copied.
    git(runner, destination, "add", "--all", "--force", ".")
    tree = git(runner, destination, "write-tree")
    dirty = tree != git(runner, destination, "rev-parse", "HEAD^{tree}")
    snapshot = source.head_sha
    if dirty:
        date = git(runner, destination, "show", "-s", "--format=%cI", source.head_sha)
        previous = runner.env.copy()
        runner.env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        try:
            snapshot = git(runner, destination, "commit-tree", tree, "-p", source.head_sha,
                           "-m", "AWX Lab local working snapshot")
        finally:
            runner.env = previous
        git(runner, destination, "update-ref", "--no-deref", "HEAD", snapshot)
    # Source directories need to be traversable by the image's non-root user.
    for directory, children, _ in os.walk(destination):
        children[:] = [name for name in children if name != ".git"]
        Path(directory).chmod(0o755)
    fingerprint = hashlib.sha256(json.dumps(manifest, ensure_ascii=True).encode()).hexdigest()
    trust_checkout(destination)
    return {"mode": "local", **source.selection(), "dirty": dirty,
            "snapshot_sha": snapshot, "tree_sha": tree, "fingerprint": fingerprint}
