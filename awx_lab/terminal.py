"""Bounded terminal text, rotating-log following and isolated CLI processes."""

from __future__ import annotations

import codecs
from collections import deque
import errno
import fcntl
import os
from pathlib import Path
import pty
import signal
import struct
import sys
import termios
import time
import unicodedata


READ_LIMIT = 64 * 1024


def cell_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def cell_slice(text, start, width):
    """Clip by terminal cells, never split a wide glyph at a pane boundary."""
    result, position = [], 0
    for char in text:
        size = cell_width(char)
        if position >= start and position + size <= start + width:
            result.append(char)
        position += size
        if position >= start + width:
            break
    return "".join(result)


class TextBuffer:
    """Store plain text only; escape sequences from builds never control the UI."""
    def __init__(self, limit=2000, line_limit=4096):
        self.lines = deque(maxlen=limit)
        self.line_limit = line_limit
        self.partial = ""
        self.total = 0
        self.escape = ""
        self.carriage = False
        self.revision = 0

    def append(self, text):
        if text:
            self.revision += 1
        for char in text:
            if self.escape:
                if self.escape == "esc":
                    self.escape = "csi" if char == "[" else "string" if char in "]P_^" else ""
                elif self.escape == "csi":
                    if "@" <= char <= "~":
                        self.escape = ""
                elif self.escape == "string":
                    if char == "\a":
                        self.escape = ""
                    elif char == "\x1b":
                        self.escape = "string-end"
                elif self.escape == "string-end":
                    self.escape = "" if char == "\\" else "string"
                continue
            if char == "\x1b":
                self.escape = "esc"
            elif char == "\n":
                self.lines.append(self.partial)
                self.total += 1
                self.partial, self.carriage = "", False
            elif char == "\r":
                self.carriage = True
            elif char == "\b":
                self.partial = self.partial[:-1]
            elif char == "\t" or char.isprintable():
                if self.carriage:
                    self.partial, self.carriage = "", False
                self.partial += " " * (4 - len(self.partial) % 4) if char == "\t" else char
                if len(self.partial) > self.line_limit:
                    self.partial = self.partial[:self.line_limit - 1] + "…"

    def visible(self, height, anchor=None):
        first = self.total - len(self.lines)
        end = self.total + bool(self.partial) if anchor is None else min(anchor, self.total + bool(self.partial))
        end = max(first, end)
        lines = list(self.lines) + ([self.partial] if self.partial else [])
        return lines[max(0, end - first - height):end - first]


class LogTail:
    def __init__(self):
        self.path = None
        self.handle = None
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None

    def select(self, path):
        if path == self.path:
            return False
        self.close()
        self.path = path
        self.decoder.reset()
        return True

    def poll(self):
        if self.path is None:
            return ""
        try:
            if self.path.is_symlink():
                return ""
            if self.handle is None:
                self.handle = self.path.open("rb")
                size = os.fstat(self.handle.fileno()).st_size
                if size > READ_LIMIT:
                    self.handle.seek(size - READ_LIMIT)
                    self.handle.readline(READ_LIMIT)  # Discard the incomplete first line.
            data = self.handle.read(READ_LIMIT)
            if data:
                return self.decoder.decode(data)
            current, opened = self.path.stat(), os.fstat(self.handle.fileno())
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino) or current.st_size < self.handle.tell():
                self.close()
                self.decoder.reset()
                # Drain the old inode before following the replacement from its start.
                self.handle = self.path.open("rb")
                return self.decoder.decode(self.handle.read(READ_LIMIT))
        except FileNotFoundError:
            pass  # A new run or rotation may not have created the file yet.
        return ""


class CommandProcess:
    """A child CLI with its own terminal, so installer/sudo prompts still work."""
    def __init__(self, arguments, cwd, state_dir):
        package_root = str(Path(__file__).resolve().parent.parent)
        # -I keeps the source checkout (the child's cwd) off sys.path. Insert
        # only the package that launched this console, including repo launches.
        entrypoint = f"import sys; sys.path.insert(0, {package_root!r}); from awx_lab.cli import main; raise SystemExit(main())"
        command = [sys.executable, "-I", "-u", "-c", entrypoint, "--state-dir", str(state_dir), *arguments]
        env = dict(os.environ, TERM="dumb", NO_COLOR="1", PYTHONUNBUFFERED="1")
        env.pop("PYTHONPATH", None)
        self.started = time.monotonic()
        self.returncode = None
        self.eof = False
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.chdir(cwd)
                os.execve(sys.executable, command, env)
            except BaseException as error:
                os.write(2, f"Cannot start AWX Lab: {error}\n".encode())
                os._exit(127)
        os.set_blocking(self.fd, False)
        attributes = termios.tcgetattr(self.fd)
        attributes[3] &= ~termios.ECHO
        termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
        self.resize(24, 100)

    def resize(self, rows, columns):
        if not self.eof:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(1, rows), max(40, columns), 0, 0))

    def poll(self):
        data = b""
        if not self.eof:
            try:
                data = os.read(self.fd, READ_LIMIT)
                if not data:
                    self.eof = True
            except BlockingIOError:
                pass
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                self.eof = True
        if self.returncode is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.decoder.decode(data, final=self.eof)

    @property
    def finished(self):
        return self.returncode is not None and self.eof

    def answer(self, value):
        os.write(self.fd, (value + "\n").encode())

    def cancel(self):
        if self.returncode is None:
            try:
                os.killpg(self.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

    def close(self):
        # Normal exits reach here after the command has finished. On terminal
        # failure, give the CLI time to stop its active subprocess and save state.
        if self.returncode is None:
            self.cancel()
            deadline = time.monotonic() + 5
            while self.returncode is None and time.monotonic() < deadline:
                self.poll()
                time.sleep(0.05)
            if self.returncode is None:
                self.cancel()
                deadline = time.monotonic() + 2
                while self.returncode is None and time.monotonic() < deadline:
                    self.poll()
                    time.sleep(0.05)
            if self.returncode is None:
                try:
                    os.killpg(self.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                os.waitpid(self.pid, 0)
        os.close(self.fd)
