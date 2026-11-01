"""Interactive AWX Lab console. Curses is imported only for interactive use."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import locale
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time

from . import __version__
from .cli import current_path, load_state, main, parser
from .system import LabError
from .terminal import CommandProcess, LogTail, TextBuffer, cell_slice, cell_width


LOGO = ("▄▀█ █ █ █ ▀▄▀   █   ▄▀█ █▄▄", "█▀█ ▀▄▀▄▀ █ █   █▄▄ █▀█ █▄█")
COMMANDS = ("up", "test", "doctor", "prereq", "plan", "status", "logs", "credentials", "down")
BUILTINS = ("help", "cancel", "follow", "clear", "exit")
WELCOME = """Welcome to AWX Lab.
Type up to create or resume your environment.
Then test a PR or use test --local from your source checkout.

Examples:
  up
  test <PR_URL>
  test --local
  test <AWX_PR_URL> --plugins-pr <PLUGIN_PR_URL>
  credentials
  down

Type help for commands and keyboard controls.
Opening or leaving this view does not stop a running lab.
"""
HELP = """AWX LAB COMMANDS
  up                         Create or resume the lab
  test <PR_URL>               Build and check a PR
  test <AWX_PR> --plugins-pr <PLUGIN_PR>   Test both PRs
  test --local [BRANCH]       Test this checkout's saved edits
  doctor                     Check prerequisites
  prereq install             Install prerequisites after approval
  credentials                Print and log the admin login
  status                     Read saved state (not a live health check)
  down / down --purge         Stop / destroy the lab
  help <command>             Full command flags and examples
  logs / follow              Focus logs / resume following
  cancel                     Interrupt the active command
  clear                      Clear the console display
  exit                       Leave the interface; keep the lab

KEYBOARD
  Tab                        Switch between console and logs
  F2                         Complete command or option
  Up / Down                  Command history; scroll when in logs
  PgUp / PgDn                Scroll the focused pane (pauses following)
  End in logs / follow       Resume following
  Left / Right in logs       Pan long log lines horizontally
  Ctrl+C                     Cancel command, or clear idle input
  Ctrl+Q / Ctrl+D            Exit (asks before cancelling active work)
  Esc                        Return to input / dismiss exit prompt

This console accepts AWX Lab commands only; git/cd/pipes are unsupported.
Local tests use the launch directory shown above; restart here to change it.
Installer Y/n and password prompts accept input in the console.
Passwords entered for the installer are not echoed or kept in history.
"""


class Console:
    def __init__(self, state_dir, cwd=None, process_factory=CommandProcess):
        self.state_dir = state_dir
        self.cwd = Path.cwd() if cwd is None else cwd
        self.process_factory = process_factory
        self.console, self.logs = TextBuffer(), TextBuffer()
        self.console.append(WELCOME)
        self.tail = LogTail()
        self.job = None
        self.arguments = []
        self.state = {}
        self.branch = "checking Git branch"
        self.last_refresh = -10
        self.focus = "console"
        self.log_source = "lab"
        self.log_anchor = self.console_anchor = None
        self.log_horizontal = 0
        self.history = []
        self.history_index = 0
        self.history_draft = ""
        self.input = ""
        self.cursor = 0
        self.prompt = None
        self.exit_prompt = False
        self.exit_after_command = False
        self.closed = False
        self.notice = "F2 completes commands. Tab switches panes."
        self.last_result = "No command running"
        self.wrap_cache = None
        self.console_rows = []

    def say(self, value):
        self.console.append(value.rstrip() + "\n")
        self.console_anchor = None

    def refresh(self):
        if time.monotonic() - self.last_refresh < 2:
            return
        self.last_refresh = time.monotonic()
        try:
            path = current_path(self.state_dir)
            self.state = load_state(path) if path else {}
            selected = path / "run.log" if path else None
            if self.tail.select(selected) and selected and self.log_source == "lab":
                self.logs = TextBuffer()
                self.log_anchor = None
        except (LabError, OSError, ValueError) as error:
            self.state = {}
            self.notice = str(error)
        try:
            result = subprocess.run(["git", "-C", str(self.cwd), "symbolic-ref", "--short", "HEAD"],
                                    capture_output=True, text=True, timeout=1)
            self.branch = result.stdout.strip() if result.returncode == 0 else "detached HEAD or not a Git checkout"
        except (OSError, subprocess.TimeoutExpired):
            self.branch = "Git branch unavailable"

    def tick(self):
        self.refresh()
        if self.log_source == "lab":
            try:
                self.logs.append(self.tail.poll())
            except OSError as error:
                self.notice = f"Cannot read log: {error}"
        if not self.job:
            return
        output = self.job.poll()
        if output:
            self.console.append(output)
            if self.log_source == "command":
                self.logs.append(output)
            # Prompts need no newline. Inspect the sanitized, current line only.
            if self.arguments[:2] == ["prereq", "install"]:
                line = self.console.partial.lower().rstrip()
                if line.endswith("[y/n]"):
                    self.prompt = "approval"
                elif re.search(r"(?:password|passphrase)[^\n]*[:：]$", line):
                    self.prompt = "password"
                elif "press" in line and any(word in line for word in ("return", "enter")):
                    self.prompt = "enter"
                if self.prompt:
                    self.focus = "console"
                    self.notice = "Password input is hidden." if self.prompt == "password" else "Reply to the installer in the input below."
        if self.job.finished:
            code = self.job.returncode
            self.job.close()
            self.job = None
            if self.prompt:
                self.input, self.cursor = "", 0
            self.prompt = None
            result = "completed" if code == 0 else "interrupted" if code in (130, -signal.SIGINT) else "failed"
            self.last_result = f"{self.arguments[0]} {result} (exit {code})"
            self.say("\n" + self.last_result)
            self.notice = "Type a command, or help for options."
            self.last_refresh = -10
            if self.exit_after_command or self.exit_prompt:
                self.closed = True

    def cancel(self):
        if self.job:
            self.job.cancel()
            if self.prompt:
                self.input, self.cursor = "", 0
            self.prompt = None
            self.notice = "Cancelling; waiting for the CLI to save its state and diagnostics…"
        else:
            self.input, self.cursor = "", 0
            self.notice = "No command is running."

    def request_exit(self):
        if self.job:
            self.exit_prompt = True
            self.focus = "console"
            self.input, self.cursor = "", 0
            self.notice = "Active command: type cancel to interrupt and exit, or stay."
        else:
            self.closed = True

    def submit(self, value):
        self.input, self.cursor = "", 0
        if self.exit_prompt:
            if value.strip().lower() == "cancel":
                self.exit_prompt = False
                self.exit_after_command = True
                self.cancel()
            elif value.strip().lower() == "stay":
                self.exit_prompt = False
                self.notice = "Continuing the current command."
            else:
                self.notice = "Type cancel or stay; Esc keeps the command running."
            return
        if self.prompt:
            if self.prompt == "approval" and value.lower().strip() not in ("", "y", "yes", "n", "no"):
                self.notice = "Reply Y or n."
                return
            self.job.answer(value)
            self.say("\n[password submitted]" if self.prompt == "password" else "\n" + value)
            self.prompt = None
            self.notice = "Installer continuing…"
            return
        if not value.strip():
            return
        self.history.append(value)
        self.history = self.history[-100:]
        self.history_index = len(self.history)
        self.history_draft = ""
        self.say("> " + value)
        try:
            words = shlex.split(value)
        except ValueError as error:
            self.say(f"Invalid command: {error}")
            return
        if words[0] in ("awx-lab", "./awx-lab"):
            words = words[1:]
        if not words:
            self.say("You are already in AWX Lab. Type help for commands.")
            return
        name = words[0].removeprefix("/")
        if name in ("exit", "quit") and len(words) == 1:
            self.request_exit()
            return
        if name == "cancel" and len(words) == 1:
            self.cancel()
            return
        if name == "clear" and len(words) == 1:
            self.console = TextBuffer()
            self.console_anchor = None
            return
        if name == "follow" or words in (["logs"], ["logs", "--follow"]):
            if words[0] == "logs" and self.log_source != "lab":
                self.log_source = "lab"
                self.tail.close()
                self.logs = TextBuffer()
            self.focus, self.log_anchor = "logs", None
            self.notice = "Following new output. PgUp pauses; End resumes."
            return
        if name == "help":
            if len(words) == 1:
                self.say(f"Source directory: {self.cwd}\nBranch: {self.branch}\n\n" + HELP)
                return
            words = [*words[1:], "--help"]
        if words[0] not in COMMANDS and words[0] not in ("--help", "--version"):
            self.say(f"Unknown AWX Lab command: {words[0]}. Type help. Shell commands are not supported.")
            return
        if any(word in ("|", "||", "&&", ";", ">", ">>", "<") for word in words):
            self.say("Shell operators are unsupported. Run one AWX Lab command at a time.")
            return
        output = io.StringIO()
        try:
            with redirect_stdout(output), redirect_stderr(output):
                args = parser().parse_args(["--state-dir", str(self.state_dir), *words])
        except SystemExit:
            self.say(output.getvalue())
            return
        if args.command == "status":
            with redirect_stdout(output), redirect_stderr(output):
                main(["--state-dir", str(self.state_dir), *words])
            self.say(output.getvalue())
            return
        if self.job:
            self.say("A command is already running. Use status, help, logs or cancel before starting another.")
            return
        try:
            self.job = self.process_factory(words, self.cwd, self.state_dir)
        except OSError as error:
            self.say(f"Cannot start command: {error}")
            return
        self.arguments = words
        self.console_anchor = None
        self.log_anchor = None
        self.log_source = "command" if args.command in ("doctor", "plan", "prereq", "logs") else "lab"
        if self.log_source == "command":
            self.logs = TextBuffer()
        else:
            # Reopen if the preceding command was displaying its own output.
            self.tail.close()
            self.logs = TextBuffer()
        self.notice = "Command running. Ctrl+C cancels; the environment is retained."

    def recall(self, delta):
        if not self.history or self.prompt or self.exit_prompt:
            return
        if self.history_index == len(self.history):
            self.history_draft = self.input
        self.history_index = max(0, min(len(self.history), self.history_index + delta))
        self.input = self.history[self.history_index] if self.history_index < len(self.history) else self.history_draft
        self.cursor = len(self.input)

    def complete(self):
        if self.prompt or self.exit_prompt:
            return
        before = self.input[:self.cursor]
        parts = before.split()
        prefix = "" if before.endswith(" ") else parts[-1] if parts else ""
        if not parts or len(parts) == 1 and not before.endswith(" "):
            choices = [*COMMANDS, *BUILTINS]
        else:
            subcommands = parser()._subparsers._group_actions[0].choices
            command = subcommands.get(parts[0])
            if parts[0] == "prereq" and (len(parts) == 1 or len(parts) == 2 and not before.endswith(" ")):
                choices = ["install"]
            else:
                if parts[:2] == ["prereq", "install"]:
                    command = command._subparsers._group_actions[0].choices["install"]
                choices = [flag for action in command._actions for flag in action.option_strings] if command else []
        matches = sorted(item for item in choices if item.startswith(prefix))
        if not matches:
            self.notice = "No completion. Type help <command> for flags."
            return
        common = os.path.commonprefix(matches)
        suffix = " " if len(matches) == 1 else ""
        self.input = before[:len(before) - len(prefix)] + common + suffix + self.input[self.cursor:]
        self.cursor = len(before) - len(prefix) + len(common) + len(suffix)
        self.notice = "  ".join(matches)

    def scroll(self, delta):
        buffer = self.logs if self.focus == "logs" else self.console
        attr = "log_anchor" if self.focus == "logs" else "console_anchor"
        current = getattr(self, attr)
        end = buffer.total + bool(buffer.partial) if self.focus == "logs" else len(self.console_rows)
        first = buffer.total - len(buffer.lines) if self.focus == "logs" else 0
        anchor = max(first + 1, min(end, (end if current is None else current) + delta))
        setattr(self, attr, None if delta > 0 and anchor == end else anchor)

    def wrapped_console(self, width, height):
        key = (id(self.console), self.console.revision, width)
        if self.wrap_cache != key:
            rows = []
            for line in [*self.console.lines, self.console.partial]:
                row, cells = "", 0
                for char in line:
                    size = cell_width(char)
                    if cells + size > width:
                        boundary = row.rfind(" ")
                        if boundary > 0:
                            rows.append(row[:boundary])
                            row = row[boundary + 1:]
                            cells = cell_width(row)
                        else:
                            rows.append(row)
                            row, cells = "", 0
                    row += char
                    cells += size
                rows.append(row)
            dropped = max(0, len(rows) - 6000)
            self.console_rows = rows[dropped:]
            self.wrap_cache = key
            if self.console_anchor is not None:
                self.console_anchor = max(1, min(len(self.console_rows), self.console_anchor))
        end = len(self.console_rows) if self.console_anchor is None else self.console_anchor
        return self.console_rows[max(0, end - height):end]

    def close(self):
        self.tail.close()
        if self.job:
            self.job.close()
            self.job = None


def draw(screen, model, curses):
    """One main-thread render pass; supports both split and narrow tab layouts."""
    screen.erase()
    height, width = screen.getmaxyx()
    accent = curses.color_pair(1)
    muted = curses.A_DIM

    def put(y, x, text, style=0, limit=None, offset=0):
        if 0 <= y < height and 0 <= x < width:
            safe = TextBuffer(line_limit=8192)
            safe.append(text)
            text = safe.partial if not safe.lines else safe.lines[0]
            try:
                screen.addstr(y, x, cell_slice(text, offset, min(width - x - 1, limit if limit is not None else width)), style)
            except curses.error:
                pass  # Resizing can invalidate a coordinate between getmaxyx/addstr.

    if height < 14 or width < 44:
        put(0, 0, "AWX LAB", accent | curses.A_BOLD)
        put(2, 0, "Resize to at least 44 columns x 14 rows.")
        put(3, 0, "Ctrl+C cancels work; Ctrl+Q exits.")
        put(5, 0, model.notice)
        screen.refresh()
        return
    tall = height >= 24
    if tall:
        for row, line in enumerate(LOGO):
            put(row, 2, line[:13], curses.A_BOLD)
            put(row, 18, line[16:], accent | curses.A_BOLD)
        put(2, 4, "BUILD · TEST · REPEAT", muted)
        info = 4
    else:
        put(0, 1, f"AWX LAB  ·  {__version__}  ·  BUILD · TEST · REPEAT", accent | curses.A_BOLD)
        info = 1
    if tall:
        put(0, max(34, width - 10), f"v{__version__}", muted)
    saved = model.state.get("status", "not created")
    runtime = model.state.get("runtime", "not selected")
    put(info, 1, f"Environment (saved): {saved}  |  Runtime: {runtime}")
    operation = (f"{' '.join(model.arguments)} · {int(time.monotonic() - model.job.started)}s elapsed"
                 if model.job else model.last_result)
    put(info + 1, 1, "Operation: " + operation, accent if model.job else muted)
    put(info + 2, 1, f"Source: {model.cwd} [{model.branch}]", muted)
    top = info + 4
    split = width >= 100
    divider = int(width * 0.45) if split else width
    console_width = divider - 3
    log_title = "Live logs · " + ("Following" if model.log_anchor is None else "PAUSED · End resumes")
    if split:
        put(top - 1, 1, "Commands" + (" [active]" if model.focus == "console" else ""), accent | curses.A_BOLD)
        put(top - 1, divider + 2, log_title, accent if model.focus == "logs" else muted)
        for y in range(top - 1, height - 2):
            put(y, divider, "│", muted)
    else:
        put(top - 1, 1, "[Commands]   Live logs" if model.focus == "console" else "Commands   [" + log_title + "]", accent)
    if split or model.focus == "console":
        for row, line in enumerate(model.wrapped_console(console_width, height - top - 4)):
            style = accent if line.startswith((">", "PASS", "Username:", "Password:", "AWX:")) else 0
            put(top + row, 1, line, style, console_width)
    if split or model.focus == "logs":
        x = divider + 2 if split else 1
        log_width = width - x - 1
        lines = model.logs.visible(height - top - (2 if split else 4), model.log_anchor)
        if not lines:
            put(top + 1, x, "Logs will appear when you run a command.", muted, log_width)
        for row, line in enumerate(lines):
            put(top + row, x, line, 0, log_width, model.log_horizontal)
    put(height - 4, 1, "─" * console_width, muted)
    prefix = "Exit [cancel/stay] > " if model.exit_prompt else "Password > " if model.prompt == "password" else "Reply > " if model.prompt else "awx-lab > "
    displayed = "*" * len(model.input) if model.prompt == "password" else model.input
    input_width = max(1, console_width - len(prefix))
    caret = cell_width(displayed[:model.cursor])
    offset = max(0, caret - input_width + 1)
    put(height - 3, 1, prefix, accent)
    put(height - 3, 1 + len(prefix), displayed, 0, input_width, offset)
    put(height - 2, 1, model.notice, muted)
    footer = "Tab panes · F2 complete · PgUp/PgDn scroll · Ctrl+C cancel · Ctrl+Q exit"
    put(height - 1, 1, footer if width >= 80 else "Tab panes · F2 complete · Ctrl+C cancel · Ctrl+Q exit", muted)
    try:
        curses.curs_set(1 if model.focus == "console" else 0)
        if model.focus == "console":
            screen.move(height - 3, min(divider - 2, 1 + len(prefix) + caret - offset))
    except curses.error:
        pass
    screen.refresh()


def interact(screen, model, curses):
    curses.raw()
    screen.keypad(True)
    screen.timeout(100)
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
        except curses.error:
            curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    while not model.closed:
        model.tick()
        if model.closed:
            break
        draw(screen, model, curses)
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        if key in ("\x11", "\x04"):
            model.request_exit()
        elif key == "\x03":
            model.cancel()
        elif key == "\x1b":
            model.exit_prompt = False
            model.focus = "console"
            model.notice = "Type a command, or help for options."
        elif key == "\t":
            model.focus = "logs" if model.focus == "console" else "console"
        elif key == curses.KEY_F2:
            model.complete()
        elif key == curses.KEY_PPAGE:
            model.scroll(-max(1, screen.getmaxyx()[0] // 2))
        elif key == curses.KEY_NPAGE:
            model.scroll(max(1, screen.getmaxyx()[0] // 2))
        elif key == curses.KEY_RESIZE:
            if model.job:
                model.job.resize(*screen.getmaxyx())
        elif model.focus == "logs":
            if key == curses.KEY_UP:
                model.scroll(-1)
            elif key == curses.KEY_DOWN:
                model.scroll(1)
            elif key == curses.KEY_END:
                model.log_anchor = None
            elif key == curses.KEY_LEFT:
                model.log_horizontal = max(0, model.log_horizontal - 10)
            elif key == curses.KEY_RIGHT:
                model.log_horizontal += 10
        elif key in ("\n", "\r", curses.KEY_ENTER):
            model.submit(model.input)
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if model.cursor:
                model.input = model.input[:model.cursor - 1] + model.input[model.cursor:]
                model.cursor -= 1
        elif key == curses.KEY_DC:
            model.input = model.input[:model.cursor] + model.input[model.cursor + 1:]
        elif key == curses.KEY_LEFT:
            model.cursor = max(0, model.cursor - 1)
        elif key == curses.KEY_RIGHT:
            model.cursor = min(len(model.input), model.cursor + 1)
        elif key in (curses.KEY_HOME, "\x01"):
            model.cursor = 0
        elif key in (curses.KEY_END, "\x05"):
            model.cursor = len(model.input)
        elif key == "\x15":
            model.input, model.cursor = "", 0
        elif key == curses.KEY_UP:
            model.recall(-1)
        elif key == curses.KEY_DOWN:
            model.recall(1)
        elif isinstance(key, str) and key.isprintable() and len(model.input) < 8192:
            model.input = model.input[:model.cursor] + key + model.input[model.cursor:]
            model.cursor += len(key)


def run(state_dir):
    try:
        import curses
    except ImportError:
        raise LabError("Interactive mode needs Python's curses support. Use direct commands such as 'awx-lab up' or install your distribution's Python curses package.") from None
    if os.environ.get("TERM", "dumb") in ("", "dumb", "unknown"):
        raise LabError("Interactive mode needs a terminal with cursor support. Use 'awx-lab --help' for direct commands.")
    locale.setlocale(locale.LC_ALL, "")
    model = Console(state_dir)
    previous = {}
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, interrupted)
    try:
        curses.wrapper(interact, model, curses)
    except curses.error as error:
        raise LabError(f"Cannot open terminal view: {error}. Direct commands remain available.") from error
    finally:
        model.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0
