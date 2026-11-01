"""Terminal help for the public CLI; kept separate from command dispatch."""

import argparse


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Wrap prose descriptions while preserving multiline guides and examples."""

    def _fill_text(self, text, width, indent):
        if "\n" not in text:
            return argparse.HelpFormatter._fill_text(self, text, width, indent)
        return super()._fill_text(text, width, indent)


ROOT_DESCRIPTION = """Build and reuse a local AWX lab with kind and AWX Operator.
Run awx-lab without a command for the interactive console and live logs.
Test GitHub PRs or your uncommitted AWX/awx-plugins changes, then open AWX
for manual feature testing. One environment per state directory; no run ID
is needed for the normal workflow."""

ROOT_EPILOG = """Quick start:
  awx-lab                           Open the terminal interface
  awx-lab prereq install             Check, approve, install and verify tools
  awx-lab up                         Create baseline AWX, or resume the lab
  awx-lab test --local               Test saved edits from your source checkout
  awx-lab test --local <BRANCH>      Test edits on this checked-out branch
  awx-lab test <PR_URL>              Test a PR merged with its latest target
  awx-lab credentials                Show the AWX URL and admin login
  awx-lab logs --follow              Watch detailed output in another terminal
  awx-lab down                       Stop and retain the database and files
  awx-lab down --purge               Destroy the lab and its data

Local branches:
  --local <BRANCH> requires that branch to exist locally and be checked out.
  Includes saved uncommitted changes; it does not switch branches.

Interactive terminal:
  Left: AWX Lab commands, concise progress and login details. Right: live logs.
  Type up, test <PR_URL>, test --local, credentials or down without the prefix.
  Tab switches panes; F2 completes; arrows recall history or scroll logs.
  PgUp pauses log following; End in logs or follow resumes it.
  Ctrl+C cancels the command. exit or Ctrl+Q leaves the view, keeping the lab;
  active work requires cancel/stay. Narrow terminals show one pane at a time.
  The launch directory is used by test --local. Type help for all controls.
  No terminal attached: bare awx-lab prints help instead. Direct commands work
  normally in scripts. Use --state-dir PATH before entering the interface.

Help:
  awx-lab <command> --help           All flags, defaults and examples
  awx-lab prereq install --help      Installer and Podman VM options

Terminal output (up, test, down):
  Default: setup stages, elapsed updates, check results, warnings and failures.
  --error-log: failures and diagnostic context only; silent on success.
  --full-log: include setup, fetch, build and deployment output.
  Successful up/test prints the URL, username and password (except error mode).
  Detailed logs include the login in every mode; read them with awx-lab logs.

Host requirements:
  Targets macOS ARM64 and Linux x86_64; Python 3.11+, Git, kind, kubectl,
  and a working native Podman or Docker runtime. Plan for 4-8 CPUs,
  12-16 GiB runtime RAM and 40+ GiB workspace disk, plus runtime storage.
  GitHub, package mirrors and container registries require network access.
  Platform acceptance status is documented in docs/validation.md.

State and environment variables:
  --state-dir PATH goes BEFORE the command and overrides AWX_LAB_HOME.
  Default: ~/.local/share/awx-lab/runs. Changing it selects another location;
  it does not move an existing lab. Reuse the same setting on later commands.
  GITHUB_TOKEN or GH_TOKEN optionally authenticates GitHub API requests;
  GITHUB_TOKEN takes precedence. Private PR builds are not supported.

Exit codes: 0 success, 1 operational failure, 2 argument error, 130 interrupted.
Full guide: https://github.com/eco-ansible-content/awx-lab#readme"""

LOGGING = """Terminal output:
  Default: setup stages, completion times and elapsed updates every 30 seconds
  during long steps, plus check results, warnings and failures.
  --error-log and --full-log are mutually exclusive and affect only the terminal.
  Successful up/test includes the URL, username and password in its summary.
  --error-log stays silent on success; saved logs still include the login.
  Detailed output is saved in every mode: awx-lab logs [--follow]."""

# Description and epilog for each help page. Argument details come from argparse.
COMMAND_HELP = {
    "doctor": (
        "Check host prerequisites and runtime connectivity without installing, "
        "starting or reconfiguring anything.",
        """Checks:
  Python, OS/architecture, Git, kind, kubectl, runtime connectivity and reported
  RAM, workspace disk, and cgroup v2 when reported by rootless Podman.
  Requires at least 8 GiB runtime RAM (allows 512 MiB guest overhead);
  recommends 12-16 GiB and 40+ GiB workspace disk. Total RAM is checked,
  not currently available RAM or free space inside a macOS runtime VM.
  This is not an AWX health check or a complete rootless host validation.
  Failed required checks exit 1; resource warnings alone do not fail.

Examples:
  awx-lab doctor
  awx-lab doctor --runtime podman --json
  awx-lab prereq install             Install missing prerequisites""",
    ),
    "prereq": (
        "Manage the host tools and runtime needed to create a local AWX lab.",
        """Examples:
  awx-lab prereq install --dry-run   Check and preview proposed changes
  awx-lab prereq install             Ask approval, install, then verify
  awx-lab prereq install --help      All installer and VM options""",
    ),
    "install": (
        "Run doctor, list missing prerequisites and proposed changes, ask for "
        "Y/n approval, install/configure them, then run doctor again.",
        """Approval:
  Enter, Y or yes approves; n, no or end-of-input cancels. No --yes flag.
  --dry-run checks and previews only; it does not prove installation will work.
  Completed installation steps are retained if a later step fails.

Managed prerequisites:
  Downloads kind/kubectl with SHA256 verification to ~/.local/share/awx-lab/bin.
  AWX Lab discovers this directory; existing tools on PATH take precedence.
  Git/Podman packages use Homebrew, DNF or APT. Linux packages may need sudo.
  Docker installation uses Homebrew cask or APT; install it manually on DNF.
  Python 3.11+, pipx and (on macOS) Homebrew must already be installed.
  Kernel configuration and runtime/login permissions may need manual setup.

Podman on macOS:
  Reuses/starts the selected local VM or creates one if absent. CPU and disk
  options apply to new VMs only. Memory can also grow a stopped VM below 8 GiB.
  Running VMs are never resized or restarted; larger allocations are kept.
  The default machine is reused, or a new awx-lab machine is proposed.
  Any proposed default-connection change is included in the approval plan.

Examples:
  awx-lab prereq install --dry-run
  awx-lab prereq install --runtime podman
  awx-lab prereq install --runtime podman --machine awx-lab --cpus 6 \\
    --memory 16384 --disk-size 80
  awx-lab up                         Create the lab after checks pass""",
    ),
    "plan": (
        "Preview baseline creation (no PR) or testing an open public GitHub PR. "
        "Print the conceptual steps without executing commands or creating files.",
        """Scope:
  Does not fetch sources, verify mergeability, check readiness or change a lab.
  --json also includes the supplied options and native architecture.
  Creation options describe a prospective lab; test reuses its saved settings.
  --plugins-pr requires an AWX primary PR. --awx-ref selects the baseline for
  creation/plugin tests; an AWX PR uses its own target branch.
  Local checkout planning is not supported; use awx-lab test --help for --local.

Examples (replace PR numbers with open PRs):
  awx-lab plan
  awx-lab plan --runtime podman --headless --json
  awx-lab plan https://github.com/ansible/awx/pull/123 --suite job
  awx-lab plan https://github.com/ansible/awx/pull/123 \\
    --plugins-pr https://github.com/ansible/awx-plugins/pull/456 --json""",
    ),
    "up": (
        "Create baseline AWX without a PR, or start/check the existing lab. "
        "The first run builds AWX and creates kind, AWX Operator, PostgreSQL "
        "and an admin account.",
        """Reuse and configuration:
  Later up calls preserve the current tested image, URL, credentials and data.
  They do not fetch a newer baseline or rebuild an already-built image.
  They repair older labs' IPv4 web settings and HTTP readiness probes; the
  Operator may replace AWX pods while keeping the image and database.
  Retry up after an interrupted setup or pending deployment to finish it.
  Creation settings are saved; conflicting options on resume are rejected.
  Changing them requires down --purge, then up with the new settings.
  --no-cache is only accepted before the baseline image has been built;
  use test <PR_URL> --no-cache or test --local --no-cache for later rebuilds.
  Failures retain the environment and diagnostic reports for investigation.

After success:
  Open the printed loopback HTTP URL and use the displayed username/password.
  The login is also saved in logs; awx-lab credentials retrieves it again.
  API checks establish readiness; test your feature manually in AWX.

Examples:
  awx-lab up
  awx-lab up --runtime podman --port 8080 --full-log
  awx-lab up --headless              Create an API-only lab
  awx-lab test --local               Then test from your source checkout
  awx-lab down                       Stop; another up resumes the lab

""" + LOGGING,
    ),
    "test": (
        "Build and test a PR or local working files in the existing AWX lab. "
        "Run awx-lab up first; the environment must be running and bootstrapped.",
        """Choose PR mode or local mode:
  PR mode accepts an AWX PR, an awx-plugins PR, or BOTH together:
    AWX only:     test <AWX_PR_URL>
    Plugins only: test <PLUGIN_PR_URL> [--awx-ref REF]
    Both:         test <AWX_PR_URL> --plugins-pr <PLUGIN_PR_URL>
  Use full URLs of open public GitHub PRs (including forks). Each PR is merged
  with its own latest target branch; conflicts fail the attempt. With both
  PRs, the plugin wheel is built into the AWX PR image. Plugin-only mode uses
  the saved baseline AWX ref unless --awx-ref overrides it for this invocation.

  --local [BRANCH]: run from your AWX or awx-plugins checkout or a subdirectory.
  Uses the current branch; an optional branch must exist locally and already
  be checked out. Save edits first: staged/unstaged edits, deletions and new
  non-ignored files on disk are included. Ignored untracked files are excluded.
  Builds a separate snapshot without changing your files, branch or index.
  No commit, push or PR is needed; no target branch is fetched or merged.
  Local mode uses ONE checkout; two local checkouts together are unsupported.
  Cannot combine --local with PR_URL or --plugins-pr. A local plugin checkout
  uses the saved baseline AWX ref, or --awx-ref for this invocation.

Local validation:
  Rejects non-Git/unsupported repos, missing/wrong branches, detached/unborn
  HEAD, merge conflicts, sparse checkouts, submodules and nested repositories.
  Included special files and symlinks outside the checkout are unsupported.
  Keep the source checkout separate from the lab state directory.
  Companion sources, dependencies and images may still need network access.

Reuse and checks:
  Reuses the cluster, URL, credentials and database. Changed sources rebuild;
  identical sources reuse the image and rerun checks. --no-cache forces rebuild.
  Repairs IPv4 web settings and waits for HTTP readiness on older labs too.
  Database migrations may change data; failed updates are not rolled back.
  Smoke checks cover API version, admin login and credential type registration.
  Plugin tests also verify the installed wheel hash. --suite job adds an
  Ansible localhost ping inside the execution environment and retains its
  inventory/job for inspection. This is not the full AWX regression suite.
  Result: ready means these checks passed; feature testing remains manual.
  On failure, inspect awx-lab logs and the printed Reports directory.
  report.json and junit.xml record results; available diagnostics are retained.

Examples (replace PR numbers with open PRs):
  awx-lab test --local
  awx-lab test --local my-feature
  awx-lab test --local my-feature --suite job
  awx-lab test --local --no-cache --full-log
  # AWX PR only
  awx-lab test https://github.com/ansible/awx/pull/123
  # Plugin PR only, using a companion AWX ref
  awx-lab test https://github.com/ansible/awx-plugins/pull/456 --awx-ref devel
  # AWX and plugin PRs together in one image
  awx-lab test https://github.com/ansible/awx/pull/123 \\
    --plugins-pr https://github.com/ansible/awx-plugins/pull/456

""" + LOGGING,
    ),
    "status": (
        "Show the selected environment's saved status and URL. "
        "This reads saved state, not the live health of AWX or its containers.",
        """Examples:
  awx-lab status
  awx-lab status --json              JSON array of saved environment records
  awx-lab status --all               Include legacy environments and IDs

Legacy environments:
  If several older environments prevent automatic selection, inspect --all
  and remove extras with awx-lab down <legacy-id> --purge.
  Normal use selects one environment automatically, with no run ID.""",
    ),
    "logs": (
        "Print the selected environment's current detailed build/test log. "
        "Use --follow to keep streaming new output, including across log rotation.",
        """Log behavior:
  Saved output includes setup/build/deployment details regardless of the
  terminal mode used for up/test/down. This command does not filter errors.
  Successful up/test summaries and credentials commands save the admin login.
  Logs rotate at 10 MiB with three backups (up to about 40 MiB per lab).
  Only the current file is printed; older files are in the Reports directory.
  Reading logs is supported while a build/test runs in another terminal.
  Ctrl-C stops following the log; it does not stop the build or environment.

Examples:
  awx-lab logs
  awx-lab logs --follow
  awx-lab logs > awx-lab.log""",
    ),
    "credentials": (
        "Read the generated admin credentials from the environment's Kubernetes "
        "secret, print the AWX URL, username and password, and save them in the log.",
        """Requirements:
  The lab must exist and be running. If stopped, run awx-lab up first.
  Open the printed loopback HTTP URL and sign in as admin with this password.

Example:
  awx-lab credentials""",
    ),
    "down": (
        "Stop the environment's kind node, retaining its database and files. "
        "Use --purge to permanently destroy the environment and its data.",
        """Stop versus purge:
  down: stops AWX, PostgreSQL and Kubernetes; keeps disk data, images and
  reports so awx-lab up can resume the same environment.
  down --purge: deletes the cluster/database, tracked AWX/UI images, managed
  sources, logs and reports. The next up creates a fresh environment.
  Shared helper/build caches, downloaded tools and the runtime VM remain.
  Stopping a lab does not shut down Podman/Docker's VM or free all VM memory.
  Cleanup failures retain state so you can inspect and retry. If no lab
  exists, down succeeds without doing anything.

Examples:
  awx-lab down
  awx-lab up                         Resume the retained lab
  awx-lab down --purge --full-log     Destroy it with detailed cleanup output

""" + LOGGING,
    ),
}
