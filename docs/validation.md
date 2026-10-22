# Validation status

Initial implementation: 22 October 2026.

## Verified locally

- Python 3.13.4 on macOS ARM64.
- CLI parsing, dependency reporting, dry planning and report generation.
- Installable wheel built from the source distribution, including the helper Containerfile and a shared package/CLI version.
- Fresh-environment installation: `awx-lab` resolves on PATH, runs outside the checkout, and discovers the same saved runs from different working directories. Uninstall removes the command and retains saved runs.
- Real local Git repositories: merge includes both PR and updated target, deterministic merge SHA, conflicts fail, moving PR heads fail.
- Plugin wheel injection preserves extras, places the wheel before dependency installation, and records a content hash.
- Simulated lifecycle: success, failed build/deployment/smoke checks, partial cluster creation, Ctrl-C, retained environments, cleanup failures.
- Per-run locking, path validation, process timeouts, live output and secret-output suppression.
- Registry manifests for `quay.io/ansible/awx-operator:2.19.1`, `quay.io/ansible/awx-ee:24.6.1`, and `quay.io/ansible/receptor:devel` include both linux/arm64 and linux/amd64. This verifies those published manifests, not the entire deployment.

## Required acceptance runs

| Platform | Runtime | Full AWX build | kind + Operator readiness | API + plugin verification | Ansible job |
| --- | --- | --- | --- | --- | --- |
| macOS ARM64 | Podman machine | Pending | Pending | Pending | Pending |
| Linux x86_64 | Podman | Pending | Pending | Pending | Pending |
| macOS ARM64 | Docker Desktop | Pending | Pending | Pending | Pending |
| Linux x86_64 | Docker | Pending | Pending | Pending | Pending |

The development host has Podman installed, but its VM is stopped and kind/kubectl are absent. No full deployment has been claimed. CI covers CLI tests, not this acceptance matrix.

Before rollout, select representative open AWX and plugin PRs and complete:

1. Run `doctor`, then `up` for a plugin PR. Confirm credentials, UI, expected plugin changes, and the recorded merge and wheel hash.
2. Run `test --suite job` for an AWX PR and verify successful Ansible execution, reports and cluster deletion.
3. Test related AWX/plugin PRs together and verify the configured Operator works with the chosen AWX revision.
4. Start two labs concurrently. Verify distinct URLs, databases and kubeconfigs; remove one and confirm the other remains available.
5. Cancel a build and a deployment; verify no transient helper container or test cluster remains. Test runtime failure and retry cleanup.
6. Record cold/warm build times, peak memory and disk consumption to replace planning estimates with measured sizing.

## Known boundaries

- Operator 2.19.1 is a published baseline, not a verified compatibility guarantee for every current AWX PR.
- Source revisions may change build layout or dependencies; adapters fail rather than silently skip the requested plugin.
- Rootless kind may require host kernel/cgroup configuration that `doctor` does not fully validate.
- macOS runtime VM storage must be sized separately from the workspace disk check.
- Private repositories, GitLab orchestration, a shared server, retained datasets, automatic PR triggers and comprehensive AWX regression suites are outside this first increment.
