import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from awx_lab.cli import main, parser
from awx_lab.build import ui_cache_key
from awx_lab.cluster import Cluster
from awx_lab.lab import Lab
from awx_lab.local_source import LocalSource, snapshot_local, working_files
from awx_lab.source import PullRequest, git, merge_pr
from awx_lab.system import LabError, Runner
from test_reuse import make_lab


class LocalRepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "developer checkout"
        self.repo.mkdir()
        self.git("init", "-b", "feature")
        self.git("config", "user.name", "Developer")
        self.git("config", "user.email", "developer@example.test")
        for name, contents in {
            "awx/__init__.py": "",
            "awx/main/tasks.py": "original\n",
            "awx/ui/Makefile": "original UI\n",
            "Makefile": "build\n",
            "tools/scripts/scm_version.py": "version\n",
            "requirements/requirements_git.txt": "dependencies\n",
            ".gitignore": "ignored*\n.venv/\n*.pyc\n",
            "ignored-but-tracked.txt": "tracked\n",
            "executable.sh": "#!/bin/sh\nexit 0\n",
        }.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        (self.repo / "executable.sh").chmod(0o755)
        self.git("add", "--force", ".")
        self.git("commit", "-m", "initial")
        self.git("tag", "v1.0.0")
        self.head = self.git("rev-parse", "HEAD")
        self.destination = self.root / "lab" / "awx"
        self.runner = Runner(log_mode="clean")

    def git(self, *args):
        return subprocess.check_output(["git", "--no-optional-locks", "-c", "commit.gpgsign=false", *args],
                                       cwd=self.repo, stderr=subprocess.DEVNULL, text=True).strip()

    def snapshot(self):
        return snapshot_local(self.runner, LocalSource.inspect(self.repo), self.destination)

    def checkout_state(self):
        return {"head": self.git("rev-parse", "HEAD"), "branch": self.git("symbolic-ref", "HEAD"),
                "status": self.git("status", "--porcelain=v1", "--untracked-files=all"),
                "index": (self.repo / ".git/index").read_bytes(),
                "files": working_files(self.runner, self.repo)}


class LocalSourceTests(LocalRepositoryFixture):
    def test_snapshot_includes_working_edits_staged_changes_deletions_and_untracked(self):
        (self.repo / "awx/main/tasks.py").write_text("staged\n")
        self.git("add", "awx/main/tasks.py")
        (self.repo / "awx/main/tasks.py").write_text("latest unstaged\n")
        (self.repo / "requirements/requirements_git.txt").unlink()
        (self.repo / "new file.txt").write_bytes(b"new\x00binary\xff")
        (self.repo / "ignored-secret.txt").write_text("do not copy")
        (self.repo / "ignored-but-tracked.txt").write_text("tracked edit")
        before = self.checkout_state()
        result = self.snapshot()
        self.assertEqual(before, self.checkout_state())
        self.assertEqual((self.destination / "awx/main/tasks.py").read_text(), "latest unstaged\n")
        self.assertFalse((self.destination / "requirements/requirements_git.txt").exists())
        self.assertEqual((self.destination / "new file.txt").read_bytes(), b"new\x00binary\xff")
        self.assertFalse((self.destination / "ignored-secret.txt").exists())
        self.assertEqual((self.destination / "ignored-but-tracked.txt").read_text(), "tracked edit")
        self.assertTrue(result["dirty"])
        self.assertEqual(result["head_sha"], self.head)
        self.assertNotEqual(result["snapshot_sha"], self.head)
        self.assertEqual(len(result["fingerprint"]), 64)
        self.assertEqual(git(self.runner, self.destination, "describe", "--tags", "--abbrev=0"), "v1.0.0")
        self.assertNotIn(str(self.repo), (self.destination / ".git/config").read_text())
        self.assertFalse((self.destination / ".git/objects/info/alternates").exists())

    def test_snapshot_is_stable_and_replaces_previous_snapshot_and_generated_files(self):
        first = self.snapshot()
        self.assertFalse(first["dirty"])
        self.assertEqual(first["snapshot_sha"], self.head)
        (self.repo / "new.py").write_text("first edit")
        dirty = self.snapshot()
        self.assertNotEqual(first["fingerprint"], dirty["fingerprint"])
        (self.destination / "generated.whl").write_text("stale")
        (self.destination / "Dockerfile").write_text("generated")
        self.assertEqual(dirty, self.snapshot())
        self.assertFalse((self.destination / "generated.whl").exists())
        self.assertFalse((self.destination / "Dockerfile").exists())
        (self.repo / "new.py").unlink()
        self.assertEqual(first, self.snapshot())
        self.assertFalse((self.destination / "new.py").exists())

    def test_uncommitted_ui_edits_invalidate_cache_but_backend_edits_reuse_it(self):
        self.snapshot()
        key = ui_cache_key(self.runner, self.destination, "stage", "arm64")
        (self.repo / "awx/main/tasks.py").write_text("changed backend")
        self.snapshot()
        self.assertEqual(key, ui_cache_key(self.runner, self.destination, "stage", "arm64"))
        (self.repo / "awx/ui/Makefile").write_text("changed UI")
        self.snapshot()
        self.assertNotEqual(key, ui_cache_key(self.runner, self.destination, "stage", "arm64"))

    def test_discovery_supports_subdirectories_worktrees_and_renamed_forks(self):
        local = LocalSource.inspect(self.repo / "awx/main", "feature")
        self.assertEqual(local.path, self.repo)
        self.assertEqual(local.repo, "awx")
        worktree = self.root / "linked worktree"
        self.git("worktree", "add", "-b", "linked", str(worktree))
        (worktree / "awx/main/tasks.py").write_text("worktree edit")
        local = LocalSource.inspect(worktree)
        self.assertEqual(local.branch, "linked")
        result = snapshot_local(self.runner, local, self.destination)
        self.assertTrue(result["dirty"])
        self.assertEqual((self.destination / "awx/main/tasks.py").read_text(), "worktree edit")
        self.assertTrue((worktree / ".git").is_file())

    def test_file_modes_and_internal_symlinks_survive_private_umask(self):
        (self.repo / "link").symlink_to("awx/main/tasks.py")
        previous = os.umask(0o077)
        try:
            self.snapshot()
        finally:
            os.umask(previous)
        self.assertEqual((self.destination / "executable.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.destination / "awx/main/tasks.py").stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.destination / "awx/main").stat().st_mode & 0o777, 0o755)
        self.assertTrue((self.destination / "link").is_symlink())

    def test_respects_global_ignores_and_handles_unusual_filenames(self):
        excludes = self.root / "excludes"
        excludes.write_text("global-secret\n")
        config = self.root / "gitconfig"
        config.write_text(f'[core]\nexcludesFile = "{excludes}"\n')
        (self.repo / "global-secret").write_text("private")
        for name in (" leading space", "line\nbreak", "unicode-☺.txt"):
            (self.repo / name).write_text(name)
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            self.snapshot()
        self.assertFalse((self.destination / "global-secret").exists())
        for name in (" leading space", "line\nbreak", "unicode-☺.txt"):
            self.assertEqual((self.destination / name).read_text(), name)

    def test_invalid_branch_wrong_branch_detached_and_outside_git(self):
        self.git("branch", "other")
        for branch, message in (("absent", "does not exist"), ("other", "is checked out"),
                                ("../invalid", "does not exist"), ("v1.0.0", "does not exist")):
            with self.subTest(branch=branch), self.assertRaisesRegex(LabError, message):
                LocalSource.inspect(self.repo, branch)
        with self.assertRaisesRegex(LabError, "Git working directory"):
            LocalSource.inspect(self.root)
        self.git("checkout", "--detach")
        with self.assertRaisesRegex(LabError, "Detached HEAD"):
            LocalSource.inspect(self.repo)

    def test_unborn_bare_and_unsupported_repositories(self):
        empty = self.root / "empty"
        subprocess.check_call(["git", "init", "--quiet", "-b", "new", str(empty)])
        with self.assertRaisesRegex(LabError, "has no commits"):
            LocalSource.inspect(empty)
        bare = self.root / "bare"
        subprocess.check_call(["git", "init", "--quiet", "--bare", str(bare)])
        with self.assertRaisesRegex(LabError, "Git working directory"):
            LocalSource.inspect(bare)
        self.git("rm", "awx/__init__.py")
        with self.assertRaisesRegex(LabError, "Unsupported repository"):
            LocalSource.inspect(self.repo)

    def test_merge_conflicts_sparse_checkout_and_submodules_are_rejected(self):
        self.git("branch", "other")
        (self.repo / "Makefile").write_text("feature")
        self.git("commit", "-am", "feature")
        self.git("checkout", "other")
        (self.repo / "Makefile").write_text("other")
        self.git("commit", "-am", "other")
        subprocess.run(["git", "merge", "feature"], cwd=self.repo, capture_output=True, check=False)
        with self.assertRaisesRegex(LabError, "merge conflicts"):
            LocalSource.inspect(self.repo)
        self.git("merge", "--abort")
        self.git("config", "core.sparseCheckout", "true")
        with self.assertRaisesRegex(LabError, "sparse checkouts"):
            LocalSource.inspect(self.repo)
        self.git("config", "core.sparseCheckout", "false")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.head},submodule")
        with self.assertRaisesRegex(LabError, "submodules"):
            LocalSource.inspect(self.repo)

    def test_rejects_external_links_special_files_and_overlapping_paths(self):
        local = LocalSource.inspect(self.repo)
        for dest in (self.repo, self.repo / "nested", self.root):
            with self.subTest(dest=dest), self.assertRaisesRegex(LabError, "must be separate"):
                snapshot_local(self.runner, local, dest)
        outside = self.root / "secret"
        outside.write_text("secret")
        (self.repo / "external").symlink_to(outside)
        with self.assertRaisesRegex(LabError, "must point inside"):
            self.snapshot()
        self.assertEqual(outside.read_text(), "secret")
        (self.repo / "external").unlink()
        (self.repo / "executable.sh").unlink()
        os.mkfifo(self.repo / "executable.sh")
        with self.assertRaisesRegex(LabError, "special files"):
            self.snapshot()

    def test_detects_changes_during_snapshot_before_build(self):
        calls = 0
        def changing(*args):
            nonlocal calls
            result = working_files(*args)
            calls += 1
            if calls == 1:
                (self.repo / "Makefile").write_text("edit while copying")
            return result
        with patch("awx_lab.local_source.working_files", side_effect=changing), \
                self.assertRaisesRegex(LabError, "changed while being copied"):
            self.snapshot()

    def test_local_snapshot_can_switch_back_to_pr_and_discard_local_edits(self):
        (self.repo / "local-only").write_text("uncommitted")
        self.snapshot()
        self.git("update-ref", "refs/pull/1/head", self.head)
        def local_fetch(runner, path, *args, **kwargs):
            if args[:2] == ("fetch", "origin"):
                args = ("fetch", self.repo.as_uri(), *args[2:])
            return git(runner, path, *args, **kwargs)
        with patch("awx_lab.source.git", side_effect=local_fetch):
            result = merge_pr(self.runner, PullRequest("ansible", "awx", 1), self.destination,
                              {"base": {"ref": "feature"}, "head": {"sha": self.head}}, reuse=True)
        self.assertEqual(result["head_sha"], self.head)
        self.assertFalse((self.destination / "local-only").exists())
        self.assertTrue((self.repo / "local-only").exists())
        self.assertTrue(self.snapshot()["dirty"])

    def test_local_plugin_identity_uses_package_metadata(self):
        self.git("rm", "awx/__init__.py")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "awx-plugins-core"\n')
        self.git("add", ".")
        self.git("commit", "-m", "plugin fixture")
        (self.repo / "plugin.py").write_text("uncommitted plugin")
        local = LocalSource.inspect(self.repo)
        self.assertEqual(local.repo, "awx-plugins")
        lab = make_lab(self.root / "lab-state")
        with patch("awx_lab.lab.checkout_ref", return_value={"sha": "baseline"}) as baseline, \
                patch("awx_lab.lab.merge_pr") as pr:
            sources = lab.prepare_sources({"local": local.selection(), "awx_ref": "devel"})
        baseline.assert_called_once()
        pr.assert_not_called()
        self.assertEqual(sources["awx"]["sha"], "baseline")
        self.assertTrue(sources["awx-plugins"]["dirty"])
        self.assertEqual((lab.path / "awx-plugins/plugin.py").read_text(), "uncommitted plugin")


class LocalCLITests(LocalRepositoryFixture):
    def test_parser_accepts_implicit_and_explicit_branches_and_rejects_mixed_inputs(self):
        self.assertEqual(parser().parse_args(["test", "--local"]).local, "")
        self.assertEqual(parser().parse_args(["test", "--local", "feature"]).local, "feature")
        self.assertEqual(parser().parse_args(["test", "--local", "--full-log"]).log_mode, "full")
        for args in (["test"], ["test", "https://github.com/ansible/awx/pull/1", "--local"],
                     ["test", "--local", "feature", "https://github.com/ansible/awx/pull/1"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                parser().parse_args(args)
            self.assertEqual(caught.exception.code, 2)

    def test_local_validation_fails_before_state_creation_or_runtime_access(self):
        state = self.root / "new-state"
        args = ["--state-dir", str(state), "test", "--local"]
        for cwd, extra, message in ((self.root, [], "Git working directory"),
                                    (self.repo, ["missing"], "does not exist"),
                                    (self.repo, ["--awx-ref", "devel"], "only applies to plugin"),
                                    (self.repo, ["--plugins-pr", "https://github.com/ansible/awx-plugins/pull/1"], "requires an AWX primary")):
            with contextlib.chdir(cwd), contextlib.redirect_stderr(io.StringIO()) as err, \
                    patch("awx_lab.cli.Lab") as lab:
                self.assertEqual(main([*args, *extra]), 1)
                self.assertIn(message, err.getvalue())
                lab.assert_not_called()
                self.assertFalse(state.exists())

    def test_existing_lab_updates_for_uncommitted_changes_and_skips_unchanged_sources(self):
        state = self.root / "state"
        lab = make_lab(state)
        command = ["--state-dir", str(state), "test", "--local"]
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.chdir(self.repo / "awx"))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.object(Lab, "prerequisites"))
            stack.enter_context(patch.object(Lab, "retain_artifacts"))
            checks = stack.enter_context(patch.object(Lab, "run_checks"))
            stack.enter_context(patch.object(Cluster, "require_existing"))
            stack.enter_context(patch.object(Cluster, "wait_ready"))
            refresh = stack.enter_context(patch.object(Cluster, "refresh"))
            builder = stack.enter_context(patch("awx_lab.lab.Builder.build", return_value={"image_id": "new"}))
            with patch("awx_lab.lab.merge_pr") as pr:
                self.assertEqual(main(command), 0)
                pr.assert_not_called()
            first = json.loads((lab.path / "state.json").read_text())
            self.assertEqual(main(command), 0)
            self.assertEqual(builder.call_count, 1)
            (self.repo / "awx/main/tasks.py").write_text("uncommitted one-line change")
            self.assertEqual(main(command), 0)
            self.assertEqual(builder.call_count, 2)
            (self.repo / "ignored-file").write_text("ignored")
            self.assertEqual(main(command), 0)
            self.assertEqual(builder.call_count, 2)
            self.assertEqual(main([*command, "--no-cache"]), 0)
            self.assertEqual(builder.call_count, 3)
            self.assertEqual(refresh.call_count, 3)
            self.assertEqual(checks.call_count, 5)
            latest = json.loads((lab.path / "state.json").read_text())
        self.assertEqual(first["url"], latest["url"])
        self.assertEqual(first["id"], latest["id"])
        self.assertNotEqual(first["image"], latest["image"])
        self.assertTrue(latest["sources"]["awx"]["dirty"])
        self.assertEqual(latest["sources"]["awx"]["head_sha"], self.head)
        self.assertEqual(latest["last_test_result"], "passed")
        self.assertIn("Next test: awx-lab test --local", output.getvalue())
        self.assertIn("includes uncommitted changes", output.getvalue())
