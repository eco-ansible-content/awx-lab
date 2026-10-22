# AWX Lab

Build one local AWX testing environment, test pull requests in it, and stop or destroy it when finished. The CLI remembers the environment; no run ID or `--reuse` flag is needed.

```bash
git clone https://github.com/eco-ansible-content/awx-lab.git
cd awx-lab
pipx install .
pipx ensurepath

# Open a new terminal after ensurepath. These work from any directory:
awx-lab prereq install
awx-lab up
awx-lab test https://github.com/ansible/awx/pull/123
awx-lab down
awx-lab up
awx-lab down --purge
```

Replace `123` with an open PR. The first `up` builds AWX from `devel` and creates kind, AWX Operator and PostgreSQL. Later `up` calls resume the existing environment without fetching or rebuilding. `test <PR_URL>` fetches the PR and latest target branch, merges them and updates AWX in that environment. The environment stays available for manual testing after the checks.

| Command | Behavior |
| --- | --- |
| `awx-lab up` | Create the baseline environment once, or start the retained environment |
| `awx-lab test <PR_URL>` | Fetch, merge and test that PR in the running environment |
| `awx-lab down` | Stop the environment's node container; retain the database, images and files |
| `awx-lab down --purge` | Destroy the cluster/database, tracked lab image tags and saved files |

`awx-lab credentials` prints the URL and login. `awx-lab status` shows saved state; `awx-lab logs --follow` follows the build/test log. All select the current environment automatically.

**Status: initial implementation.** Baseline AWX build, kind/Operator deployment and authenticated API smoke checks have passed on macOS ARM with Podman. Live PR updates, runtime stop/resume and Linux acceptance runs remain pending. See [validation status](docs/validation.md).

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

A built wheel can be installed with `pipx install /path/to/awx_lab-0.4.6-py3-none-any.whl`. The Python package is shared across macOS ARM64 and Linux x86_64; AWX images are still built for the native runtime architecture. After installing the CLI, run `awx-lab prereq install` to check and install the host tools with your approval.

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
- **Podman on macOS:** reuse the configured local VM, increase an undersized stopped VM's memory, and start it. If none exists, create a VM with 6 CPUs, 16 GiB RAM and 80 GiB disk. Each change appears in the approval plan. Running VMs are not restarted or resized. If a running VM has too little RAM, stop it yourself and rerun the installer; the memory check fails until it is adequately sized.
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

Start with **4–8 CPUs, 12–16 GiB RAM and at least 40 GiB free disk for one lab**. These are planning estimates.

The CLI requires at least 8 GiB allocated to the runtime (allowing 512 MiB for memory reserved by the guest OS) and warns below the recommended range. A confirmed 2 GiB Podman VM exhausted memory during the UI build; passing the memory check does not guarantee every PR will fit.

On macOS, allocate resources to the container VM as well as leaving memory for the host. `doctor` checks runtime connectivity, architecture, total RAM where reported, tools and workspace disk. `up` and `test` stop before fetching or building when the memory check fails and display resource warnings in the terminal and saved log. These checks do not change runtime settings or prove kind's kernel/cgroup prerequisites.

On macOS, a Podman VM can report running while its SSH/API connection is still becoming available. The prerequisite check retries transient connection failures up to five attempts within a 30-second probe window when the selected local VM is running or starting. Persistent failures report that VM's state and connection checks; authentication errors, stopped VMs and remote/overridden connections are not retried automatically. No VM restart or connection change is performed.

For macOS Podman, initialize a machine if you do not already have one, then start it:

```bash
podman machine init --cpus 6 --memory 16384 --disk-size 80  # only if no machine exists
podman machine start
awx-lab doctor --runtime podman
```

For rootless Linux, review [kind's rootless prerequisites](https://kind.sigs.k8s.io/docs/user/rootless/): cgroup v2, delegated controllers, PID/inotify limits and networking modules. The prerequisite installer reports kernel/cgroup failures for manual configuration. Install kind using its [official instructions](https://kind.sigs.k8s.io/docs/user/quick-start/) and use a kubectl version compatible with the selected Kubernetes node image (default Kubernetes 1.34).

## Commands

```bash
# Preview baseline setup or a PR test without executing commands.
awx-lab plan --json
awx-lab plan https://github.com/ansible/awx/pull/123 --json

# Create the environment, optionally choosing its baseline and runtime.
awx-lab up
awx-lab up --awx-ref devel --runtime podman

# Test an AWX PR; repeated calls fetch its latest commits and target branch.
awx-lab test https://github.com/ansible/awx/pull/123

# Switch to an awx-plugins PR using the baseline AWX ref selected by up.
awx-lab test https://github.com/ansible/awx-plugins/pull/456

# Override companion AWX only for this plugin test.
awx-lab test https://github.com/ansible/awx-plugins/pull/456 --awx-ref devel

# Test related AWX/plugin PRs together and launch a localhost Ansible ping job.
awx-lab test https://github.com/ansible/awx/pull/123 \
  --plugins-pr https://github.com/ansible/awx-plugins/pull/456 --suite job

awx-lab status
awx-lab status --json
awx-lab logs --follow
awx-lab credentials
awx-lab down
awx-lab up
awx-lab down --purge
```

`test` requires a running environment created by `up`; it does not automatically create or destroy a cluster. After `down`, run `up` before testing. After `down --purge`, the next `up` creates a fresh baseline and database. `up` on an already running environment checks readiness without rebuilding or resetting the tested PR.

Environment settings belong to the first `up`: `--runtime`, `--awx-ref`, `--headless`, `--operator-version`, `--node-image`, `--ee-image`, `--port`, `--build-timeout` and `--ready-timeout`. Changing those on a later `up` requires destroying the existing environment first. `--runtime auto` prefers reachable Podman, then Docker. `--headless` skips the UI. Timeouts are seconds; the default HTTP port is a free loopback port.

`test --suite job` adds the Ansible ping test. `test <PR_URL> --no-cache` forces an AWX/UI rebuild even when source revisions are unchanged. The old fresh `test`/`--keep`/`--reuse` modes are replaced by this single-environment workflow. Tests always retain the environment, including on failure or Ctrl-C.

## Updating and switching PRs

The environment retains its kind cluster, Operator, database, credentials and URL. Every test fetches the selected PR and its latest target branch and prepares a fresh local merge, replacing the previous PR's source changes. Omitting `--plugins-pr` removes any earlier companion plugin override from the next build. Plugin PRs use the environment's baseline AWX ref unless `--awx-ref` is supplied for that test.

Changed code gets a new image tag, which is loaded into kind and applied through the Operator. Tests wait for both web and task deployments to run that exact image. An unchanged source selection skips the image build and rollout and reruns the checks. A failed build or rollout reports failure, retaining the environment for inspection; it cannot pass by testing old healthy pods. An interrupted rollout is recorded, and `up` can finish applying its already-built image.

After a successful `up` or `test`, automatic cleanup keeps the current and previous successful AWX builds and their compiled UI caches. Older tracked image tags are removed from kind and Podman/Docker when unused. Active/pending workloads, shared image aliases and cleanup failures can retain additional images; cleanup retries on later successful runs. Failed attempts keep their images for inspection until the next success. The previous successful image is retained from version 0.4.5 onward; this is not a database rollback facility.

The first build still downloads dependencies. Later builds reuse compiled UI when only backend Python files or the companion plugin changed. Other tracked AWX file changes invalidate that cache conservatively. The UI cache retains the external `ansible-ui` revision fetched during its build; use `--no-cache` to refresh mutable upstream inputs. Custom plugin wheels install after the base requirements layer, allowing that layer to remain cached. Application changes still require an image build and restart; live source mounting is not used.

The CLI resets and cleans its managed source copies before each fetch, retaining Git objects. Push changes from your own development checkout; edits inside saved `awx/` or `awx-plugins/` directories are discarded on the next test. The database persists across PRs, and normal AWX migrations may change it. For clean-data testing or incompatible migrations, use `down --purge` followed by `up`. Database snapshots and migration rollback are not implemented.

## Stop, resume and destroy

`down` stops only this environment's kind node container, including Kubernetes, AWX and PostgreSQL. It verifies the container stopped, then records the stopped state. Disk state remains so `up` can restart the same node, refresh its private kubeconfig, and wait for Kubernetes and AWX readiness. Resume errors preserve the environment and data; the CLI never silently recreates a failed stopped cluster. Runtime-specific stop/resume acceptance runs are still required, particularly with Podman.

`down --purge` deletes the cluster and database, all tracked AWX/UI image tags for this environment, and its source files, logs and reports. Shared layers, helper images and build cache remain; no global prune is performed. If cleanup fails, saved state remains for retry.

Both commands leave the shared Podman/Docker VM running. To release its reserved host RAM on macOS, stop the VM separately when other workloads no longer need it. The CLI never stops unrelated containers or another user's cluster.

## What gets tested

1. `up` checks prerequisites, fetches the baseline AWX ref and renders its Kubernetes Dockerfile using upstream tooling. It builds for native `linux/arm64` or `linux/amd64`, creates kind, installs AWX Operator, and deploys AWX/PostgreSQL.
2. `test <PR_URL>` fetches the PR head and current target branch, merges locally and fails on conflicts. It records the base, head and merge SHAs. Nothing is pushed to GitHub.
3. For a plugin PR, build a wheel from the merged checkout. After AWX's base dependencies are installed, replace the installed `awx-plugins-core` with that wheel, preserving extras and resolving its dependencies.
4. Load the exact updated image into the existing kind cluster and wait for it to roll out. The Operator's shared pull policy is `IfNotPresent`: AWX uses the imported image with its unique local tag, while PostgreSQL, Redis and execution-environment images can be downloaded when missing. A failed AWX image import stops deployment.
5. Check API availability, authenticated admin access and credential-type registration. For plugin tests, verify the installed wheel SHA256 through pip metadata inside the AWX task container.
6. With `--suite job`, launch an Ansible `ping` ad hoc job against localhost inside its execution environment and require a successful result.
7. Save results and retain the environment for manual testing. Failed attempts also collect diagnostics when Kubernetes is reachable.

**A passing smoke run is not a full regression result.** It does not run all AWX unit/integration tests, verify every changed feature, or validate a cloud credential plugin against its external service. Use the retained environment for manual feature testing. Team-specific test scenarios are a next increment.

The default Operator is `devel`, with `quay.io/ansible/awx-ee:24.6.1` and `kindest/node:v1.34.0`. `--operator-version`, `--ee-image` and `--node-image` override them. The selection controls both manifests and the operator image. Operator 2.19.1 injects an LDAP import no longer present in current AWX; the development Operator handles that compatibility change. Explicit release tags remain available for matching older AWX versions. Development combinations still require acceptance testing. Supporting image tags and upstream Git-based dependencies can change; recorded PR SHAs alone do not make the entire build reproducible.

## Logs, results and cleanup

The default terminal output shows the selected test, check results and final summary. Routine fetching, environment setup, image building and deployment output goes to the saved log. Resource warnings and failures remain visible.

```bash
awx-lab test https://github.com/ansible/awx/pull/123
awx-lab test https://github.com/ansible/awx/pull/123 --error-log
awx-lab test https://github.com/ansible/awx/pull/123 --full-log
```

`--error-log` shows reported failures and their diagnostic context, including setup failures; a successful run is silent and exits zero. `--full-log` streams verbose command output, including setup and builds. The flags are mutually exclusive and also work with `up` and `down`. Put the flag after the subcommand. The choice applies only to that invocation and never changes execution, test selection or exit status.

All modes save unfiltered output, subject to the retention limits below and existing sensitive-output suppression. Use `awx-lab logs --follow` from another terminal for live detail, or `awx-lab logs` afterward. These commands read the saved log without filtering. The prerequisite installer keeps its dependency list and approval prompt visible and does not accept these flags.

State lives under `~/.local/share/awx-lab/runs/`. `current.json` selects the environment automatically; an internal unique name identifies its cluster and directory. Set `AWX_LAB_HOME` or put `--state-dir <path>` **before** the subcommand to use another location. Commands from any working directory find the same selected environment.

Inside the selected environment directory:

- `run.log`: recent streamed commands and build/deployment output; capped at 10 MiB with three rotated backups (`run.log.1` through `run.log.3`, newest first). Terminal streaming continues in full and `logs --follow` follows rotation.
- `report.json`: result, exact source revisions, wheel hash, image ID and stage timings.
- `junit.xml`: lifecycle and smoke-check results for report consumers.
- `state.json`: saved lifecycle state; `status` is not a live health check.
- `diagnostics/`: pod status, events and Operator/AWX logs from the latest failed attempt, when reachable.
- `kubeconfig`, `awx/`, `awx-plugins/`, `awx-operator/`: private managed workspace.
- `history/<attempt-id>/`: the ten most recent archived states, reports and diagnostics. Older archives are removed after an attempt, including failed attempts.

Ctrl-C stops the active command, attempts diagnostics and retains the environment. Run `down` to stop it or `down --purge` to destroy it. Root and environment locks prevent overlapping creation, tests, stop or cleanup from different terminals. Retention only deletes recorded images belonging to this environment; cleanup warnings appear in the terminal and report without changing a passed test into a failed test.

This is artifact retention, not a fixed disk quota. Shared runtime build layers and helper images remain for speed, Git object stores grow as sources change, and the database retains test data. Failed builds can accumulate until a successful run permits image cleanup. No global image, volume or build-cache prune is performed. Freeing space inside the Podman VM also does not guarantee that its host disk file immediately shrinks.

When upgrading from 0.3, a single retained legacy environment is selected automatically. Older failed-build directories are preserved. If multiple legacy environments exist, `status --all` lists their internal names; optional legacy IDs remain accepted by `logs`, `credentials` and `down` solely for maintaining those environments. The normal workflow never requires an ID.

Only public GitHub AWX/awx-plugins repositories are supported initially. `GITHUB_TOKEN`/`GH_TOKEN` can raise GitHub API rate limits; those tokens are not forwarded into build subprocesses or container images. No GitHub labels, CI permissions, repository writes or server are needed.

PR builds execute the supplied code and Dockerfile on your local runtime. Use trusted PRs. kind nodes require runtime privileges. Workspace files are private by default; diagnostic logs can contain application data, so review them before sharing. AWX is published on loopback only, using HTTP for local testing.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
.venv/bin/pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m build
python scripts/check_install.py dist/awx_lab-0.4.6-py3-none-any.whl
```

You can also invoke `.venv/bin/python` directly. Tests use the standard library and temporary local Git repositories; they do not require a container runtime. When `kubectl` is on PATH, they also render the generated Operator overlay with real Kustomize. To include installer-managed tools, run tests with `PATH="$HOME/.local/share/awx-lab/bin:$PATH" python -m unittest discover -s tests -v`. The included GitHub Actions workflow tests the CLI on Linux and macOS, builds a wheel from the source distribution, and verifies installation, packaged assets, cross-directory run discovery, and uninstall in a fresh environment. It does not run full AWX deployments.

For development from the checkout without installation, `./awx-lab` remains available. An editable installation with `pipx install --editable .` also reflects source edits immediately. Keep the version in `awx_lab/__init__.py`; package metadata and `awx-lab --version` use that same value. See [the design notes](docs/design.md) for how this adapts Bill's baseline.
