# Validation status

Initial implementation: 22 October 2026.

## Verified locally

- Version 0.4.6 logging modes: 132 automated tests passed. CLI scenarios exercise clean/error/full output with real subprocess output, successful checks, setup/test failures and interruption. Tests verify unchanged execution/exit status, unfiltered saved logs, no persisted log preference, mutual exclusion, silent successful stop/purge, and suppression of sensitive failed-command output. Plugin and job PASS lines are emitted only after those checks succeed.
- The installed 0.4.6 command passed live `up` checks in all three modes against the retained macOS ARM/Podman lab. Clean mode showed API PASS lines and a ready summary without setup commands; error mode produced no output; full mode included deployment-readiness detail. All exited zero and preserved the AWX image/source selection. The wheel passed isolated installation checks, including flag availability.

- Version 0.4.5 retention: 122 automated tests passed. Repeated successful updates retain two successful AWX/UI build pairs and ten report archives; unchanged runs preserve the previous success. Failed updates, pending images, aliased/in-use/pinned images, partial cleanup, foreign tags and history symlinks are covered. Log tests cover oversized legacy logs, large Unicode writes, the 40 MiB total log bound, and following output across rotation. Shared build caches and database growth remain outside these bounds.
- Live retention check on macOS ARM/Podman: built and imported tiny disposable images into the existing kind node. An obsolete image was removed from both stores; an image sharing its ID with a retained tag was preserved. AWX deployment specifications/readiness and saved state were unchanged. All verification tags were then removed. The 0.4.5 wheel also passed the isolated install/PATH/assets/state/uninstall check. Linux and Docker runtime retention remain covered by simulated tests, pending live verification.

- Baseline deployment completed on macOS ARM64 / Podman: AWX `24.6.2.dev962+ga17f5c167`, Operator devel at `e3f2ca0bb0df15319e77f3a036f85f44847d9927`, successful database migrations, web/task deployment readiness, API ping, admin authentication and credential type registration. The corrected image was rebuilt from the same AWX commit using the cached UI; default UID 1000 could execute the startup scripts. The existing cluster and database volume were retained. Browser UI, live PR updates, plugin verification, Ansible job execution and stop/resume are separate pending acceptance checks.
- Version 0.4.4: 111 automated tests passed, including the real Kustomize renderer. A fresh wheel installation passed command/PATH, package asset, shared-state and uninstall checks.
- The development Operator reproduced the macOS ARM/Podman OpenSSL issue: `ansible --version` exited 132, while `OPENSSL_armcap=0 ansible --version` succeeded. The generated overlay applies that environment variable only to the Operator on Darwin/arm64/Podman. Tests cover that platform boundary and render the patch with real kubectl.
- Operator compatibility: 2.19.1 injected `from django_auth_ldap.config import *`, causing current AWX to fail with ModuleNotFoundError. The published `quay.io/ansible/awx-operator:devel` image was inspected and contains the upstream optional-import fix. Its manifest supports linux/arm64 and linux/amd64; `devel` is now the CLI default, with explicit release tags still accepted. See the [upstream development template](https://github.com/ansible/awx-operator/blob/devel/roles/installer/templates/configmaps/config.yaml.j2).
- Source permissions: the original baseline image contained root-owned 0700 startup scripts while running as UID 1000, causing web/rsyslog/init containers to fail with Permission denied. Git checkout inherited the CLI's 0077 umask. Source preparation now restores Git-tracked file modes and traversable directories inside the private run directory. Real local Git tests cover baseline/PR checkouts, retained old modes, symlinks, and preservation of private parent/.git/target permissions.
- AWX startup revealed that the Operator applies `image_pull_policy` to PostgreSQL and other supporting images as well as AWX. Setting it to `Never` blocked the initial database pod. Initial deployments and PR updates now use `IfNotPresent`; the AWX image is imported first and import failures stop deployment.
- Operator proxy image: the retired `gcr.io/kubebuilder/kube-rbac-proxy:v0.15.0` returned NotFound during the live rollout. The overlay now redirects the repository to the project-maintained `quay.io/brancz/kube-rbac-proxy`, retaining the upstream tag. Its live registry manifest includes linux/arm64 and linux/amd64. See [Kubebuilder registry retirement](https://github.com/kubernetes-sigs/kubebuilder/discussions/3907).
- Deployment manifest correction (0.4.4): Kustomize discovers the generated `kustomization.yaml` (JSON syntax is accepted as YAML). The overlay relies on the Operator base namespace instead of adding a duplicate. The real kubectl 1.34.11 / Kustomize 5.7.1 rendered the complete checked-out AWX Operator 2.19.1 configuration, including CRDs, the pinned Operator image and exactly one namespace, with legacy generated JSON files still present. Regression tests exercise the real renderer when kubectl is on PATH.
- Baseline build (22 October): AWX `devel` at `a17f5c167b24b8639baa7c5fc5841d6a7e3e6bcb` built successfully on macOS ARM64 / Podman (client 5.5.0, server 5.0.1), producing image `4254b86bea648fcad7882fdbace5b508e3d276ecd3bee000f97db6bf58c42cfb`. kind v0.33.0 created the Kubernetes v1.34.0 cluster successfully. The original Docker-only image loader failed before Operator deployment.
- Live archive-import workaround: exported the retained AWX image with Podman in 5.6 seconds and imported it into the existing kind node in 11.8 seconds; `crictl inspecti` confirmed the exact image ID and tag. The temporary archive was removed. The corrected baseline image subsequently used the same import path in the successful deployment above.
- Image-loader regression tests cover Podman export/import without Docker, Docker's direct loader, workspace paths with spaces, partial archive cleanup on export/import failure and Ctrl-C, and preventing rollout after a failed import.
- Podman startup troubleshooting (22 October): the VM reported running, and a subsequent real API check succeeded with 15.6 GiB RAM and approximately 77 seconds of guest uptime. The full installed doctor check passed without restarting or reconfiguring Podman. Transient startup timing is the likely explanation for the reported SSH reset; its exact cause was not reproduced.
- Runtime-readiness tests cover recovery from SSH resets, bounded retries, persistent API failure while running, starting/stopped VMs, rootful connections, authentication failures, diagnostic failures, connection overrides and unchanged Linux/Docker behavior.

- Python 3.13.4 on macOS ARM64.
- macOS ARM64 / Podman 5.5.0: reproduced the failed helper mount from the AWX PR #16752 run. With the macOS mount fix and OpenSSL compatibility setting, the real helper successfully read the checkout, rendered the Kubernetes Dockerfile, and reported AWX version `24.6.2.dev964+gbe3291b84`. This check did not build the full AWX image or create a cluster.
- Regression tests cover macOS Podman helpers for both AWX and plugin checkouts, retaining Linux SELinux labeling, and keeping Docker options unchanged.
- A subsequent AWX PR #16752 run reached `make ui`; the Podman VM kernel confirmed that the out-of-memory killer terminated webpack with only 2 GiB RAM and no swap. After increasing that same idle VM to 16 GiB, the real `ui-builder` image stage completed successfully (image `91b06470f3fa`). The runtime reported 15.6 GiB total RAM and passed doctor. At that point the full AWX image and deployment remained unverified; the later baseline build is recorded above.
- Memory regression tests cover Podman/Docker info formats, minimum and recommended RAM thresholds with guest reserved-memory allowance, stopping `up`/`test` before fetching on low RAM, and printing/saving resource warnings. The installer reports an undersized running VM without restarting it.
- CLI parsing, dependency reporting, dry planning and report generation.
- On the development host, the real prerequisite preview selected the existing stopped VM and cancellation at `Y/n` left tools and VM configuration unchanged.
- Prerequisite installer tests cover approval/cancellation, platform package plans, default VM selection, memory changes for stopped VMs, checksum validation and final verification. Host package installs and VM changes are simulated in these tests.
- Installable wheel built from the source distribution, including the helper Containerfile and a shared package/CLI version.
- Fresh-environment installation: `awx-lab` resolves on PATH, runs outside the checkout, and discovers the same saved runs from different working directories. Uninstall removes the command and retains saved runs.
- Real local Git repositories: merge includes both PR and updated target, deterministic merge SHA, conflicts fail, moving PR heads fail.
- Plugin wheel injection preserves extras, installs the exact local wheel after the base dependency layer, and records a content hash. The generated commands are tested; a full image build with this adapter remains pending.
- Simulated lifecycle: baseline creation without a PR, repeated up without rebuilding, failed build/deployment/smoke checks, partial cluster creation, Ctrl-C, retained environments and cleanup failures.
- PR update tests cover latest PR and target fetches in real local Git repositories without recloning; switching PRs removes the previous changes; unchanged revisions skip building; failed fetches, builds and rollouts retain the environment; tests run only after the requested image is ready. Cluster and container operations are simulated.
- Default environment selection, concurrent-operation locks, stop without deletion, resume without building, pending-update recovery and purge retry are covered by tests. Runtime/container operations are simulated; stop/resume is not yet verified on a live kind cluster.
- UI cache tests cover backend-only changes, UI/dependency/build-instruction changes, architecture changes and `--no-cache`. Simulated cleanup removes all tracked per-run image tags and retains run state if image removal fails.
- Per-run locking, path validation, process timeouts, live output and secret-output suppression.
- Registry manifests for `quay.io/ansible/awx-operator:2.19.1`, `quay.io/ansible/awx-ee:24.6.1`, and `quay.io/ansible/receptor:devel` include both linux/arm64 and linux/amd64. This verifies those published manifests, not the entire deployment.

## Required acceptance runs

| Platform | Runtime | Full AWX build | kind + Operator readiness | API + plugin verification | Ansible job |
| --- | --- | --- | --- | --- | --- |
| macOS ARM64 | Podman machine | Passed (baseline) | Passed (baseline) | API passed; plugin pending | Pending |
| Linux x86_64 | Podman | Pending | Pending | Pending | Pending |
| macOS ARM64 | Docker Desktop | Pending | Pending | Pending | Pending |
| Linux x86_64 | Docker | Pending | Pending | Pending | Pending |

The development host initially had Podman installed with its VM stopped and kind/kubectl absent. The baseline deployment above used the existing Podman VM with 5 CPUs and 16 GiB RAM. CI covers CLI tests, not this acceptance matrix.

Before rollout, select representative open AWX and plugin PRs and complete:

1. Run `doctor`, then `up` without a PR. Confirm the baseline URL, login, UI and recorded source/image.
2. Run `test <plugin-PR>` and verify the expected plugin changes, merge and wheel hash. Then switch to an AWX-only PR and confirm no previous custom plugin override remains.
3. Run `test <AWX-PR> --suite job`; verify reports and successful Ansible execution while the environment remains running.
4. Push a backend change and repeat the test. Verify the new image, stable URL/credentials and retained test data, and compiled UI cache reuse. Repeat for target-branch changes and unchanged revisions.
5. Run `down`. Verify the environment's node is stopped and unrelated containers remain running. Run `up`; verify the same node and database, current image, URL and credentials, without fetching or rebuilding. Repeat after a runtime VM restart.
6. Interrupt a test rollout, confirm no pass against old healthy pods, and recover through `up` or another `test`.
7. Run `down --purge`; confirm this cluster, database, lab image tags and workspace are gone. Verify shared caches and unrelated workloads remain. Run `up` again and confirm a fresh database.
8. Record cold/warm build times, peak memory and disk consumption to replace planning estimates with measured sizing.

## Known boundaries

- Current AWX devel needs a compatible development Operator; the default is now `devel`. The Operator image/branch and supporting image tags are mutable, and compatibility with arbitrary PRs is not guaranteed.
- Source revisions may change build layout or dependencies; adapters fail rather than silently skip the requested plugin.
- Rootless kind may require host kernel/cgroup configuration that `doctor` does not fully validate.
- macOS runtime VM storage must be sized separately from the workspace disk check.
- PR tests retain the current database and applies normal AWX migrations. Snapshot/restore, migration rollback and live source mounting are not provided. End-to-end stop/resume, PR updates and build-time improvements still need measurement.
- Private repositories, GitLab orchestration, a shared server, automatic PR triggers and comprehensive AWX regression suites are outside this first increment.

kind has a historical, still-open [Podman restart issue](https://github.com/kubernetes-sigs/kind/issues/2272). The CLI verifies Kubernetes and AWX readiness after starting a retained node and reports failure without deleting data. This is not a compatibility guarantee for untested runtime/node-image combinations.
