"""Explicitly scoped API smoke tests and an optional local Ansible job."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from .system import LabError


class API:
    def __init__(self, url, username, password):
        self.url = url.rstrip("/")
        self.authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        # Lab access is loopback-only; do not send admin credentials to an HTTP proxy or redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, method="GET", body=None):
        request = urllib.request.Request(self.url + path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Authorization": self.authorization, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            raise LabError(f"AWX API {method} {path} returned HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise LabError(f"AWX API {method} {path} failed: {error}") from error

    def smoke(self):
        deadline = time.monotonic() + 120
        while True:
            try:
                ping = self.call("/api/v2/ping/")
                if not ping.get("version"):
                    raise LabError("AWX ping did not return a version.")
                break
            except LabError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(3)
        me = self.call("/api/v2/me/")
        if not me.get("results") or me["results"][0].get("username") != "admin":
            raise LabError("Admin authentication smoke check failed.")
        types = self.call("/api/v2/credential_types/")
        if types.get("count", 0) < 1:
            raise LabError("No AWX credential types were registered.")
        return {"version": ping["version"], "checks": ["API ping", "admin authentication", "credential type registration"]}

    def job(self, run_id, timeout=300):
        # A fresh inventory limits this job to localhost inside its execution environment.
        # Retain the job and inventory for inspection when the lab is kept.
        org = self.call("/api/v2/organizations/", "POST", {"name": run_id})["id"]
        inventory = self.call("/api/v2/inventories/", "POST", {"name": run_id, "organization": org})["id"]
        self.call(f"/api/v2/inventories/{inventory}/hosts/", "POST", {
            "name": "localhost", "variables": json.dumps({"ansible_connection": "local", "ansible_python_interpreter": "{{ ansible_playbook_python }}"})})
        job = self.call("/api/v2/ad_hoc_commands/", "POST", {
            "inventory": inventory, "limit": "localhost", "module_name": "ping", "job_type": "run"})["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.call(f"/api/v2/ad_hoc_commands/{job}/")
            if result["status"] == "successful":
                return {"job_id": job, "status": "successful"}
            if result["status"] in ("failed", "error", "canceled"):
                raise LabError(f"Ansible localhost ping job {job}: {result['status']}")
            time.sleep(3)
        raise LabError(f"Ansible localhost ping job {job} timed out.")
