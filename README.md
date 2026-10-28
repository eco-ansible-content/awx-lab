# AWX Lab

AWX Lab is a local CLI that prepares an AWX environment for testing changes from GitHub pull requests or your local working checkout. Create the environment once, then select an AWX/awx-plugins PR or run `test --local` from your source checkout. It builds the application image, updates AWX, and runs basic checks. PR mode merges the latest PR and target branch; local mode includes uncommitted changes without requiring a commit, push or PR. The environment stays running so you can open AWX and test the feature yourself.

Each engineer runs their own lab on their computer. The CLI is available from any terminal directory and remembers the environment; the normal workflow needs no run ID. No access to AWX CI, GitHub labels or repository write permissions is required.

```text
awx-lab up              Create the baseline environment, or resume it
awx-lab test <PR_URL>   Build and check a PR in that environment
awx-lab test --local    Build and check your current working files
awx-lab test --local <BRANCH>  Test edits on this checked-out branch
awx-lab credentials    Show the AWX URL and admin login
awx-lab down            Stop the environment and keep its data
awx-lab down --purge    Destroy the environment and its data
```

`test --local <BRANCH>` requires that branch to exist locally and already be checked out. It includes saved uncommitted edits and does not switch branches.

The host CLI drives Podman or Docker, with build tooling in a helper container and Kubernetes running through kind. AWX Operator manages AWX and PostgreSQL inside that cluster. On macOS, the containers run inside the runtime's Linux VM. One command orchestrates several containers; Compose is not required.

**Status: early implementation.** Baseline creation and an AWX PR update with authenticated API checks have passed on macOS ARM64 with Podman. Other runtime/platform combinations and some scenarios still need live acceptance testing; see [supported platforms](#supported-platforms) and the detailed [validation record](docs/validation.md).

## Contents

- [Supported platforms](#supported-platforms)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Installation and updates](#installation-and-updates)
- [Prerequisite installer](#prerequisite-installer)
- [Daily workflow](#daily-workflow)
- [Command reference](#command-reference)
- [What a passing result means](#what-a-passing-result-means)
- [Logs and reports](#logs-and-reports)
- [Local changes, caching and retention](#local-changes-caching-and-retention)
- [Troubleshooting](#troubleshooting)
- [Current limits](#current-limits)
- [Development](#development)

## Supported platforms

The project targets **macOS on Apple Silicon (ARM64)** and **Linux on x86_64**, using native application images. Implemented support and completed live validation are separate:

| Host | Runtime | Live validation status |
| --- | --- | --- |
| macOS ARM64 | Podman machine | Baseline build/deployment and AWX PR update passed API checks |
| Linux x86_64 | Podman | Implemented; full deployment acceptance pending |
| macOS ARM64 | Docker Desktop | Implemented; full deployment acceptance pending |
| Linux x86_64 | Docker | Implemented; full deployment acceptance pending |

Browser feature testing, full plugin PR verification, the optional Ansible job, and stop/resume still need live acceptance runs. Automated CLI tests cover these paths with simulated runtime operations. See [validation status](docs/validation.md) for the evidence and remaining checks.

The prerequisite installer supports Homebrew on macOS, DNF on Fedora/RHEL-family Linux, and APT on Debian/Ubuntu. This does not certify every distribution/version. Windows, macOS Intel and Linux ARM64 are outside the current target/acceptance matrix. Cross-architecture emulation is not supported; the runtime architecture must match the host.

## Requirements

| Requirement | Details |
| --- | --- |
| Python | 3.11 or newer |
| CLI installation | [pipx](https://github.com/pypa/pipx#install-pipx) recommended; install it before AWX Lab |
| Host tools | Git, kind, kubectl, and Podman or Docker |
| macOS runtime | A running native ARM64 Podman VM or Docker Desktop |
| Linux runtime | A working Podman or Docker connection; rootless kind also needs suitable host configuration |
| Network | Access to GitHub, PyPI, container registries including Docker Hub and Quay, and upstream package mirrors |

For **one local lab**, start with **4–8 CPUs, 12–16 GiB RAM available to the container runtime, and at least 40 GiB free workspace disk**, plus sufficient runtime image/VM storage. These are planning estimates, not measured limits. A newly created Podman VM defaults to 6 CPUs, 16 GiB RAM and 80 GiB disk. On macOS, leave additional memory for the host OS and other applications.

`doctor` checks tools, OS/architecture, runtime connectivity, reported total RAM and workspace free disk. It blocks below 8 GiB runtime RAM, with 512 MiB allowed for guest-reserved memory, and warns below the recommended 12 GiB range or 40 GiB workspace disk. It checks total reported RAM, not currently available RAM; a passing result does not guarantee a build will fit. A 2 GiB Podman VM was observed to run out of memory during the UI build.

For rootless Linux, follow [kind's rootless prerequisites](https://kind.sigs.k8s.io/docs/user/rootless/) for cgroup v2, controller delegation, PID/inotify limits and networking modules. `doctor` checks cgroup v2 when reported by rootless Podman, but does not fully validate or configure those host requirements. Size macOS VM storage separately from the workspace disk check.

The CLI has no third-party Python runtime dependencies. Its helper container supplies Python 3.12, Ansible, make and build tooling; AWX's build image supplies UI dependencies.

## Quick start

Install Python 3.11+, Git and pipx first. On macOS, install Homebrew if you want the prerequisite installer to manage packages. Then clone and install:

```bash
git clone https://github.com/eco-ansible-content/awx-lab.git
cd awx-lab
pipx install .
pipx ensurepath
```

Open a new terminal if pipx changed your PATH. The following commands work from any directory:

```bash
awx-lab --version
awx-lab prereq install --runtime podman
awx-lab doctor --runtime podman
awx-lab up --runtime podman
```

The prerequisite installer lists proposed changes and asks for approval. The first `up` downloads dependencies, builds baseline AWX from `devel`, and creates kind, AWX Operator, PostgreSQL and an admin account. This is the expensive initial setup; progress details are available in another terminal with `awx-lab logs --follow`.

Test your local changes from a separate AWX or awx-plugins source checkout:

```bash
cd /path/to/your/awx
awx-lab test --local
awx-lab credentials
```

Or test a PR, replacing the example number with an **open** PR:

```bash
awx-lab test https://github.com/ansible/awx/pull/123
awx-lab credentials
```

After `Result: ready`, open the printed AWX URL and sign in with the generated credentials. You can now create any credentials, inventory, projects or job templates needed for your feature test. A plugin PR can be used in the same command: `awx-lab test https://github.com/ansible/awx-plugins/pull/456`.

When finished, run `awx-lab down` to stop the lab while keeping its database. Run `awx-lab up` to resume it. Use `awx-lab down --purge` only when you want to delete the cluster, database and saved lab files. See [daily workflow](#daily-workflow) for reuse and cleanup details.

## Installation and updates

pipx installs `awx-lab` in its own Python environment and exposes the command on your PATH. No virtual-environment activation or repository working directory is needed. Installation copies the package and its build assets; changes in a normal source clone take effect after reinstalling.

Alternative installation methods:

```bash
# Install from an existing local clone.
pipx install /absolute/path/to/awx-lab

# Or install directly from GitHub.
pipx install 'git+https://github.com/eco-ansible-content/awx-lab.git'

# Or install a wheel built from this repository.
pipx install /path/to/awx_lab-0.5.0-py3-none-any.whl

pipx ensurepath
```

Choose Python explicitly if needed: `pipx install --python python3.12 /absolute/path/to/awx-lab`. The same Python package works on both target platforms; AWX images are built for the native runtime architecture.

For an installation from a local clone, update that clone and reinstall:

```bash
cd /absolute/path/to/awx-lab
git pull --ff-only
pipx reinstall awx-lab
awx-lab --version
```

For installation directly from GitHub, `pipx reinstall awx-lab` reinstalls from the recorded Git URL. For a wheel installation, install the newly built wheel with `pipx install --force /path/to/new-wheel.whl`. Reinstalling the CLI preserves the lab, database, caches and downloaded tools.

To remove the CLI, use `pipx uninstall awx-lab`. If you also want to delete the lab, run `awx-lab down --purge` **before** uninstalling; uninstalling the Python package does not stop or delete containers or saved data.

## Prerequisite installer

```bash
awx-lab prereq install
awx-lab prereq install --dry-run
```

The installer runs `doctor`, lists missing prerequisites and proposed changes, then prompts `Install/configure these prerequisites? [Y/n]`. Enter, `Y` or `yes` approves; `n`, `no` or end-of-input cancels without installing or starting anything. There is no unattended `--yes` flag. After approved actions finish, `doctor` runs again. Installation/verification failure returns a nonzero exit code; retrying keeps completed steps.

`--dry-run` performs checks and prints the proposed plan without installing tools, changing VM settings or starting a runtime. It does not test whether the proposed installation will succeed.

| Component | What the installer can do |
| --- | --- |
| kind / kubectl | Download native official binaries, verify published SHA256 checksums, and install kind `v0.33.0` / kubectl `v1.34.11` |
| Git / Podman | Install missing packages through Homebrew, DNF or APT; Linux package actions may prompt for sudo |
| Podman on macOS | Reuse/start the configured local VM, create one if absent, or increase the memory of an undersized stopped VM |
| Docker | Start an installed Docker Desktop on macOS or system service on Linux; install through Homebrew cask or APT's `docker.io` when available |

For DNF-based Linux, use Podman or install Docker separately. Python, pipx and Homebrew must already be available where needed. Unsupported package managers, host kernel configuration and runtime permissions need manual setup. The installer does not change login groups, boot settings or remove VMs.

Podman VM options apply on macOS:

| Option | Default | Effect |
| --- | --- | --- |
| `--runtime auto\|podman\|docker` | `auto` | Select the runtime to check/install |
| `--machine NAME` | Configured local machine, or a new `awx-lab` machine | Select the Podman VM; useful when selection is ambiguous |
| `--cpus N` | `6` | CPU count for a new VM |
| `--memory MIB` | `16384` | Memory for a new VM, or an existing stopped VM with less than 8 GiB |
| `--disk-size GIB` | `80` | Disk size for a new VM |
| `--dry-run` | Off | Preview only |

For example: `awx-lab prereq install --runtime podman --machine awx-lab --cpus 6 --memory 16384 --disk-size 80`.

Running VMs are never restarted or resized. Existing larger VMs keep their allocation. If a running VM is undersized, stop it when its other workloads allow, then rerun the installer. Ambiguous machines, another running machine, or a nonlocal/overridden runtime connection may require manual resolution. Any proposed default-connection change is included in the approval plan.

Downloaded tools live in `~/.local/share/awx-lab/bin`. AWX Lab discovers that directory automatically without editing your shell configuration; existing tools on PATH take precedence. Those tools may therefore be usable through AWX Lab even if your shell cannot run `kind` directly. Reinstalling or uninstalling the CLI leaves them in place.

## Daily workflow

### Create or resume

```bash
awx-lab up
```

The first run creates the baseline without a PR. Later runs resume the same environment or check its readiness, preserving the tested PR, URL, credentials and database. They do not fetch newer baseline code or rebuild an already-built image. A failed/interrupted setup can be retried with `up`; a pending update can also be completed using its already-built image.

### Test local changes before committing

From your own AWX or awx-plugins checkout (including a subdirectory):

```bash
# Use the currently checked-out branch, including uncommitted changes.
awx-lab test --local

# Optionally require that this exact local branch is checked out.
awx-lab test --local my-feature

# The same test and logging options work in local mode.
awx-lab test --local --suite job --full-log
awx-lab test --local --no-cache
```

Save your edits in your editor first; local mode reads files on disk. It includes staged/unstaged modifications, deletions and non-ignored untracked files. Ignored untracked files are excluded, including files covered by your Git exclude configuration. Tracked files remain included even if an ignore rule matches them. Your checkout's branch, index and files are left untouched: no commit, stash, branch switch or generated build file is written there.

The CLI makes a separate snapshot in the managed lab workspace. It keeps enough Git history/tags for AWX and plugin versioning and may create a synthetic commit **only in that disposable snapshot**. Reports record `mode: local`, the repository path, branch, starting `head_sha`, `dirty`, `snapshot_sha`, `tree_sha` and a SHA256 `fingerprint` of the copied paths, contents and modes. `dirty` indicates that the snapshot's Git tree differs from HEAD.

Each invocation takes a fresh snapshot. A changed snapshot triggers a new image build; identical sources reuse the existing image and rerun checks. UI caching also accounts for uncommitted files: backend-only changes can reuse the UI, while UI changes invalidate it. Database, credentials, URL and cluster are retained as in PR mode.

An AWX checkout supplies the AWX source directly. An awx-plugins checkout supplies the plugin wheel, using the environment's baseline AWX ref (or `--awx-ref REF` for that invocation). Local mode tests the working snapshot as-is, without fetching/merging a target branch. It cannot be combined with a PR URL or `--plugins-pr`; an optional base-merge flag is not implemented. Upstream companion AWX sources, build dependencies and images may still require network access.

Validation rejects a missing/wrong branch, detached HEAD, a branch without commits, unresolved conflicts, unsupported repositories, or commands outside a Git working directory. It recognizes repository contents rather than requiring a particular directory name or remote URL. Normal Git worktrees are supported. Sparse checkouts, submodules, nested repositories and included special files are not supported. Symlinks must be relative and stay inside the source checkout, outside `.git`. The source checkout and state directory must be separate. If the branch or files change during snapshot capture, the attempt fails before building; save your edits and retry.

### Test PR changes

```bash
# AWX PR, merged with its own latest target branch.
awx-lab test https://github.com/ansible/awx/pull/123

# Plugin PR, built into the environment's baseline AWX ref.
awx-lab test https://github.com/ansible/awx-plugins/pull/456

# Select a companion AWX ref for this plugin test only.
awx-lab test https://github.com/ansible/awx-plugins/pull/456 --awx-ref devel

# Test related AWX and plugin PRs together.
awx-lab test https://github.com/ansible/awx/pull/123 \
  --plugins-pr https://github.com/ansible/awx-plugins/pull/456

# Add an Ansible localhost ping job, or force a fresh AWX/UI build.
awx-lab test https://github.com/ansible/awx/pull/123 --suite job
awx-lab test https://github.com/ansible/awx/pull/123 --no-cache
```

`test` requires a running, bootstrapped environment. Run `up` first, including after `down`. Push changes to the PR, then repeat the same test command to fetch its latest commits and latest target branch. Merge conflicts fail the attempt. Nothing is pushed to GitHub.

Switching PRs replaces the previous PR's source changes. Omitting `--plugins-pr` removes a previous custom companion plugin override from the next build. Plugin-only tests use the baseline AWX ref chosen at creation, unless overridden for that invocation. If the source selection is unchanged, the image build and rollout are skipped and checks run again. `--no-cache` forces a rebuild.

The cluster, Operator, URL, credentials and database are reused. AWX migrations may change retained data; switching back to an older PR is not a database rollback. For a fresh database, destroy the lab with `down --purge`, then create it again with `up`.

### Stop or destroy

| Command | What happens | What remains |
| --- | --- | --- |
| `awx-lab down` | Stop this lab's kind node, including Kubernetes, AWX and PostgreSQL | Database, images, sources, reports and logs for resume |
| `awx-lab up` after stopping | Restart the same node and wait for Kubernetes/AWX readiness | Same database, current image, credentials and URL |
| `awx-lab down --purge` | Delete this cluster/database, tracked lab AWX/UI image tags, and workspace | Shared helper images/build cache, downloaded tools and runtime VM |

Stop verifies that the node stopped. Resume failure preserves the environment instead of silently recreating it. Purge failure keeps saved state for retry. Both stop and purge leave the shared Podman/Docker VM running; on macOS, stop that VM separately to release its reserved host RAM when no other workloads need it. AWX Lab does not stop unrelated containers or globally prune images.

## Command reference

Help is available without a running container runtime. The root page lists all
commands and the common workflow; each command's help includes its flags,
defaults, examples and relevant limitations:

```bash
awx-lab --help
awx-lab up --help
awx-lab test --help
awx-lab prereq install --help
awx-lab down --help
```

The same `--help` flag works on `doctor`, `plan`, `prereq`, `status`, `logs` and
`credentials`. Global options such as `--state-dir` go before the command.

Use `awx-lab --help` or `awx-lab <command> --help` for installed-version help. Use `awx-lab prereq install --help` for installer options.

| Command | Purpose and options |
| --- | --- |
| `awx-lab --version` | Print the installed CLI version |
| `awx-lab doctor` | Read-only prerequisite checks; `--runtime auto\|podman\|docker`, `--json` |
| `awx-lab prereq install` | Check, request approval, install and verify; see [installer options](#prerequisite-installer) |
| `awx-lab plan [PR_URL]` | Preview baseline setup or a PR test; `--json` and the build/test options below |
| `awx-lab up` | Create or resume the lab; creation options below and [log flags](#logs-and-reports) |
| `awx-lab test <PR_URL>` | Test an open AWX/plugin PR; `--plugins-pr`, `--awx-ref`, `--suite`, `--no-cache`, log flags |
| `awx-lab test --local [BRANCH]` | Test the current AWX/plugin working checkout including uncommitted edits; `--awx-ref` for plugins, `--suite`, `--no-cache`, log flags |
| `awx-lab status` | Show saved state, not live health; `--json`, `--all` for legacy environments |
| `awx-lab credentials` | Read the running lab's URL and generated admin username/password |
| `awx-lab logs` | Print the saved current log; `--follow` for live output across rotation |
| `awx-lab down` | Stop and retain the lab; `--purge` to destroy it, plus log flags |

`plan` does not fetch the PR, check mergeability, build an image, create files or change the environment. Its output describes the workflow and supplied options, not a live readiness assessment. For example: `awx-lab plan https://github.com/ansible/awx/pull/123 --json`.

### Creation and test options

| Option | Commands | Default / meaning |
| --- | --- | --- |
| `--runtime auto\|podman\|docker` | `up`, `plan` | `auto`: prefer reachable Podman, then Docker |
| `--awx-ref REF` | `up`, `plan`, `test` | `devel` for creation; for plugin tests, the saved baseline ref. Branch, tag or SHA. Not accepted on an AWX-primary `test` |
| `--operator-version VERSION` | `up`, `plan` | `devel`; selects Operator manifests and image. A release tag can be supplied for a compatible older AWX |
| `--node-image IMAGE` | `up`, `plan` | `kindest/node:v1.34.0` |
| `--ee-image IMAGE` | `up`, `plan` | `quay.io/ansible/awx-ee:24.6.1` |
| `--port PORT` | `up`, `plan` | Choose a free loopback HTTP port; explicit values must be 1024–65535 |
| `--headless` | `up`, `plan` | Off; skip UI build for an API-only lab |
| `--build-timeout SECONDS` | `up`, `plan` | `7200` per build/helper command, not a total run limit |
| `--ready-timeout SECONDS` | `up`, `plan` | `900` for deployment readiness waits |
| `--plugins-pr URL` | `test`, `plan` | Optional awx-plugins companion PR; requires an AWX primary PR |
| `--local [BRANCH]` | `test` | Use the current checkout including uncommitted changes; optional branch must exist locally and be checked out. Mutually exclusive with a PR URL |
| `--suite smoke\|job` | `test`, `plan` | `smoke`; `job` adds an Ansible localhost ping |
| `--no-cache` | `up`, `test`, `plan` | Off; rebuild AWX/UI without cached layers. On `up`, only before a baseline image has been built |
| `--json` | `plan`, `doctor`, `status` | Machine-readable output |

Runtime, baseline ref, images, port, headless mode and timeouts belong to the first `up`. Later `up` commands use the saved settings and reject conflicting explicit values; changing the environment configuration requires purge/recreation. `test` uses those saved settings, with only the test options listed above. `up --no-cache` does not refresh an existing built baseline; use `test <PR_URL> --no-cache` or `test --local --no-cache`.

The default development Operator was selected for compatibility with the current AWX build. Arbitrary AWX/Operator/image combinations are not guaranteed. See [validation status](docs/validation.md) for observed compatibility issues.

### State location and environment variables

| Setting | Behavior |
| --- | --- |
| `--state-dir PATH` | Global flag, **before** the subcommand; overrides the state directory |
| `AWX_LAB_HOME` | State directory when `--state-dir` is omitted; default `~/.local/share/awx-lab/runs` |
| `GITHUB_TOKEN` / `GH_TOKEN` | Optional GitHub API authentication for rate limits; `GITHUB_TOKEN` takes precedence |

For example, `awx-lab --state-dir /path/to/lab-state status` uses that location. Keep using the same setting for later commands; changing it selects a different state location, not a migration of the existing lab. Downloaded tools remain in their standard location. Tokens are not forwarded into build subprocesses or container images and do not enable private-repository builds.

One environment is selected per state directory. Locks prevent concurrent create/test/stop/cleanup commands from different terminals from changing the same lab. Reading logs from another terminal is supported.

## What a passing result means

A successful default test produces output like this (paths, PR number and port vary):

```text
Testing https://github.com/ansible/awx/pull/123
Detailed output: awx-lab logs --follow

== API smoke checks ==
PASS API ping
PASS admin authentication
PASS credential type registration

Result: ready
Reports: /home/developer/.local/share/awx-lab/runs/awx-lab-...
AWX: http://127.0.0.1:55215
Credentials: awx-lab credentials
Next test: awx-lab test <PR_URL>
Stop: awx-lab down
Destroy: awx-lab down --purge
```

Before checking the API, the CLI waits for both AWX web and task deployments to run the requested image and become ready, including migrations. Image import or rollout failure stops the test; an older healthy deployment cannot stand in for the requested image.

| Check | What it establishes |
| --- | --- |
| API ping | AWX responds and reports a version |
| Admin authentication | The generated admin credentials authenticate successfully |
| Credential type registration | AWX reports at least one registered credential type; not that every expected plugin works |
| Installed plugin wheel hash, for plugin tests | The wheel built from the PR merge or local snapshot matches pip metadata in the running AWX task container |
| Ansible localhost ping, with `--suite job` | A real ad hoc Ansible ping job completes inside its execution environment |

For plugin tests, the CLI builds a wheel from the PR merge or local snapshot, installs it after AWX's base dependencies while preserving dependency extras, and adds `PASS installed plugin wheel hash` after verification. The job suite adds `PASS Ansible localhost ping job`; its organization, inventory, host and job are retained for inspection.

**`Result: ready` means the environment is ready and the requested built-in checks passed.** It does not establish that the PR's feature is correct. These checks do not run AWX's full unit/integration suites, exercise every plugin, or contact a cloud service to validate its credentials. Supply your test credentials/data and run the feature-specific scenarios in AWX. This is the handoff point after the setup/build steps in Bill's guide; see [the design mapping](docs/design.md).

Successful lifecycle commands exit `0`; operational failures exit `1`, CLI argument errors `2`, and interruption `130`. The report records a successful test as `last_test_result: passed` while the environment status is `ready`.

## Logs and reports

| Mode | Terminal output |
| --- | --- |
| Default | Test/check progress, PASS lines and result summary; routine fetch/build/deploy output is hidden. Warnings and failures remain visible |
| `--error-log` | Reported failures and diagnostic context, including setup failures; silent on success |
| `--full-log` | Verbose commands and output, including environment setup and image builds |

```bash
awx-lab test https://github.com/ansible/awx/pull/123 --error-log
awx-lab test https://github.com/ansible/awx/pull/123 --full-log
awx-lab logs
awx-lab logs --follow
```

The flags are mutually exclusive and supported on `up`, `test` and `down`. Put them after the subcommand. They apply only to that invocation and do not change execution, exit status or saved preferences. The prerequisite installer always shows its plan/approval prompt and does not accept these flags.

All modes save unfiltered logs, subject to rotation and sensitive-output suppression. `logs` and `logs --follow` read that saved output without filtering. Read rotated files directly if the older output is no longer in `run.log`.

The printed `Reports:` directory contains:

| Path | Contents |
| --- | --- |
| `report.json` | Result, source revisions or local snapshot identity/dirty state, image ID, plugin wheel hash when applicable, and stage timings |
| `junit.xml` | Lifecycle/check stage results for report consumers |
| `state.json` | Saved lifecycle state; also used by `status` |
| `run.log`, `run.log.1`–`run.log.3` | Recent command/build/test output, newest file first |
| `diagnostics/` | Pod status, events, Operator/AWX logs from a failed attempt, when Kubernetes is reachable |
| `history/<attempt-id>/` | Archived states, reports and diagnostics from earlier attempts |
| `kubeconfig`, `awx/`, `awx-plugins/`, `awx-operator/` | Private cluster configuration and managed source checkouts |

Failures and Ctrl-C retain the environment and attempt diagnostics. A fetch/build failure leaves the previous deployment available if it was running. A failed/interrupted rollout may leave a partially updated deployment; inspect the error and use `up` or another `test` to recover. The CLI does not promise rollback, and migrations may already have changed the database. An AWX URL printed in a failed summary alone does not prove readiness.

## Local changes, caching and retention

Running a test changes both managed files on your computer and container storage. It does not install AWX's application dependencies into your system Python or modify your separate development checkout.

| Location | What changes |
| --- | --- |
| Host state directory | Source checkouts, generated build/deployment files, credentials/configuration, logs and reports |
| Podman/Docker storage | Built AWX/UI/helper images and dependency layers |
| kind node's internal container storage | Imported application images, Kubernetes workloads and persistent PostgreSQL data |

Build helpers mount the managed source directories read/write to generate build inputs and wheels. Each test replaces their working files while retaining Git objects; edits made inside them will be discarded. Use `test --local` from your own development checkout for uncommitted work, or push changes and test the PR. The running AWX application does not use a live source mount. Switching between local and PR mode replaces the previous source selection; old snapshot files and custom plugin overrides do not leak into subsequent AWX-only builds.

The first build downloads dependencies. Later builds reuse image layers and a separately cached compiled UI. Backend-only Python changes and plugin-only changes can reuse that UI; other tracked AWX changes invalidate it conservatively. The cached UI retains the external `ansible-ui` revision fetched when it was built. Use `--no-cache` to rebuild and refresh mutable upstream inputs. Changed application code still requires an image build and rollout.

| Artifact | Retention |
| --- | --- |
| AWX/UI images | After successful `up`/`test`, keep current and previous successful build pairs; remove older tracked, unused lab tags from runtime and kind |
| Pending/failed images | Retained for inspection; later successful cleanup retries removal when safe |
| Report/diagnostic history | Latest 10 archives; older archives removed after attempts, including failures |
| Disk logs | 10 MiB per file, with 3 backups: up to 40 MiB total |
| Database | Retained across PRs and stops; deleted by purge |
| Shared helper images, dependency cache and Git objects | Reused; not subject to a fixed size limit |

Active/pending workloads, shared aliases and cleanup failures can keep additional images. Retention warnings appear in the terminal and report without turning a passed test into a failure. Previous-success image retention applies from version 0.4.5 onward and is not a database restore feature.

These policies limit lab artifacts, **not total disk usage**. Shared caches, Git objects and test data can grow; repeated failed builds can accumulate until a successful cleanup. No global image/volume/cache prune is performed. Purge removes this lab's tracked resources and workspace, while shared caches and tools remain. Freeing space inside a macOS VM may not immediately shrink its host disk file.

When upgrading from 0.3, a single retained legacy environment is selected automatically. `status --all` lists legacy environments if more than one exists. Optional legacy IDs are still accepted by `logs`, `credentials` and `down` for inspecting/removing those environments; ordinary use never needs an ID. The old `test --keep` / `test --reuse` workflow has been replaced by `up`, `test` and `down`.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `awx-lab` is not found | Run `pipx ensurepath`, open a new terminal and check `pipx list` |
| Missing Git/kind/kubectl/runtime | Run `awx-lab prereq install --dry-run`, then `awx-lab prereq install` to approve the plan |
| Podman says running, but connection fails | Check `podman machine list`, `podman system connection list` and `podman info`; rerun `doctor --runtime podman` once the API is ready |
| Low-memory failure / build killed | Increase runtime RAM; the installer can resize an undersized stopped Podman VM. A running VM is never resized automatically |
| Linux rootless kind fails | Review [kind's rootless setup](https://kind.sigs.k8s.io/docs/user/rootless/); passing `doctor` does not validate every kernel/cgroup requirement |
| No environment, or environment stopped | Run `awx-lab up` before `test` or `credentials` |
| Another lab command holds the lock | Let that operation finish; use `awx-lab logs --follow` to inspect progress |
| GitHub metadata, closed PR or merge conflict error | Use an open public PR, resolve conflicts/push updates, and retry. An optional GitHub token can help with API rate limits |
| No new build on repeated `test` | Unchanged sources intentionally reuse the image. Push new commits, or use `--no-cache` for a forced rebuild |
| Local branch missing, wrong branch, or outside a repository | Run from your AWX/awx-plugins checkout on the desired branch. `test --local` uses that branch automatically |
| Local edits not reflected | Save the file to disk, check that it is tracked or not ignored, then rerun `test --local`. Changes are captured per invocation, not live-mounted |
| Build/readiness failure | Read the reported stage, `awx-lab logs` and the report/diagnostics directory. Retry with `--full-log` after addressing the cause |
| Need a fresh database or different creation settings | Save any wanted reports/data, then use `awx-lab down --purge` and `awx-lab up` with the new settings |

On macOS Podman, AWX Lab retries transient API/SSH connection failures up to five attempts within a 30-second probe window when the selected local VM is running or starting. It does not restart the VM or change connections automatically during `doctor`, `up` or `test`. Persistent failures include connection/VM details; stopped VMs, authentication errors and overridden/remote connections need manual attention.

When reporting an issue, include the CLI version, OS/architecture, runtime, failing stage and relevant report/log excerpt. Review diagnostics for credentials or application data before sharing them.

## Current limits

- PR mode supports public GitHub PRs in repositories named `awx` or `awx-plugins`, including forks. Private-repository fetching and GitHub Enterprise are not supported. Local mode reads an existing AWX/plugin checkout without fetching it from its remote, including unpushed and uncommitted work.
- This is a local development/testing lab. Shared-server scheduling, GitLab orchestration, automatic PR/label triggers and a management web interface are not included.
- Tests reuse one selected environment. Full AWX regression suites, feature-specific test scenarios, database snapshots and migration rollback are not implemented.
- Build inputs can include mutable upstream branches/tags. Recorded PR SHAs, wheel hashes and image IDs aid diagnosis but do not make the whole build reproducible.
- AWX is exposed on loopback over HTTP for local testing. PR builds execute supplied code in your local runtime, and kind nodes require runtime privileges; use PRs you trust.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m build
python scripts/check_install.py dist/awx_lab-0.5.0-py3-none-any.whl
```

Tests use the standard library and temporary local Git repositories; they do not need a container runtime. With `kubectl` on PATH, they also render the generated Operator overlay with real Kustomize. To include installer-managed tools, use `PATH="$HOME/.local/share/awx-lab/bin:$PATH" python -m unittest discover -s tests -v`.

The included GitHub Actions workflow runs CLI tests on Linux/macOS, builds a wheel from the source distribution, and verifies installation, assets, cross-directory state discovery and uninstall in a fresh environment. It does not perform full AWX deployments.

`./awx-lab` runs from the checkout without installation. `pipx install --editable .` is available for development and reflects source edits immediately. The version lives in `awx_lab/__init__.py`; package metadata and `awx-lab --version` use that value.

See [design and Bill's baseline](docs/design.md), [validation and acceptance checks](docs/validation.md), and [the project backlog](docs/backlog.md).
