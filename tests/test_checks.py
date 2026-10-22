import unittest
from unittest.mock import Mock, patch

from awx_lab.checks import API
from awx_lab.system import LabError


class CheckTests(unittest.TestCase):
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
