# AWX Lab

Build a local AWX environment from an AWX or awx-plugins pull request, test it, and keep the results in your terminal.

```bash
git clone https://github.com/eco-ansible-content/awx-lab.git
cd awx-lab
pipx install .
pipx ensurepath

# Open a new terminal after ensurepath, then run from any directory:
awx-lab prereq install
awx-lab up https://github.com/ansible/awx-plugins/pull/123
```

Replace `123` with an open PR. `up` streams build and deployment logs, runs API smoke checks, and prints the lab URL and run ID. Retrieve its generated login with `awx-lab credentials <run-id>`. The lab stays up until `awx-lab down <run-id>`.

**Status: initial implementation.** The CLI and local Git/lifecycle tests have been exercised on macOS ARM. A complete AWX image build and deployment has not yet been validated on either target platform. See [validation status](docs/validation.md) before treating this as the team's standard testing tool.

## Installation

AWX Lab installs a terminal command named `awx-lab`. You install it once with **pipx**, which keeps the application in its own Python environment and adds the executable to your user command directory. No virtual-environment activation or repository working directory is needed to use it.

Install Python 3.11+ and [pipx](https://github.com/pypa/pipx#install-pipx) first. For example, `brew install pipx` on macOS or `sudo dnf install pipx` on Fedora, using your platform's supported package sources.

From an existing clone:

```bash
pipx install /absolute/path/to/awx-lab
pipx ensurepath
```

Open a new terminal if pipx updated your PATH. Then `awx-lab --version` and `awx-lab --help` work from any directory. Installation copies the package and its build assets into pipx's environment; the clone is needed again only when updating from that local source. Choose Python explicitly if necessary: `pipx install --python python3.12 /absolute/path/to/awx-lab`.

Team members can also install directly from the repository:

```bash
pipx install 'git+https://github.com/eco-ansible-content/awx-lab.git'
pipx ensurepath
```

A built wheel can be installed with `pipx install /path/to/awx_lab-0.2.0-py3-none-any.whl`. The Python package is shared across macOS ARM64 and Linux x86_64; AWX images are still built for the native runtime architecture. After installing the CLI, run `awx-lab prereq install` to check and install the host tools with your approval.

To update a local-clone installation, update that clone and run `pipx reinstall awx-lab`. For a GitHub installation, the same command reinstalls from its recorded Git URL. To remove the command, run `pipx uninstall awx-lab`. Saved runs and running clusters remain independent of the installed package; clean up unwanted labs with `awx-lab down` before uninstalling.

## Install prerequisites

```bash
awx-lab prereq install
```

The installer runs `doctor`, lists missing prerequisites and the exact proposed changes, then waits for:

```text
Install/configure these prerequisites? [Y/n]
```

`Y`, `yes`, or Enter approves the displayed plan. `n`, `no`, or end-of-input cancels without installing or starting anything. There is no unattended `--yes` option. Once approved, it applies the plan and runs `doctor` again. Failed installation or failed verification returns a nonzero exit code; completed steps are retained and skipped when you retry.

Preview without changing anything:

```bash
awx-lab prereq install --dry-run
awx-lab prereq install --runtime podman
```

The installer handles:

- **kind and kubectl:** native official binaries, verified against their published SHA256 checksums. It pins kind `v0.33.0` and kubectl `v1.34.11` to keep kubectl in the default cluster's Kubernetes 1.34 minor.
- **Missing Git/Podman:** Homebrew on macOS; `dnf` on Fedora/RHEL-family systems or `apt-get` on Debian/Ubuntu. Linux package installation can prompt for sudo after approval.
- **Podman on macOS:** reuse the configured local VM, increase an undersized stopped VM's memory, and start it. If none exists, create a VM with 6 CPUs, 16 GiB RAM and 80 GiB disk. Each change appears in the approval plan. Running VMs are not restarted or resized.
- **Docker:** use an already working installation; start Docker Desktop on macOS or its system service on Linux when needed. Missing Docker can be installed through Homebrew's cask on macOS or `docker.io` on APT-based Linux. For DNF-based Linux, choose Podman or install Docker separately.

To select a Podman VM or adjust allocation:

```bash
awx-lab prereq install --machine awx-lab --cpus 6 --memory 12288 --disk-size 80
```

Memory is MiB; disk is GiB. CPU/disk settings apply to new VMs; memory also applies when an existing stopped VM has less than 8 GiB. Existing larger VMs are preserved. If multiple machines have no clear default, select one explicitly. Other running machines and nonlocal runtime connections require manual resolution.

Downloaded tools live in `~/.local/share/awx-lab/bin`. Every `awx-lab` command discovers that directory automatically; your shell configuration is unchanged, and existing tools on PATH take precedence. These tools are independent of the pipx environment, so reinstalling the CLI preserves them.

Python/pipx must already be available to install AWX Lab. Missing Homebrew, unsupported Linux package managers, kernel/cgroup configuration, and Docker socket permissions are reported as manual prerequisites. The installer does not replace Python, change login groups, modify boot settings, or delete VMs. Resource warnings are shown again after verification.

## Prerequisites

| Requirement | macOS ARM64 | Linux x86_64 |
| --- | --- | --- |
| Python | Python 3.11 or newer | Python 3.11 or newer |
| Git | Installed and on PATH | Installed and on PATH |
| Container runtime | Podman with a running native ARM VM, or Docker Desktop | Podman or Docker with a running daemon/socket |
| Kubernetes tools | kind and kubectl | kind and kubectl |
| Connectivity | GitHub, PyPI, OS package mirrors, Docker Hub and Quay | Same |

The CLI has no Python runtime package dependencies. pipx installs it in an isolated environment. A helper container supplies Python 3.12, Ansible, make and build tooling. Node/UI build dependencies come from AWX's build image. Compose is not required.

Start with **4–8 CPUs, 12–16 GiB RAM and at least 40 GiB free disk for one lab**. These are planning estimates, not measured minimums. On macOS, allocate resources to the container VM as well as leaving memory for the host. `doctor` checks runtime connectivity, architecture, available RAM where reported, tools and workspace disk. It does not change runtime settings or prove kind's kernel/cgroup prerequisites.

For macOS Podman, initialize a machine if you do not already have one, then start it:

```bash
podman machine init --cpus 6 --memory 16384 --disk-size 80  # only if no machine exists
podman machine start
awx-lab doctor --runtime podman
```

For rootless Linux, review [kind's rootless prerequisites](https://kind.sigs.k8s.io/docs/user/rootless/): cgroup v2, delegated controllers, PID/inotify limits and networking modules. The prerequisite installer reports kernel/cgroup failures for manual configuration. Install kind using its [official instructions](https://kind.sigs.k8s.io/docs/user/quick-start/) and use a kubectl version compatible with the selected Kubernetes node image (default Kubernetes 1.34).

## Commands

```bash
# Inspect the proposed run, without fetching code or starting containers.
awx-lab plan https://github.com/ansible/awx/pull/123 --json

# Retain a lab for manual testing, including its UI.
awx-lab up https://github.com/ansible/awx/pull/123 --runtime podman

# Test a plugin PR against AWX devel (or select a branch, tag or SHA).
awx-lab up https://github.com/ansible/awx-plugins/pull/123 --awx-ref devel

# Test related AWX and plugin PRs together.
awx-lab test https://github.com/ansible/awx/pull/123 \
  --plugins-pr https://github.com/ansible/awx-plugins/pull/456

# Smoke test, save reports and remove the cluster, even after failure.
awx-lab test https://github.com/ansible/awx-plugins/pull/123

# Also run a real Ansible localhost ping job; keep the lab for inspection.
awx-lab test https://github.com/ansible/awx/pull/123 --suite job --keep

awx-lab status
awx-lab status <run-id> --json
awx-lab logs <run-id> --follow
awx-lab credentials <run-id>
awx-lab down <run-id>
awx-lab down <run-id> --purge
```

`--runtime auto` prefers a reachable Podman runtime, then Docker. Use `--runtime` explicitly if you have both. `--headless` skips the UI; `--no-cache` disables AWX layer reuse. `--build-timeout` and `--ready-timeout` are seconds. `--port` chooses a fixed unprivileged HTTP port; otherwise each run chooses a free loopback port. Run `awx-lab <command> --help` for all options.

Each `up` or `test` creates a new run and a fresh database. Runs have separate kind cluster names, kubeconfigs, source directories, image tags and ports. The host's active Kubernetes context is not used. Multiple runs share CPU, memory, disk and build cache; isolation is not a resource quota or a security boundary.

## What gets tested

1. Fetch the PR's current head and its target branch. Merge locally and fail on conflicts. Record the base, head and resulting merge SHA. Nothing is pushed to GitHub.
2. Render AWX's Kubernetes Dockerfile using upstream build tooling. Build for the host/runtime's native `linux/arm64` or `linux/amd64` architecture, with the UI by default.
3. For a plugin PR, build a wheel from the merged checkout. Replace AWX's `awx-plugins-core` requirement with that local wheel, preserving extras such as `credentials-github-app`. Copy it into the image before dependency installation.
4. Create kind, load that exact image, install AWX Operator, and deploy AWX and PostgreSQL. AWX's pull policy is `Never` so Kubernetes cannot substitute a registry image.
5. Check API availability, authenticated admin access and credential-type registration. For plugin runs, verify the installed wheel's SHA256 using pip metadata inside the AWX task container.
6. With `test --suite job`, launch an Ansible `ping` ad hoc job against localhost inside its execution environment and require a successful result.
7. Save reports and diagnostic logs. `test` deletes its cluster unless `--keep` is supplied. `up` keeps it, including after deployment failures, for inspection.

**A passing smoke run is not a full regression result.** It does not run all AWX unit/integration tests, verify every changed feature, or validate a cloud credential plugin against its external service. Use `up` for manual feature testing. Team-specific test scenarios are a next increment.

The default Operator is `2.19.1`, with `quay.io/ansible/awx-ee:24.6.1` and `kindest/node:v1.34.0`. `--operator-version`, `--ee-image` and `--node-image` override them. Operator release tags select both manifests and the operator image. AWX devel and arbitrary PRs are development combinations: compatibility with this Operator must be validated. Supporting image tags and upstream Git-based dependencies can change; recorded PR SHAs alone do not make the entire build reproducible.

## Logs, results and cleanup

Runs live under `~/.local/share/awx-lab/runs/<run-id>/`. Set `AWX_LAB_HOME` or put `--state-dir <path>` **before** the subcommand to use another location.

- `run.log`: streamed commands and build/deployment output.
- `report.json`: result, exact source revisions, wheel hash, image ID and stage timings.
- `junit.xml`: lifecycle and smoke-check results for report consumers.
- `state.json`: current saved lifecycle state; `status` is not a live health check.
- `diagnostics/`: pod status, events and Operator/AWX logs, captured before cleanup.
- `kubeconfig`, `awx/`, `awx-plugins/`, `awx-operator/`: private run workspace.

`down` deletes only the named run's cluster and database. It retains sources, reports and images. `down --purge` also removes that run's image and workspace. Shared image layers and helper images remain cached. No global container/image/volume prune is performed.

Ctrl-C during a test attempts to stop the current command, save diagnostics and remove its cluster. If the runtime is unavailable, the run reports `cleanup_failed`; retry `down` when the runtime returns. Forced termination or host shutdown may leave a run's cluster behind. A run lock prevents cleanup during an active operation.

Only public GitHub AWX/awx-plugins repositories are supported initially. `GITHUB_TOKEN`/`GH_TOKEN` can raise GitHub API rate limits; those tokens are not forwarded into build subprocesses or container images. No GitHub labels, CI permissions, repository writes or server are needed.

PR builds execute the supplied code and Dockerfile on your local runtime. Use trusted PRs. kind nodes require runtime privileges. Workspace files are private by default; diagnostic logs can contain application data, so review them before sharing. AWX is published on loopback only, using HTTP for local testing.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
.venv/bin/pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m build
python scripts/check_install.py dist/awx_lab-0.2.0-py3-none-any.whl
```

You can also invoke `.venv/bin/python` directly. Tests use the standard library and temporary local Git repositories; they do not require a container runtime. The included GitHub Actions workflow tests the CLI on Linux and macOS, builds a wheel from the source distribution, and verifies installation, packaged assets, cross-directory run discovery, and uninstall in a fresh environment. It does not run full AWX deployments.

For development from the checkout without installation, `./awx-lab` remains available. An editable installation with `pipx install --editable .` also reflects source edits immediately. Keep the version in `awx_lab/__init__.py`; package metadata and `awx-lab --version` use that same value. See [the design notes](docs/design.md) for how this adapts Bill's baseline.
