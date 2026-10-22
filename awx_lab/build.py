"""Native container builds, using AWX's own Dockerfile renderer."""

from __future__ import annotations

import hashlib
from pathlib import Path
import os
import re
import shutil

from .system import LabError


def inject_plugin(awx: Path, wheel: Path):
    requirements = awx / "requirements/requirements_git.txt"
    original = requirements.read_text()
    pattern = re.compile(r"^(awx-plugins-core(?:\[[^\]\n]+\])?)\s*@[^\n]+$", re.MULTILINE)
    matches = list(pattern.finditer(original))
    if len(matches) != 1:
        raise LabError("Expected one awx-plugins-core dependency in requirements/requirements_git.txt; this AWX revision needs an updated adapter.")
    if not re.fullmatch(r"awx_plugins_core-[A-Za-z0-9_.+-]+\.whl", wheel.name):
        raise LabError(f"Unexpected plugin wheel filename: {wheel.name}")
    copied = awx / "requirements" / wheel.name
    shutil.copy2(wheel, copied)
    replacement = f"{matches[0].group(1)} @ file:///tmp/awx-lab/{wheel.name}"
    requirements.write_text(pattern.sub(lambda _: replacement, original))
    dockerfile = awx / "Dockerfile"
    contents = dockerfile.read_text()
    marker = "ADD requirements/requirements.txt"
    if contents.count(marker) != 1:
        raise LabError("AWX's generated Dockerfile has changed: cannot safely insert the plugin wheel before dependency installation.")
    contents = contents.replace(marker, f"COPY requirements/{wheel.name} /tmp/awx-lab/{wheel.name}\n{marker}")
    dockerfile.write_text(contents)
    return {"filename": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}


class Builder:
    def __init__(self, runner, runtime, path, arch, timeout=7200):
        self.runner, self.runtime, self.path, self.arch = runner, runtime, path, arch
        self.timeout = timeout
        self.assets = Path(__file__).parent / "assets"
        digest = hashlib.sha256((self.assets / "Containerfile").read_bytes()).hexdigest()[:12]
        self.tools_image = f"localhost/awx-lab-tools:{digest}-{arch}"

    def tools(self):
        self.runner.run([self.runtime, "build", "--platform", f"linux/{self.arch}", "-t", self.tools_image,
                         "-f", self.assets / "Containerfile", self.assets], timeout=self.timeout)

    def in_tools(self, source, command, capture=False):
        flags = []
        if self.runtime == "podman":
            flags += [f"--userns=keep-id:uid={os.getuid()},gid={os.getgid()}"]
        suffix = ":Z" if self.runtime == "podman" else ""
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
                self.runner.write(f"Could not remove helper {name}; retry 'awx-lab down {self.path.name}'.\n")
            raise

    def build(self, image, plugin=False, no_cache=False, headless=False):
        self.tools()
        awx = self.path / "awx"
        self.in_tools(awx, ["make", "Dockerfile", f"HEADLESS={'yes' if headless else 'no'}"])
        version = self.in_tools(awx, ["python", "tools/scripts/scm_version.py"], capture=True).stdout.strip().splitlines()[-1]
        if not re.fullmatch(r"[0-9][A-Za-z0-9.+-]*", version):
            raise LabError(f"Unexpected AWX version: {version!r}")
        result = {"awx_version": version, "tools_image": self.tools_image}
        if plugin:
            source = self.path / "awx-plugins"
            self.in_tools(source, ["python", "-m", "build", "--wheel", "--outdir", "/src/dist"])
            wheels = list((source / "dist").glob("awx_plugins_core-*.whl"))
            if len(wheels) != 1:
                raise LabError("Plugin build must produce exactly one awx_plugins_core wheel.")
            result["plugin_wheel"] = inject_plugin(awx, wheels[0])
        flags = ["--no-cache"] if no_cache else []
        self.runner.run([self.runtime, "build", *flags, "--platform", f"linux/{self.arch}",
                         "--build-arg", f"VERSION={version}", "--build-arg", f"SETUPTOOLS_SCM_PRETEND_VERSION={version}",
                         "--build-arg", f"HEADLESS={'yes' if headless else 'no'}",
                         "-f", awx / "Dockerfile", "-t", image, awx],
                        timeout=self.timeout, env={"DOCKER_BUILDKIT": "1"})
        result["image_id"] = self.runner.run([self.runtime, "image", "inspect", image, "--format", "{{.Id}}"], capture=True).stdout.strip()
        return result
