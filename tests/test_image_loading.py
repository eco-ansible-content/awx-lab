from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from awx_lab.cluster import Cluster
from awx_lab.system import LabError


class ImageLoadingTests(unittest.TestCase):
    image = "localhost/awx-lab:awx-lab-test-new"
    cluster_name = "awx-lab-test"

    def exercise_podman(self, failure=None, interrupt=False):
        with tempfile.TemporaryDirectory(prefix="awx lab test ") as directory:
            root = Path(directory)
            sentinel = root / "state.json"
            sentinel.write_text("preserved")
            runner = Mock(env={})
            archive = None

            def run(command, **kwargs):
                nonlocal archive
                if command[:2] == ["podman", "save"]:
                    archive = Path(command[5])
                    self.assertEqual(command, ["podman", "save", "--format", "docker-archive", "--output", archive, self.image])
                    self.assertEqual(archive.parent.parent, root)
                    archive.write_bytes(b"partial archive" if failure == "save" else b"image archive")
                    stage = "save"
                else:
                    self.assertEqual(command, ["kind", "load", "image-archive", archive, "--name", self.cluster_name])
                    self.assertTrue(archive.is_file())
                    self.assertEqual(runner.env["KIND_EXPERIMENTAL_PROVIDER"], "podman")
                    stage = "import"
                if failure == stage:
                    if interrupt:
                        raise KeyboardInterrupt()
                    raise LabError(f"{stage} failed")
                return Mock(returncode=0)

            runner.run.side_effect = run
            cluster = Cluster(runner, root, {"runtime": "podman", "id": self.cluster_name})
            if failure:
                with self.assertRaises(KeyboardInterrupt if interrupt else LabError):
                    cluster.load_image(self.image)
            else:
                cluster.load_image(self.image)
            self.assertIsNotNone(archive)
            self.assertFalse(archive.parent.exists())
            self.assertEqual(sentinel.read_text(), "preserved")
            self.assertEqual(runner.run.call_count, 1 if failure == "save" else 2)

    def test_podman_loads_exported_image_without_docker_and_removes_archive(self):
        self.exercise_podman()

    def test_export_failure_cleans_partial_archive_and_never_imports(self):
        self.exercise_podman(failure="save")

    def test_import_failure_preserves_error_and_removes_archive(self):
        self.exercise_podman(failure="import")

    def test_interrupt_during_export_or_import_removes_archive(self):
        for stage in ("save", "import"):
            with self.subTest(stage=stage):
                self.exercise_podman(failure=stage, interrupt=True)

    def test_docker_keeps_direct_image_loader(self):
        runner = Mock(env={})
        cluster = Cluster(runner, Path("/unused"), {"runtime": "docker", "id": self.cluster_name})
        cluster.load_image(self.image)
        runner.run.assert_called_once_with(["kind", "load", "docker-image", self.image, "--name", self.cluster_name], timeout=600)
        self.assertEqual(runner.env["KIND_EXPERIMENTAL_PROVIDER"], "docker")
