import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from awx_lab.cluster import Cluster
from awx_lab.retention import image_candidates, prune_history, prune_images, remember_success
from awx_lab.system import LabError, Runner, read_log
from test_reuse import make_lab


class RetentionTests(unittest.TestCase):
    def test_many_successful_updates_bound_images_and_history(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            lab = make_lab(Path(directory))
            original = lab.state["image"]
            present = {original}
            lab.state["images"] = [original]
            def runtime(command, **kwargs):
                if command[1:3] == ["image", "ls"]:
                    return Mock(stdout="\n".join(present))
                self.assertEqual(command[1:4], ["image", "rm", "--no-prune"])
                present.remove(command[-1])
                return Mock(stdout="", returncode=0)
            with patch.object(lab.runner, "run", side_effect=runtime), \
                    patch.object(lab.cluster, "prune_images", side_effect=lambda tags: set(tags)) as node_cleanup:
                for number in range(15):
                    image = original + f"-{number}"
                    ui = f"localhost/awx-lab-ui:{lab.state['id']}-{number}"
                    def update():
                        lab.track_image(image)
                        lab.track_image(ui)
                        present.update((image, ui))
                        lab.state.update(image=image, build={"ui_image": ui})
                    self.assertEqual(lab.operation("test", update), 0)
                last_previous = lab.state["previous_good"]
                lab.operation("up", lambda: None)
                self.assertEqual(lab.state["previous_good"], last_previous)
            self.assertEqual(present, {original + "-14", original + "-13",
                f"localhost/awx-lab-ui:{lab.state['id']}-14", f"localhost/awx-lab-ui:{lab.state['id']}-13"})
            self.assertEqual(set(lab.state["images"]), present)
            self.assertLessEqual(len(list((lab.path / "history").iterdir())), 10)
            self.assertEqual(lab.state["status"], "ready")
            self.assertGreater(node_cleanup.call_count, 0)

    def test_failed_update_keeps_last_success_and_next_success_cleans_failed_image(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            lab = make_lab(Path(directory))
            old = lab.state["image"]
            failed, new = old + "-failed", old + "-new"
            def fail():
                lab.state.update(image=failed, images=[old, failed])
                raise LabError("smoke failed")
            with patch.object(lab.cluster, "diagnostics"), patch("awx_lab.lab.prune_images") as cleanup:
                with self.assertRaisesRegex(LabError, "smoke failed"):
                    lab.operation("test", fail)
                cleanup.assert_not_called()
            self.assertEqual(lab.state["last_good"]["image"], old)
            lab.state["image"] = new
            remember_success(lab.state)
            self.assertEqual(lab.state["previous_good"]["image"], old)
            self.assertEqual(image_candidates(lab.state), {failed})

    def test_pending_image_and_ui_are_protected_and_unowned_tags_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            old = lab.state["image"]
            ui = f"localhost/awx-lab-ui:{lab.state['id']}-pending"
            lab.state.update(images=[old, old + "-pending", ui, old + "-obsolete"],
                             pending_update={"image": old + "-pending", "build": {"ui_image": ui}})
            self.assertEqual(image_candidates(lab.state), {old + "-obsolete"})
            lab.state["images"].append("localhost/awx-lab:awx-lab-someone-else")
            with patch.object(lab.cluster, "prune_images") as cleanup, self.assertRaises(LabError):
                prune_images(lab)
            cleanup.assert_not_called()

    def test_cleanup_failure_is_a_warning_and_remains_retryable(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            lab = make_lab(Path(directory))
            old = lab.state["image"] + "-obsolete"
            lab.state["images"] = [lab.state["image"], old]
            with patch.object(lab.cluster, "prune_images", return_value={old}), \
                    patch.object(lab.runner, "run", side_effect=[Mock(stdout=old), LabError("image in use")]):
                self.assertEqual(lab.operation("test", lambda: None), 0)
            self.assertIn(old, lab.state["images"])
            self.assertEqual(lab.state["last_test_result"], "passed")
            self.assertIn("image in use", output.getvalue())
            self.assertTrue(lab.state["retention"]["warnings"])
            with patch.object(lab.cluster, "prune_images", return_value={old}), \
                    patch.object(lab.runner, "run", return_value=Mock(stdout="")):
                prune_images(lab)
            self.assertNotIn(old, lab.state["images"])

    def test_history_keeps_newest_ten_and_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            history = path / "history"
            history.mkdir()
            outside = path / "outside"
            outside.mkdir()
            (outside / "keep").write_text("valuable")
            for index in range(15):
                folder = history / f"20261022-120000-{index:06x}"
                folder.mkdir()
                (folder / "report.json").write_text("{}")
            (history / "20261021-000000-000000").symlink_to(outside, target_is_directory=True)
            (history / "notes").mkdir()
            self.assertEqual(prune_history(path), 5)
            self.assertTrue((history / "20261022-120000-00000e").exists())
            self.assertFalse((history / "20261022-120000-000000").exists())
            self.assertEqual((outside / "keep").read_text(), "valuable")
            self.assertTrue((history / "notes").exists())


class NodeRetentionTests(unittest.TestCase):
    def test_only_unused_images_with_exclusively_obsolete_aliases_are_removed(self):
        for runtime in ("podman", "docker"):
            with self.subTest(runtime=runtime):
                prefix = "localhost/awx-lab:awx-lab-test"
                tag = lambda name: prefix + "-" + name
                images = [
                    {"id": "old-id", "repoTags": [tag("old")], "repoDigests": []},
                    {"id": "shared-id", "repoTags": [tag("shared"), tag("current")]},
                    {"id": "foreign-id", "repoTags": [tag("foreign"), "other:latest"]},
                    {"id": "used-id", "repoTags": [tag("used")]},
                    {"id": "pending-id", "repoTags": [tag("pending")]},
                    {"id": "pinned-id", "repoTags": [tag("pinned")], "pinned": True},
                    {"id": "digest-id", "repoTags": [tag("digest")], "repoDigests": ["some@sha256:abc"]},
                ]
                removed = []
                def run(command, **kwargs):
                    self.assertEqual(command[:4], [runtime, "exec", "owned-node", "crictl"])
                    if command[4] == "images":
                        return Mock(stdout=json.dumps({"images": images}))
                    if command[4] == "ps":
                        return Mock(stdout=json.dumps({"containers": [
                            {"imageRef": "used-id", "state": "CONTAINER_RUNNING"},
                            {"imageRef": "old-id", "state": "CONTAINER_EXITED"}]}))
                    self.assertEqual(command[4], "rmi")
                    removed.append(command[5])
                    images[:] = [item for item in images if item["id"] != command[5]]
                    return Mock(returncode=0)
                cluster = Cluster(Mock(env={}, run=Mock(side_effect=run)), Path("/unused"),
                                  {"id": "awx-lab-test", "runtime": runtime})
                pods = {"items": [{"spec": {"initContainers": [{"image": tag("pending")}]},
                                   "status": {"phase": "Pending"}}]}
                candidates = {tag(name) for name in ("old", "shared", "foreign", "used", "pending", "pinned", "digest", "absent")}
                with patch.object(cluster, "nodes", return_value=["owned-node"]), \
                        patch.object(cluster, "kubectl", return_value=Mock(stdout=json.dumps(pods))):
                    result = cluster.prune_images(candidates)
                self.assertEqual(removed, ["old-id"])
                self.assertEqual(result, {tag("old"), tag("absent")})

    def test_cluster_queries_fail_closed_without_host_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = make_lab(Path(directory))
            lab.state["images"] = [lab.state["image"] + "-old"]
            with patch.object(lab.cluster, "prune_images", side_effect=LabError("Kubernetes unavailable")), \
                    patch.object(lab.runner, "run") as runtime, self.assertRaises(LabError):
                prune_images(lab)
            runtime.assert_not_called()

    def test_other_environment_tag_rejected_before_any_command(self):
        runner = Mock(env={})
        cluster = Cluster(runner, Path("/unused"), {"id": "awx-lab-test", "runtime": "podman"})
        with self.assertRaises(LabError):
            cluster.prune_images({"localhost/awx-lab:awx-lab-another"})
        runner.run.assert_not_called()


class LogRetentionTests(unittest.TestCase):
    def test_large_unicode_write_and_old_log_are_bounded_without_losing_terminal_output(self):
        with tempfile.TemporaryDirectory() as directory, patch("awx_lab.system.LOG_MAX_BYTES", 100):
            path = Path(directory) / "run.log"
            path.write_text("old" * 1000)
            value = "🧪" * 300 + "LATEST\n"
            with contextlib.redirect_stdout(io.StringIO()) as output:
                Runner(path).write(value)
            self.assertEqual(output.getvalue(), value)
            files = list(path.parent.glob("run.log*"))
            self.assertLessEqual(len(files), 4)
            self.assertTrue(path.read_text().endswith("LATEST\n"))
            for file in files:
                self.assertLessEqual(file.stat().st_size, 100)
                file.read_text()  # UTF-8 boundaries survive rotation.

    def test_follow_continues_after_rotation(self):
        with tempfile.TemporaryDirectory() as directory, patch("awx_lab.system.LOG_MAX_BYTES", 100):
            path = Path(directory) / "run.log"
            runner = Runner(path)
            runner.write("first\n", echo=False)
            reader = read_log(path, follow=True)
            self.assertEqual(next(reader), "first\n")
            def rotate(_seconds):
                runner.write("x" * 94, echo=False)
                runner.write("after rotation\n", echo=False)
            with patch("awx_lab.system.time.sleep", side_effect=rotate):
                self.assertEqual(next(reader), "x" * 94)
                self.assertEqual(next(reader), "after rotation\n")
            reader.close()

    def test_log_symlink_cannot_overwrite_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            outside = path / "keep"
            outside.write_text("unchanged")
            (path / "run.log").symlink_to(outside)
            with self.assertRaises(LabError):
                Runner(path / "run.log").write("bad", echo=False)
            self.assertEqual(outside.read_text(), "unchanged")
