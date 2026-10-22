import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

from awx_lab.source import PullRequest, checkout_ref, clone, merge_pr, prepare_checkout, restore_checkout_modes
from awx_lab.system import LabError, Runner


class SourceTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(PullRequest.parse("https://github.com/ansible/awx-plugins/pull/42/").number, 42)
        for url in ("http://github.com/ansible/awx/pull/1", "https://evil.test/awx/pull/1",
                    "https://github.com/ansible/other/pull/1", "https://github.com/ansible/awx/pull/1;touch /tmp/pwn"):
            with self.assertRaises(LabError):
                PullRequest.parse(url)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repo = self.root / "origin"
        self.repo.mkdir()
        self.git("init", "-b", "devel")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@localhost")
        (self.repo / "common").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.git("checkout", "-b", "feature")
        (self.repo / "feature").write_text("PR change\n")
        self.git("add", ".")
        self.git("commit", "-m", "feature")
        self.head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/pull/7/head", self.head)
        self.git("checkout", "devel")
        (self.repo / "target-change").write_text("new target commit\n")
        self.git("add", ".")
        self.git("commit", "-m", "target advanced")
        self.base = self.git("rev-parse", "HEAD")

    def tearDown(self):
        self.directory.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "commit.gpgsign=false", *args], cwd=self.repo, stderr=subprocess.DEVNULL, text=True).strip()

    def merge(self, name="checkout", expected=None):
        with patch("awx_lab.source.clone", side_effect=lambda runner, remote, path: clone(runner, str(self.repo), path)):
            return merge_pr(Runner(), PullRequest("ansible", "awx", 7), self.root / name,
                            {"base": {"ref": "devel"}, "head": {"sha": expected or self.head}})

    def test_merge_contains_pr_and_latest_target_with_recorded_parents(self):
        result = self.merge()
        checkout = self.root / "checkout"
        self.assertEqual((checkout / "feature").read_text(), "PR change\n")
        self.assertTrue((checkout / "target-change").exists())
        parents = subprocess.check_output(["git", "show", "-s", "--format=%P", "HEAD"], cwd=checkout, text=True).split()
        self.assertEqual(parents, [self.base, self.head])
        self.assertEqual(result["merge_sha"], self.merge("repeat")["merge_sha"])

    def test_moving_pr_is_rejected(self):
        with self.assertRaisesRegex(LabError, "changed while"):
            self.merge(expected="0" * 40)

    def test_private_umask_preserves_source_file_modes_for_container_users(self):
        scripts = self.repo / "scripts"
        scripts.mkdir()
        executable = scripts / "launch.sh"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        (scripts / "config.txt").write_text("config\n")
        (scripts / "link").symlink_to("launch.sh")
        private = self.root / "private.txt"
        private.write_text("private\n")
        private.chmod(0o600)
        links = self.repo / "links"
        links.mkdir()
        (links / "external").symlink_to(private)
        self.git("add", ".")
        self.git("commit", "-m", "runtime files")
        parent = self.root / "private run"
        parent.mkdir(mode=0o700)
        checkout = parent / "awx"
        previous = os.umask(0o077)
        try:
            log = parent / "checkout.log"
            checkout_ref(Runner(log), str(self.repo), "devel", checkout)
            self.assertNotIn(b"\0", log.read_bytes())
            # Simulate a checkout retained from an older CLI, then repair it too.
            for attempt in range(2):
                if attempt:
                    (checkout / "scripts/launch.sh").chmod(0o700)
                    (checkout / "scripts/config.txt").chmod(0o600)
                    (checkout / "scripts").chmod(0o700)
                    restore_checkout_modes(Runner(), checkout)
                self.assertEqual(stat.S_IMODE((checkout / "scripts/launch.sh").stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE((checkout / "scripts/config.txt").stat().st_mode), 0o644)
                self.assertEqual(stat.S_IMODE((checkout / "scripts").stat().st_mode), 0o755)
                self.assertTrue((checkout / "scripts/link").is_symlink())
                self.assertEqual(stat.S_IMODE((checkout / "links").stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((checkout / ".git").stat().st_mode), 0o700)
        finally:
            os.umask(previous)

    def test_pr_merge_also_restores_readable_source_under_private_umask(self):
        previous = os.umask(0o077)
        try:
            self.merge()
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE((self.root / "checkout/feature").stat().st_mode), 0o644)

    def test_refresh_fetches_new_pr_and_target_without_recloning(self):
        original = self.merge()
        checkout = self.root / "checkout"
        (checkout / "generated-wheel.whl").write_text("old generated artifact")
        self.git("checkout", "feature")
        (self.repo / "feature").write_text("updated PR change\n")
        self.git("commit", "-am", "update PR")
        self.head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/pull/7/head", self.head)
        self.git("checkout", "devel")
        (self.repo / "target-change").write_text("updated target\n")
        self.git("commit", "-am", "update target")
        with patch.object(PullRequest, "remote", new_callable=PropertyMock, return_value=str(self.repo)), \
             patch("awx_lab.source.clone") as clone_mock:
            updated = merge_pr(Runner(), PullRequest("ansible", "awx", 7), checkout,
                               {"base": {"ref": "devel"}, "head": {"sha": self.head}}, reuse=True)
        clone_mock.assert_not_called()
        self.assertNotEqual(original["merge_sha"], updated["merge_sha"])
        self.assertEqual((checkout / "feature").read_text(), "updated PR change\n")
        self.assertEqual((checkout / "target-change").read_text(), "updated target\n")
        self.assertFalse((checkout / "generated-wheel.whl").exists())

    def test_refresh_rejects_an_unexpected_remote_before_resetting(self):
        self.merge()
        checkout = self.root / "checkout"
        (checkout / "common").write_text("local modification\n")
        with self.assertRaisesRegex(LabError, "Unexpected source remote"):
            merge_pr(Runner(), PullRequest("ansible", "awx", 7), checkout,
                     {"base": {"ref": "devel"}, "head": {"sha": self.head}}, reuse=True)
        self.assertEqual((checkout / "common").read_text(), "local modification\n")

    def test_switching_pr_discards_previous_pr_changes(self):
        self.merge()
        checkout = self.root / "checkout"
        self.git("checkout", "-b", "another-pr", "devel")
        (self.repo / "second-feature").write_text("second PR\n")
        self.git("add", ".")
        self.git("commit", "-m", "second PR")
        head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/pull/8/head", head)
        with patch.object(PullRequest, "remote", new_callable=PropertyMock, return_value=str(self.repo)):
            result = merge_pr(Runner(), PullRequest("ansible", "awx", 8), checkout,
                              {"base": {"ref": "devel"}, "head": {"sha": head}}, reuse=True)
        self.assertFalse((checkout / "feature").exists())
        self.assertTrue((checkout / "second-feature").exists())
        self.assertTrue((checkout / "target-change").exists())
        self.assertEqual(result["head_sha"], head)

    def test_switching_public_repository_retains_git_cache(self):
        self.merge()
        checkout = self.root / "checkout"
        subprocess.check_call(["git", "remote", "set-url", "origin", "https://github.com/ansible/awx.git"], cwd=checkout)
        prepare_checkout(Runner(), "https://github.com/example/awx.git", checkout, reuse=True, switch_remote=True)
        remote = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=checkout, text=True).strip()
        self.assertEqual(remote, "https://github.com/example/awx.git")
        self.assertTrue((checkout / ".git/objects").is_dir())
        with self.assertRaises(LabError):
            prepare_checkout(Runner(), "https://github.com/example/awx-plugins.git", checkout, reuse=True, switch_remote=True)

    def test_merge_conflict_is_reported_without_testing_target_alone(self):
        self.git("checkout", "feature")
        (self.repo / "common").write_text("PR value\n")
        self.git("commit", "-am", "PR conflict")
        self.head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/pull/7/head", self.head)
        self.git("checkout", "devel")
        (self.repo / "common").write_text("target value\n")
        self.git("commit", "-am", "target conflict")
        with self.assertRaisesRegex(LabError, "Cannot merge"):
            self.merge()


if __name__ == "__main__":
    unittest.main()
