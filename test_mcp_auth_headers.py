import importlib.util
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parent / "adapters" / "mcp-auth-headers.py"
SPEC = importlib.util.spec_from_file_location("mcp_auth_headers", MODULE_PATH)
helper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(helper)


class ResolveTokenTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("RECALL_CONTEXT_TOKEN", None)
        self.writes = []
        for name, value in {
            "write_cache": self.writes.append,
            "read_cache": lambda: (None, None),
            "read_1password": lambda: None,
        }.items():
            p = patch.object(helper, name, value)
            p.start()
            self.addCleanup(p.stop)

    def set_cache(self, token, age):
        helper.read_cache = lambda: (token, int(time.time() - age))

    def test_environment_token_wins(self):
        os.environ["RECALL_CONTEXT_TOKEN"] = "from-env"
        self.set_cache("cached", 0)
        self.assertEqual(helper.resolve_token(), "from-env")

    def test_fresh_cache_skips_1password(self):
        self.set_cache("cached", 60)
        helper.read_1password = lambda: self.fail("1Password should not be called")
        self.assertEqual(helper.resolve_token(), "cached")

    def test_expired_cache_refreshes_from_1password(self):
        self.set_cache("old", helper.TTL_SECONDS + 1)
        helper.read_1password = lambda: "new"
        self.assertEqual(helper.resolve_token(), "new")
        self.assertEqual(self.writes, ["new"])

    def test_refresh_flag_bypasses_fresh_cache(self):
        self.set_cache("cached", 60)
        helper.read_1password = lambda: "new"
        self.assertEqual(helper.resolve_token(refresh=True), "new")

    def test_expired_cache_used_when_1password_fails(self):
        self.set_cache("old", helper.TTL_SECONDS + 1)
        self.assertEqual(helper.resolve_token(), "old")
        self.assertEqual(self.writes, [])

    def test_nothing_available(self):
        self.assertIsNone(helper.resolve_token())


class ReadOnePasswordTests(unittest.TestCase):
    def test_timeout_returns_none(self):
        with patch.object(helper, "find_with_secret", return_value="/bin/with-secret"), \
             patch.object(helper.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertIsNone(helper.read_1password())

    def test_failed_read_returns_none(self):
        failed = subprocess.CompletedProcess([], 1, stdout="Error fetching secret", stderr="")
        with patch.object(helper, "find_with_secret", return_value="/bin/with-secret"), \
             patch.object(helper.subprocess, "run", return_value=failed):
            self.assertIsNone(helper.read_1password())


class CacheFormatTests(unittest.TestCase):
    def run_read(self, stdout, returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with patch.object(helper.subprocess, "run", return_value=result):
            return helper.read_cache()

    def test_reads_stamp_and_token(self):
        self.assertEqual(self.run_read("1700000000:abc-123\n"), ("abc-123", 1700000000))

    def test_rejects_malformed_value(self):
        self.assertEqual(self.run_read("not-a-stamp:abc"), (None, None))
        self.assertEqual(self.run_read('1700000000:has"quote'), (None, None))

    def test_missing_item(self):
        self.assertEqual(self.run_read("", returncode=44), (None, None))

    def test_write_refuses_unsafe_token(self):
        with patch.object(helper.subprocess, "run") as run:
            helper.write_cache('bad" -w "x')
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
