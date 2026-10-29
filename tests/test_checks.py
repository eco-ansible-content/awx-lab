import http.client
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from awx_lab.checks import API
from awx_lab.system import LabError


class CheckTests(unittest.TestCase):
    def response(self, value):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(value).encode()
        return response

    def test_transport_errors_include_endpoint_and_are_normalized(self):
        errors = (ConnectionResetError(104, "Connection reset by peer"),
                  http.client.RemoteDisconnected("Remote end closed connection"),
                  http.client.IncompleteRead(b"partial"), TimeoutError("timed out"))
        for error in errors:
            for during_read in (False, True):
                with self.subTest(error=type(error).__name__, during_read=during_read):
                    api = API("http://127.0.0.1:8123", "admin", "secret")
                    api.opener = MagicMock()
                    if during_read:
                        api.opener.open.return_value.__enter__.return_value.read.side_effect = error
                    else:
                        api.opener.open.side_effect = error
                    with self.assertRaisesRegex(LabError, r"GET http://127.0.0.1:8123/api/v2/ping/ failed"):
                        api.call("/api/v2/ping/")

    def test_startup_connection_reset_is_retried_then_checks_complete(self):
        api = API("http://127.0.0.1:8123", "admin", "secret")
        api.opener.open = Mock(side_effect=[
            ConnectionResetError(104, "Connection reset by peer"),
            self.response({"version": "test-version"}),
            self.response({"results": [{"username": "admin"}]}), self.response({"count": 1})])
        with patch("awx_lab.checks.time.sleep") as sleep:
            self.assertEqual(api.smoke()["version"], "test-version")
        sleep.assert_called_once_with(3)
        self.assertEqual(api.opener.open.call_count, 4)

    def test_persistent_connection_reset_stops_at_startup_deadline(self):
        api = API("http://127.0.0.1:8123", "admin", "secret")
        api.opener.open = Mock(side_effect=ConnectionResetError(104, "Connection reset by peer"))
        with patch("awx_lab.checks.time.monotonic", side_effect=[0, 0, 121]), \
             patch("awx_lab.checks.time.sleep") as sleep, \
             self.assertRaisesRegex(LabError, r"http://127.0.0.1:8123/api/v2/ping/.*Connection reset by peer"):
            api.smoke()
        self.assertEqual(api.opener.open.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_connection_reset_never_replays_a_mutating_request(self):
        api = API("http://127.0.0.1:8123", "admin", "secret")
        api.opener.open = Mock(side_effect=ConnectionResetError(104, "Connection reset by peer"))
        with patch("awx_lab.checks.time.sleep") as sleep, self.assertRaises(LabError):
            api.call("/api/v2/organizations/", "POST", {"name": "test"})
        api.opener.open.assert_called_once()
        sleep.assert_not_called()

    def test_smoke_requires_authenticated_admin_and_registered_types(self):
        api = API("http://127.0.0.1:8000", "admin", "secret")
        api.call = Mock(side_effect=[{"version": "1.0"}, {"results": [{"username": "admin"}]}, {"count": 12}])
        self.assertEqual(api.smoke()["version"], "1.0")
        api.call = Mock(side_effect=[{"version": "1.0"}, {"results": []}])
        with self.assertRaisesRegex(LabError, "authentication"):
            api.smoke()
        api.call = Mock(side_effect=[{"version": "1.0"}, {"results": [{"username": "admin"}]}, {"count": 0}])
        with self.assertRaisesRegex(LabError, "credential types"):
            api.smoke()

    def test_job_waits_for_real_success_and_is_limited_to_localhost(self):
        api = API("http://127.0.0.1:8000", "admin", "secret")
        api.call = Mock(side_effect=[{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"status": "running"}, {"status": "successful"}])
        with patch("awx_lab.checks.time.sleep"):
            self.assertEqual(api.job("awx-lab-test")["job_id"], 4)
        launch = api.call.call_args_list[3].args[2]
        self.assertEqual(launch["limit"], "localhost")
        self.assertEqual(launch["module_name"], "ping")

    def test_job_failure_propagates(self):
        api = API("http://127.0.0.1:8000", "admin", "secret")
        api.call = Mock(side_effect=[{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"status": "failed"}])
        with self.assertRaisesRegex(LabError, "failed"):
            api.job("awx-lab-test")
