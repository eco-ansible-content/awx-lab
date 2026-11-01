# Project backlog

## Log output modes

- [x] Add log flags and make the default terminal output concise.

Status: implemented in 0.4.6 for `up`, `test` and `down`.

| Mode | Terminal output |
| --- | --- |
| No log flag | Clean output focused on the test and its results, with concise setup stages and elapsed updates; raw command/build output stays hidden. |
| `--error-log` | Only errors, including failures during environment setup or testing. |
| `--full-log` | The entire log, including environment installation, build, deployment and test output, matching the current verbose output. |

Acceptance criteria:

- Default output hides raw environment preparation output while showing setup stages, elapsed time and test results (updated in 0.5.2).
- Setup failures remain visible so a run cannot appear to succeed when testing never started.
- `--error-log` suppresses non-error output.
- `--full-log` preserves the detail currently shown in the terminal.
- The two flags are mutually exclusive.
- Output filtering does not change test execution or exit status.
- Keep saved logs unfiltered regardless of the terminal mode, subject to the existing rotation limits and sensitive-output suppression.
- Document the flags and verify successful runs, setup failures and test failures.

`awx-lab logs` and `logs --follow` continue to read the saved log without filtering. The prerequisite installer retains its explicit approval flow.

## Feedback improvements (0.5.2)

- [x] Show setup stages, completion times and elapsed updates during long operations.
- [x] Print the verified AWX login after successful checks and save it in the log.
- [x] Put Quick start first and move the full reference to the user guide.
- [x] Document the repository launcher alongside pipx installation.
- [x] Clarify AWX-only, plugin-only, combined PR and single-checkout local modes.

## Interactive terminal (0.6.0)

- [x] Open the logo, command console and live log view with bare `awx-lab`.
- [x] Preserve direct commands and the repository launcher without extra Python runtime dependencies.
- [x] Add command completion/history, busy-state handling, installer input and cancellation/exit controls.
- [x] Follow rotating logs, pause/resume scrolling and adapt to narrow terminals.
- [x] Cover native pseudo-terminal behavior and document the launch-directory rule for local tests.
- [ ] Complete Linux terminal acceptance and full AWX runs through the interactive view.
