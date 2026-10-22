# Project backlog

## Log output modes

- [x] Add log flags and make the default terminal output concise.

Status: implemented in 0.4.6 for `up`, `test` and `down`.

| Mode | Terminal output |
| --- | --- |
| No log flag | Clean output focused on the test and its results, without environment installation, build or deployment logs. |
| `--error-log` | Only errors, including failures during environment setup or testing. |
| `--full-log` | The entire log, including environment installation, build, deployment and test output, matching the current verbose output. |

Acceptance criteria:

- Default output hides routine environment preparation logs while showing test progress and results.
- Setup failures remain visible so a run cannot appear to succeed when testing never started.
- `--error-log` suppresses non-error output.
- `--full-log` preserves the detail currently shown in the terminal.
- The two flags are mutually exclusive.
- Output filtering does not change test execution or exit status.
- Keep saved logs unfiltered regardless of the terminal mode, subject to the existing rotation limits and sensitive-output suppression.
- Document the flags and verify successful runs, setup failures and test failures.

`awx-lab logs` and `logs --follow` continue to read the saved log without filtering. The prerequisite installer retains its explicit approval flow.
