import contextlib
import io
import json
import os
import fcntl
import pty
import struct
import sys
import termios
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from awx_lab.cli import main
from awx_lab.terminal import CommandProcess, LogTail, TextBuffer, cell_slice, cell_width
from awx_lab.tui import Console, draw


class FakeJob:
    def __init__(self, *args):
        self.started = time.monotonic()
        self.output = []
        self.returncode = None
        self.answers = []
        self.cancelled = self.closed = False

    @property
    def finished(self):
        return self.returncode is not None

    def poll(self):
        return self.output.pop(0) if self.output else ""

    def answer(self, value):
        self.answers.append(value)

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


class Screen:
    def __init__(self, height, width):
        self.height, self.width = height, width
        self.erase()

    def erase(self):
        self.cells = [[" " for _ in range(self.width)] for _ in range(self.height)]
        self.styles = [[0 for _ in range(self.width)] for _ in range(self.height)]

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, y, x, text, style):
        assert 0 <= y < self.height
        assert 0 <= x and x + cell_width(text) < self.width
        for char in text:
            self.cells[y][x] = char
            self.styles[y][x] = style
            x += cell_width(char)

    def move(self, y, x):
        assert 0 <= y < self.height and 0 <= x < self.width

    def refresh(self):
        pass

    def text(self):
        return "\n".join("".join(row) for row in self.cells)


FAKE_CURSES = Mock(A_BOLD=1, A_DIM=2, error=RuntimeError, color_pair=lambda number: 4)


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.factory = Mock(side_effect=FakeJob)
        self.model = Console(self.root / "state", cwd=self.root, process_factory=self.factory)
        self.model.last_refresh = time.monotonic()

    def test_no_argument_interactive_launch_and_non_tty_help_do_not_create_a_lab(self):
        for tty in (True, False):
            with self.subTest(tty=tty), patch("sys.stdin.isatty", return_value=tty), \
                    patch("sys.stdout.isatty", return_value=tty), patch("awx_lab.tui.run", return_value=0) as launch, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                # Redirected stdout has its own isatty, so patch that stream too.
                with patch.object(output, "isatty", return_value=tty):
                    self.assertEqual(main(["--state-dir", str(self.model.state_dir)]), 0)
                self.assertEqual(launch.call_count, int(tty))
                if not tty:
                    self.assertIn("usage:", output.getvalue())
            self.assertFalse(self.model.state_dir.exists())

    def test_shell_commands_and_invalid_arguments_never_start_work(self):
        for value in ("git status", "cd /tmp", "sudo anything", "up && down", "up --made-up", "test", "test '"):
            self.model.submit(value)
        self.factory.assert_not_called()
        self.assertFalse(self.model.state_dir.exists())

    def test_local_and_combined_pr_commands_preserve_launch_directory_and_flags(self):
        for command, arguments in (
                ("test --local my-branch", ["test", "--local", "my-branch"]),
                ("awx-lab test https://github.com/ansible/awx/pull/1 --plugins-pr https://github.com/ansible/awx-plugins/pull/2",
                 ["test", "https://github.com/ansible/awx/pull/1", "--plugins-pr", "https://github.com/ansible/awx-plugins/pull/2"])):
            self.model.job = None
            self.model.submit(command)
            self.factory.assert_called_with(arguments, self.root, self.model.state_dir)

    def test_busy_commands_allow_help_status_and_log_navigation(self):
        self.model.submit("up")
        self.model.submit("test --local")
        self.model.submit("down --purge")
        self.model.submit("help test")
        self.model.submit("status")
        self.model.submit("logs --follow")
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.model.focus, "logs")
        content = "\n".join(self.model.console.lines)
        self.assertIn("already running", content)
        self.assertIn("Choose PR mode or local mode", content)
        self.assertIn("No environment exists", content)

    def test_exit_requires_explicit_cancellation_and_does_not_run_down(self):
        self.model.submit("up")
        job = self.model.job
        self.model.submit("exit")
        self.assertFalse(job.cancelled)
        self.model.submit("stay")
        self.assertFalse(self.model.exit_prompt)
        self.model.submit("exit")
        self.model.submit("cancel")
        self.assertTrue(job.cancelled)
        self.assertFalse(self.model.closed)
        job.returncode = 130
        self.model.tick()
        self.assertTrue(self.model.closed)
        self.assertTrue(job.closed)
        self.assertEqual(self.factory.call_count, 1)

    def test_installer_approval_and_password_input_are_separate_from_commands(self):
        self.model.submit("prereq install")
        job = self.model.job
        job.output.append("Install/configure these prerequisites? [Y/n] ")
        self.model.tick()
        self.assertEqual(self.model.prompt, "approval")
        self.model.submit("up")
        self.assertEqual(job.answers, [])
        self.model.submit("Y")
        self.assertEqual(job.answers, ["Y"])
        job.output.append("\n[sudo] password for tester: ")
        self.model.tick()
        self.assertEqual(self.model.prompt, "password")
        self.model.input, self.model.cursor = "host-password", len("host-password")
        screen = Screen(32, 120)
        draw(screen, self.model, FAKE_CURSES)
        self.assertNotIn("host-password", screen.text())
        self.assertIn("*************", screen.text())
        self.model.submit("host-password")
        self.assertEqual(job.answers[-1], "host-password")
        self.assertNotIn("host-password", str(self.model.history) + str(self.model.console.lines) + str(self.model.logs.lines))

    def test_exit_finishes_if_active_command_completes_during_confirmation(self):
        self.model.submit("up")
        self.model.submit("exit")
        self.model.job.returncode = 0
        self.model.tick()
        self.assertTrue(self.model.closed)

    def test_completion_history_and_finished_command_preserve_a_draft(self):
        self.model.input, self.model.cursor = "   ", 3
        self.model.complete()
        self.assertIn("up", self.model.notice)
        for partial, expected in (("cre", "credentials "), ("test --loc", "test --local "),
                                  ("prereq inst", "prereq install "), ("prereq install --dry", "prereq install --dry-run ")):
            self.model.input, self.model.cursor = partial, len(partial)
            self.model.complete()
            self.assertEqual(self.model.input, expected)
        self.model.submit("up")
        self.model.input = "test --local"
        self.model.cursor = len(self.model.input)
        self.model.recall(-1)
        self.assertEqual(self.model.input, "up")
        self.model.recall(1)
        self.assertEqual(self.model.input, "test --local")
        self.model.job.returncode = 0
        self.model.tick()
        self.assertEqual(self.model.input, "test --local")

    def test_cancelling_password_prompt_discards_input_before_unmasking(self):
        self.model.submit("prereq install")
        self.model.prompt = "password"
        self.model.input, self.model.cursor = "host-password", len("host-password")
        self.model.cancel()
        screen = Screen(32, 120)
        draw(screen, self.model, FAKE_CURSES)
        self.assertEqual(self.model.input, "")
        self.assertIsNone(self.model.prompt)
        self.assertNotIn("host-password", screen.text())

    def test_paused_log_stays_at_its_position_and_follow_resumes(self):
        self.model.logs.append("".join(f"line {i}\n" for i in range(100)))
        self.model.focus = "logs"
        self.model.scroll(-10)
        before = self.model.logs.visible(10, self.model.log_anchor)
        self.model.logs.append("new output\n")
        self.assertEqual(self.model.logs.visible(10, self.model.log_anchor), before)
        self.model.submit("follow")
        self.assertIsNone(self.model.log_anchor)
        self.assertIn("new output", self.model.logs.visible(10))

    def test_layout_split_narrow_resize_and_password_summary(self):
        self.model.console.append("\nResult: ready\nUsername: admin\nPassword: lab-password\n")
        self.model.logs.append("Username: admin\nPassword: lab-password\n")
        for height, width, focus in ((32, 120, "console"), (24, 70, "console"), (24, 70, "logs"), (14, 44, "console"), (8, 35, "console")):
            with self.subTest(height=height, width=width, focus=focus):
                self.model.focus = focus
                screen = Screen(height, width)
                draw(screen, self.model, FAKE_CURSES)
                if width >= 70:
                    self.assertIn("Password: lab-password", screen.text())
                if width >= 100:
                    self.assertIn("Commands", screen.text())
                    self.assertIn("Live logs", screen.text())


class TerminalTests(unittest.TestCase):
    def test_text_is_bounded_and_removes_split_escape_sequences(self):
        buffer = TextBuffer(limit=3, line_limit=12)
        buffer.append("\x1b[3")
        buffer.append("1mred\x1b[0m\n\x1b]52;c;clipboard")
        buffer.append("\aokay\r\nprogress\roverwrite\n" + "x" * 100)
        self.assertEqual(list(buffer.lines), ["red", "okay", "overwrite"])
        self.assertEqual(len(buffer.partial), 12)
        buffer.append("\nlast\n")
        self.assertEqual(len(buffer.lines), 3)
        self.assertNotIn("\x1b", str(buffer.lines))
        self.assertEqual(cell_width("A界B"), 4)
        self.assertEqual(cell_slice("A界B", 0, 2), "A")

    def test_log_tail_drains_rotation_and_handles_utf8_split_and_new_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.log"
            path.write_text("initial\n")
            tail = LogTail()
            self.addCleanup(tail.close)
            tail.select(path)
            self.assertEqual(tail.poll(), "initial\n")
            with path.open("ab") as handle:
                handle.write("界".encode()[:1])
            self.assertEqual(tail.poll(), "")
            with path.open("ab") as handle:
                handle.write("界".encode()[1:] + b"\n")
            self.assertEqual(tail.poll(), "界\n")
            backup = path.with_suffix(".old")
            path.rename(backup)
            with backup.open("ab") as handle:
                handle.write(b"late\n")
            path.write_text("new\n")
            self.assertEqual(tail.poll(), "late\n")
            self.assertEqual(tail.poll(), "new\n")
            tail.select(None)
            self.assertEqual(tail.poll(), "")

    def test_real_cli_process_uses_selected_state_directory_and_local_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for arguments, expected, code in ((["status"], "No environment exists", 0),
                                               (["test", "--local"], "Git working directory", 1)):
                job = CommandProcess(arguments, root, root / "isolated-state")
                output = ""
                try:
                    deadline = time.monotonic() + 5
                    while not job.finished and time.monotonic() < deadline:
                        output += job.poll()
                        time.sleep(0.01)
                    self.assertTrue(job.finished, output)
                    self.assertEqual(job.returncode, code)
                    self.assertIn(expected, output)
                finally:
                    job.close()

    def test_real_follow_process_cancels_without_deleting_saved_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "awx-lab-test"
            path.mkdir()
            (root / "current.json").write_text(json.dumps({"id": path.name}))
            (path / "state.json").write_text(json.dumps({"id": path.name, "runtime": "podman"}))
            (path / "run.log").write_text("retained log\n")
            job = CommandProcess(["logs", "--follow"], root, root)
            try:
                deadline = time.monotonic() + 5
                output = ""
                while "retained log" not in output and time.monotonic() < deadline:
                    output += job.poll()
                    time.sleep(0.01)
                self.assertIn("retained log", output)
                job.cancel()
                while not job.finished and time.monotonic() < deadline:
                    output += job.poll()
                    time.sleep(0.01)
                self.assertTrue(job.finished)
                self.assertEqual(job.returncode, 130)
                self.assertEqual((path / "run.log").read_text(), "retained log\n")
                self.assertTrue((path / "state.json").exists())
            finally:
                job.close()

    def test_installer_child_has_a_terminal_and_does_not_echo_passwords(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Patch installation only in this isolated child; never install host packages.
            (root / "sitecustomize.py").write_text('''
import getpass
import awx_lab.prereq as prereq
def fake_install(args, state_root):
    with open('/dev/tty') as terminal:
        assert terminal.isatty()
    if not prereq.confirm():
        return 0
    password = getpass.getpass('Password: ')
    print('Accepted password length: ' + str(len(password)), flush=True)
    return 0
prereq.install_prerequisites = fake_install
''')
            with patch.dict(os.environ, {"PYTHONPATH": str(root)}):
                job = CommandProcess(["prereq", "install"], root, root / "state")
            output = ""
            try:
                def until(value):
                    nonlocal output
                    deadline = time.monotonic() + 5
                    while value not in output and time.monotonic() < deadline and not job.finished:
                        output += job.poll()
                        time.sleep(0.01)
                    self.assertIn(value, output)
                until("[Y/n]")
                job.answer("Y")
                until("Password:")
                job.answer("fixture-password")
                until("Accepted password length: 16")
                deadline = time.monotonic() + 5
                while not job.finished and time.monotonic() < deadline:
                    output += job.poll()
                    time.sleep(0.01)
                self.assertEqual(job.returncode, 0)
                self.assertNotIn("fixture-password", output)
            finally:
                job.close()

    def test_real_fullscreen_terminal_accepts_commands_resizes_and_restores_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, TERM="xterm-256color", PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            pid, fd = pty.fork()
            if pid == 0:
                os.chdir(root)
                os.execve(sys.executable, [sys.executable, "-m", "awx_lab", "--state-dir", str(root / "state")], env)
            os.set_blocking(fd, False)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 120, 0, 0))
            transcript = bytearray()

            def until(value):
                deadline = time.monotonic() + 5
                while value not in transcript and time.monotonic() < deadline:
                    try:
                        transcript.extend(os.read(fd, 65536))
                    except BlockingIOError:
                        pass
                    time.sleep(0.02)
                self.assertIn(value, transcript)

            reaped = False
            try:
                until(b"Live logs")
                self.assertFalse(root.joinpath("state").exists())
                os.write(fd, b"status\n")
                until(b"No environment exists")
                os.write(fd, b"test --local\n")
                until(b"test failed (exit 1)")
                transcript.clear()
                fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 70, 0, 0))
                os.kill(pid, signal.SIGWINCH)
                os.write(fd, b"\t")
                until(b"[Live logs")
                os.write(fd, b"\x11")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        transcript.extend(os.read(fd, 65536))
                    except OSError:
                        pass
                    exited, status = os.waitpid(pid, os.WNOHANG)
                    if exited:
                        reaped = True
                        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                        break
                    time.sleep(0.02)
                self.assertTrue(reaped)
                self.assertTrue(termios.tcgetattr(fd)[3] & termios.ECHO)
                self.assertFalse(root.joinpath("state").exists())
            finally:
                if not reaped:
                    # A failed assertion must not hang the suite on a full PTY.
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                os.close(fd)
