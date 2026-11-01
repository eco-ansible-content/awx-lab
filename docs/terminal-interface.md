# Interactive terminal

Run `awx-lab` without a subcommand in an interactive terminal. `./awx-lab` also works from the repository. To select another state directory, run `awx-lab --state-dir /path/to/state`. Opening the interface reads saved state and logs; it does not create a lab or install anything. Without an attached terminal, bare `awx-lab` prints help.

The interface uses Python's optional standard-library `curses` module and Unix pseudo-terminals, with no third-party Python runtime dependencies. The supported targets remain macOS ARM64 and Linux x86_64. A terminal with cursor control and a Python build with curses are required; direct commands remain available if curses is absent. Colors use the terminal's default background with a cyan accent. The approved wordmark reads AWX LAB with the tagline BUILD · TEST · REPEAT.

## Layout and commands

At 100 columns or wider, the command console is on the left and live logs are on the right. Narrower terminals use tabs; Tab switches panes. Below 44 columns or 14 rows, a resize notice replaces the layout. The header shows the saved environment status/runtime, active command and elapsed time, and launch directory/Git branch. Saved environment state is not a live health check; the command can be building an update while an earlier AWX image remains available.

Use the usual subcommands without the `awx-lab` prefix:

```text
up
test https://github.com/ansible/awx/pull/123
test https://github.com/ansible/awx/pull/123 --plugins-pr https://github.com/ansible/awx-plugins/pull/456
test --local
test --local my-feature
credentials
down
down --purge
```

Replace PR numbers with your open PRs. Pasting the full `awx-lab …` command also works. The same validation, dependencies, image builds, tests, exit codes and storage policies apply as in direct mode. Only one command runs at a time. During active work, `help`, `status`, log navigation and `cancel` remain available; a second deployment/test is rejected rather than queued.

`test --local` always uses the directory from which the interface was launched. The directory and branch are shown in the header; `help` also prints the full path. Start the interface from your AWX or plugin checkout, using the installed command or `/path/to/awx-lab/awx-lab`. Changing directories, shell pipelines, `git` and arbitrary shell commands are unsupported. Exit and relaunch to change source directories.

## Keyboard and built-in commands

| Control | Action |
| --- | --- |
| Tab | Switch console/log focus (also changes the visible narrow-screen tab) |
| F2 | Complete a command or option; list matches if ambiguous |
| Up/Down in console | Previous/next command; preserve a draft when returning to the end |
| Left/Right, Home/End in input | Move the insertion cursor |
| Backspace/Delete | Edit the command |
| Ctrl+U | Clear input |
| PgUp/PgDn | Scroll the focused pane |
| Up/Down in logs | Scroll by one line and pause following |
| Left/Right in logs | Pan long lines horizontally |
| End in logs, or `follow` | Resume automatic following |
| `logs` / `logs --follow` | Show the lab's saved detailed log |
| `help` / `/help` | List commands, keyboard controls and the full source path |
| `help test` / `help prereq install` | Show existing CLI help and examples |
| `clear` | Clear the displayed console transcript; saved logs are retained |
| Ctrl+C / `cancel` | Interrupt the active command; otherwise clear idle input |
| `exit` / `quit` / Ctrl+Q / Ctrl+D | Leave the view; active commands require cancel/stay |
| Esc | Return to console input and dismiss an exit prompt |

Command history stays in memory for this interface session. Console output wraps; log output can be panned horizontally. Scrollback and line lengths are bounded to keep the interface responsive during large builds; complete output remains subject to the existing disk-log retention policy.

## Logs, credentials and prompts

The left pane shows command output, normally the concise progress and successful login summary. The right pane follows `run.log`, including rotation and selection of a new lab. When no command is running, the latest output remains visible. `doctor`, `plan` and prerequisite installation display their command output in the right pane because they do not produce a lab log; `logs` switches back to the saved lab log.

The generated AWX username/password appear after successful checks in both panes, as they do in direct mode and saved logs. `--error-log` suppresses successful output from the command in the left pane; the interface still shows its command status and the right pane remains unfiltered.

For `prereq install`, the proposed actions and Y/n prompt appear on the left. Answer in the same input field. Recognized password/passphrase prompts hide input and exclude it from command history; cancelling discards that input. Enter/Return prompts are also supported. If a package manager uses an unrecognized interactive prompt, cancel and run `awx-lab prereq install` directly in your shell. The existing installer approval is still required.

## Cancellation and exit

Ctrl+C sends an interrupt to the active CLI process, allowing its normal cleanup, diagnostics and state saving. The UI remains open, and cancelling does not run `down` or purge the environment. A partial rollout may need `up` or another `test` to recover, just as in direct mode.

`exit` leaves an idle interface immediately and keeps AWX running. During active work it offers `cancel` (interrupt, wait for the CLI to finish, then exit) or `stay`. If the command finishes while this choice is open, the interface exits normally. Closing a terminal abruptly can interrupt setup and leave a partial environment; it does not provide background job continuation. Reopen and inspect saved state/logs before retrying.

## Implementation and verification

The UI runs on a single curses event loop. Each operation executes the existing CLI in a separate pseudo-terminal with the launch directory and selected state directory. No shell evaluates the input. This keeps the screen responsive and preserves installer/getpass behavior. A separate bounded reader follows the log; ANSI control sequences from build output are stripped before display.

Unit tests cover command routing, invalid input, source selection, busy-state behavior, prompts, cancellation/exit races, history/completion, log rotation and paused scroll. Actual pseudo-terminal tests cover launch, commands, resize, terminal restoration, cancellation and password input without installing packages or creating containers. Preview images use illustrative output rendered by the same drawing function. Linux live terminal acceptance and full deployments through this view remain recorded separately in the [validation status](validation.md).
