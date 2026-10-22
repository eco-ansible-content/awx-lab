from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from awx_lab.source import PullRequest, clone, merge_pr
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
