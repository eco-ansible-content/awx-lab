"""Native container builds, using AWX's own Dockerfile renderer."""

from __future__ import annotations

import hashlib
from pathlib import Path
import os
import platform
import re
import shlex
import shutil
import stat
import tempfile

from .system import LabError
from .source import git


def inject_plugin(awx: Path, wheel: Path):
    requirements = awx / "requirements/requirements_git.txt"
    original = read_regular(requirements, awx).decode()
    pattern = re.compile(r"^(awx-plugins-core(?:\[[^\]\n]+\])?)\s*@[^\n]+$", re.MULTILINE)
    matches = list(pattern.finditer(original))
    if len(matches) != 1:
        raise LabError("Expected one awx-plugins-core dependency in requirements/requirements_git.txt; this AWX revision needs an updated adapter.")
    if not re.fullmatch(r"awx_plugins_core-[A-Za-z0-9_.+-]+\.whl", wheel.name):
        raise LabError(f"Unexpected plugin wheel filename: {wheel.name}")
    if wheel.parent.is_symlink() or wheel.is_symlink() or not wheel.is_file():
        raise LabError("Plugin build did not produce a regular wheel file.")
    copied = awx / "requirements" / wheel.name
    if copied.exists() or copied.is_symlink():
        raise LabError(f"Refusing to replace an existing wheel destination: {copied}")
    digest = hashlib.sha256()
    source_fd = os.open(wheel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise LabError("Plugin build did not produce a regular wheel file.")
        target_fd = os.open(copied, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(source_fd, "rb", closefd=False) as source, os.fdopen(target_fd, "wb") as target:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
        except BaseException:
            copied.unlink(missing_ok=True)
            raise
    finally:
        os.close(source_fd)
    dockerfile = awx / "Dockerfile"
    contents = read_regular(dockerfile, awx).decode()
    # Keep the expensive requirements layer stable across plugin-only updates.
    # pip installs the exact local wheel and resolves any changed dependencies.
    marker = re.compile(r"^RUN[^\n]*\bmake requirements_awx\s*$", re.MULTILINE)
    if len(marker.findall(contents)) != 1:
        raise LabError("AWX's generated Dockerfile has changed: cannot locate its dependency installation step.")
    extras = matches[0].group(1).removeprefix("awx-plugins-core")
    wheel_path = shlex.quote(f"/tmp/awx-lab/{wheel.name}{extras}")
    pip = "/var/lib/awx/venv/awx/bin/pip"
    addition = (f"\nCOPY requirements/{wheel.name} /tmp/awx-lab/{wheel.name}\n"
                f"RUN {pip} uninstall --yes awx-plugins-core && {pip} install --no-cache-dir {wheel_path}\n")
    contents = marker.sub(lambda match: match[0] + addition, contents)
    write_regular(dockerfile, awx, contents)
    return {"filename": wheel.name, "sha256": digest.hexdigest()}


def read_regular(path: Path, root: Path):
    """Read a helper-produced file without traversing links into host files."""
    if root.is_symlink() or path.parent.is_symlink():
        raise LabError(f"Build path is a symlink: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise LabError(f"Build output is not a regular file: {path}")
        return handle.read()


def write_regular(path: Path, root: Path, contents: str):
    if root.is_symlink() or path.parent.is_symlink():
        raise LabError(f"Build path is a symlink: {path}")
    fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "w") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise LabError(f"Build output is not a regular file: {path}")
        os.ftruncate(handle.fileno(), 0)
        handle.write(contents)


def ui_cache_key(runner, awx, stage, arch):
    entries = git(runner, awx, "ls-tree", "-r", "-z", "HEAD", log_output=False).split("\0")
    inputs = []
    for entry in entries:
        if not entry:
            continue
        path = entry.split("\t", 1)[1]
        # Conservatively invalidate on all changes except backend Python files.
        if path.startswith("awx/") and not path.startswith("awx/ui/") and path.endswith(".py"):
            continue
        inputs.append(entry)
    return hashlib.sha256((arch + "\n" + stage + "\n" + "\0".join(inputs)).encode()).hexdigest()[:16]


class Builder:
    def __init__(self, runner, runtime, path, arch, timeout=7200, record_image=None):
        self.runner, self.runtime, self.path, self.arch = runner, runtime, path, arch
        self.timeout = timeout
        self.record_image = record_image or (lambda image: None)
        self.assets = Path(__file__).parent / "assets"
        digest = hashlib.sha256((self.assets / "Containerfile").read_bytes()).hexdigest()[:12]
        self.tools_image = f"localhost/awx-lab-tools:{digest}-{arch}"

    def tools(self):
        self.runner.run([self.runtime, "build", "--platform", f"linux/{self.arch}", "-t", self.tools_image,
                         "-f", self.assets / "Containerfile", self.assets], timeout=self.timeout)

    def in_tools(self, source, command, capture=False):
        flags = []
        suffix = ""
        if self.runtime == "podman":
            flags += [f"--userns=keep-id:uid={os.getuid()},gid={os.getgid()}"]
            if platform.system() == "Darwin":
                # Podman machine's macOS shares cannot reliably be relabeled.
                # Disable SELinux separation only for this temporary helper.
                flags += ["--security-opt", "label=disable"]
                if self.arch == "arm64":
                    # Older Apple Hypervisor VMs can misreport ARM extensions,
                    # crashing cryptography/OpenSSL before Ansible starts.
                    flags += ["--env", "OPENSSL_armcap=0"]
            else:
                suffix = ":Z"
        name = self.path.name + "-tools"
        try:
            return self.runner.run([self.runtime, "run", "--rm", "--name", name, *flags,
                                    "--user", f"{os.getuid()}:{os.getgid()}",
                                    "--volume", f"{source}:/src{suffix}", self.tools_image, *command],
                                   capture=capture, timeout=self.timeout)
        except BaseException:
            # A remote runtime may leave the helper running after its client is stopped.
            try:
                self.runner.run([self.runtime, "rm", "--force", name], capture=True, check=False, timeout=30)
            except (LabError, KeyboardInterrupt):
                self.runner.write(f"Could not remove helper {name}; remove it with '{self.runtime} rm --force {name}'.\n", level="error")
            raise

    def build(self, image, plugin=False, no_cache=False, headless=False):
        # Helpers run code from a PR/local checkout. Give them a disposable copy:
        # host-side Git continues to use the checkout that was never mounted.
        with tempfile.TemporaryDirectory(prefix=".build-", dir=self.path) as temporary:
            stage = Path(temporary)
            awx = stage / "awx"
            shutil.copytree(self.path / "awx", awx, symlinks=True)
            if plugin:
                shutil.copytree(self.path / "awx-plugins", stage / "awx-plugins", symlinks=True)
            return self._build_staged(image, awx, stage / "awx-plugins", plugin, no_cache, headless)

    def _build_staged(self, image, awx, plugin_source, plugin, no_cache, headless):
        self.tools()
        self.in_tools(awx, ["make", "Dockerfile", f"HEADLESS={'yes' if headless else 'no'}"])
        version = self.in_tools(awx, ["python", "tools/scripts/scm_version.py"], capture=True).stdout.strip().splitlines()[-1]
        if not re.fullmatch(r"[0-9][A-Za-z0-9.+-]*", version):
            raise LabError(f"Unexpected AWX version: {version!r}")
        result = {"awx_version": version, "tools_image": self.tools_image}
        if not headless:
            dockerfile = awx / "Dockerfile"
            contents = read_regular(dockerfile, awx).decode()
            stage = re.search(r"^FROM [^\n]+ AS ui-builder\s*\n.*?(?=^FROM )", contents, re.MULTILINE | re.DOTALL | re.IGNORECASE)
            if stage:
                key = ui_cache_key(self.runner, self.path / "awx", stage[0], self.arch)
                ui_image = f"localhost/awx-lab-ui:{self.path.name}-{key}"
                self.record_image(ui_image)
                exists = self.runner.run([self.runtime, "image", "inspect", ui_image], capture=True, check=False).returncode == 0
                if no_cache or not exists:
                    self.runner.write("Building the UI cache; later backend-only updates will reuse it.\n")
                    self.runner.run([self.runtime, "build", *(["--no-cache"] if no_cache else []),
                                     "--target", "ui-builder", "--platform", f"linux/{self.arch}",
                                     "-f", dockerfile, "-t", ui_image, awx], timeout=self.timeout)
                else:
                    self.runner.write("Reusing the compiled UI image.\n")
                # The original Dockerfile still builds every backend/dependency change.
                write_regular(dockerfile, awx, contents[:stage.start()] + f"FROM {ui_image} AS ui-builder\n\n" + contents[stage.end():])
                result["ui_image"] = ui_image
            else:
                self.runner.write("UI build layout is unfamiliar; using the complete upstream build.\n")
        if plugin:
            if plugin_source.is_symlink() or (plugin_source / "dist").is_symlink():
                raise LabError("Plugin build output directory is a symlink.")
            self.in_tools(plugin_source, ["python", "-m", "build", "--wheel", "--outdir", "/src/dist"])
            wheels = list((plugin_source / "dist").glob("awx_plugins_core-*.whl"))
            if len(wheels) != 1:
                raise LabError("Plugin build must produce exactly one awx_plugins_core wheel.")
            result["plugin_wheel"] = inject_plugin(awx, wheels[0])
        flags = ["--no-cache"] if no_cache else []
        self.record_image(image)
        self.runner.run([self.runtime, "build", *flags, "--platform", f"linux/{self.arch}",
                         "--build-arg", f"VERSION={version}", "--build-arg", f"SETUPTOOLS_SCM_PRETEND_VERSION={version}",
                         "--build-arg", f"HEADLESS={'yes' if headless else 'no'}",
                         "-f", awx / "Dockerfile", "-t", image, awx],
                        timeout=self.timeout, env={"DOCKER_BUILDKIT": "1"})
        result["image_id"] = self.runner.run([self.runtime, "image", "inspect", image, "--format", "{{.Id}}"], capture=True).stdout.strip()
        return result
