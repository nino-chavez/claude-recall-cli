"""session-archive push/verify against a fake R2.

Measured 2026-10-06: codex-sessions/2026-07 was marked pushed on 2026-09-21,
but R2 never had the key. The put exited 0 with its log stopped at "Creating
object". Every later run skipped the push, failed verify with "The specified
key does not exist", and exited before prune. These tests pin both halves of
the fix: push confirms the object before marking it, and verify un-marks an
object the remote says is absent.

Hermetic: the module's OUT/STATE/HOME point at a temp dir and `_wrangler` is
replaced by an in-memory bucket, so no test reaches R2, `op`, or the real
~/.claude/archived_sessions/archive-state.json.
"""

import argparse
import hashlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("session-archive.py")
SPEC = importlib.util.spec_from_file_location("session_archive", MODULE_PATH)
sa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(sa)

NAME = "codex-sessions/2026-07.tar.zst"
# The exact shape wrangler 4.50 printed on 2026-10-06, ANSI codes included.
MISSING = ("\x1b[31m✘ \x1b[41;31m[\x1b[41;97mERROR\x1b[41;31m]\x1b[0m "
           "\x1b[1mThe specified key does not exist.\x1b[0m\n")
AUTH = "✘ [ERROR] A request to the Cloudflare API failed. Authentication error [code: 10000]\n"


class FakeR2:
    """Stands in for `wrangler r2 object get|put`.

    put_mode: "ok" stores the bytes; "phantom" exits 0 and stores nothing (the
    2026-09-21 failure); "timeout" returns 124 the way _wrangler reports one.
    get_mode: "ok" serves what is stored; "auth" fails without saying the key
    is missing.
    """

    def __init__(self):
        self.objects = {}
        self.put_mode = "ok"
        self.get_mode = "ok"
        self.calls = []

    def __call__(self, bucket, op, path, *rest, timeout=None):
        self.calls.append((op, path, timeout))
        key = path.split("/", 1)[1]
        file = next(a.split("=", 1)[1] for a in rest if a.startswith("--file="))
        if op == "put":
            if self.put_mode == "timeout":
                return subprocess.CompletedProcess([op], 124, "", f"wrangler timed out after {timeout}s")
            if self.put_mode == "ok":
                self.objects[key] = Path(file).read_bytes()
            return subprocess.CompletedProcess([op], 0, f'Creating object "{key}"\n', "")
        if op == "get":
            if self.get_mode == "auth":
                Path(file).write_bytes(b"")
                return subprocess.CompletedProcess([op], 1, "", AUTH)
            if key not in self.objects:
                # wrangler leaves a 0-byte file behind on a missing key.
                Path(file).write_bytes(b"")
                return subprocess.CompletedProcess([op], 1, "", MISSING)
            Path(file).write_bytes(self.objects[key])
            return subprocess.CompletedProcess([op], 0, "Download complete.\n", "")
        raise AssertionError(f"unexpected wrangler op {op}")


class ArchiveTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.saved = {k: getattr(sa, k) for k in ("OUT", "STATE", "HOME", "_wrangler")}
        self.saved_which = sa.shutil.which
        sa.HOME = str(root / "home")
        sa.OUT = str(root / "archived_sessions")
        sa.STATE = os.path.join(sa.OUT, "archive-state.json")
        # The point of the redirect: a test must never write the real state file.
        self.assertTrue(sa.STATE.startswith(self.tmp.name))
        self.r2 = FakeR2()
        sa._wrangler = self.r2
        sa.shutil.which = lambda tool: f"/fake/{tool}"

        payload = os.urandom(4096)
        tar = Path(sa.OUT) / NAME
        tar.parent.mkdir(parents=True)
        tar.write_bytes(payload)
        (Path(sa.OUT) / "MANIFEST.jsonl").write_text("")  # pack always writes one
        sa.save_state({"objects": {NAME: {
            "tree": "codex-sessions", "month": "2026-07", "members": [],
            "file_count": 0, "raw_bytes": 0, "line_count": 0,
            "archive_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "packed": "2026-09-21T09:15:08+00:00", "pushed": None, "verified": None,
        }}})
        self.payload = payload
        self.args = argparse.Namespace(bucket="test-bucket", remote="")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(sa, k, v)
        sa.shutil.which = self.saved_which
        self.tmp.cleanup()

    def run_cmd(self, fn):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fn(self.args)
        return rc, out.getvalue()

    def obj(self):
        return sa.load_state()["objects"][NAME]


class PushConfirmsBeforeMarking(ArchiveTestCase):
    def test_put_that_stores_nothing_is_not_marked_pushed(self):
        self.r2.put_mode = "phantom"
        rc, out = self.run_cmd(sa.cmd_push)
        self.assertIsNone(self.obj()["pushed"])
        self.assertEqual(rc, 1)
        self.assertIn("not in remote", out)

    def test_put_that_stores_the_object_is_marked_pushed(self):
        rc, out = self.run_cmd(sa.cmd_push)
        self.assertEqual(rc, 0)
        self.assertIsNotNone(self.obj()["pushed"])
        self.assertEqual(self.r2.objects[NAME], self.payload)
        self.assertIn("confirmed in remote", out)

    def test_corrupt_upload_is_not_marked_pushed(self):
        def corrupt_put(bucket, op, path, *rest, timeout=None):
            r = FakeR2.__call__(self.r2, bucket, op, path, *rest, timeout=timeout)
            if op == "put":
                self.r2.objects[path.split("/", 1)[1]] = b"truncated"
            return r
        sa._wrangler = corrupt_put
        rc, out = self.run_cmd(sa.cmd_push)
        self.assertIsNone(self.obj()["pushed"])
        self.assertIn("sha256", out)

    def test_put_timeout_is_a_failure(self):
        self.r2.put_mode = "timeout"
        rc, out = self.run_cmd(sa.cmd_push)
        self.assertEqual(rc, 1)
        self.assertIsNone(self.obj()["pushed"])
        self.assertIn("timed out", out)

    def test_put_is_given_a_timeout(self):
        self.run_cmd(sa.cmd_push)
        put = next(c for c in self.r2.calls if c[0] == "put" and c[1].endswith(NAME))
        self.assertIsNotNone(put[2])

    def test_missing_local_tarball_is_reported_without_failing_the_run(self):
        # If the transcripts are gone too, nothing can rebuild the tarball. A
        # failure here would stop every later run before verify and prune.
        os.remove(Path(sa.OUT) / NAME)
        rc, out = self.run_cmd(sa.cmd_push)
        self.assertEqual(rc, 0)
        self.assertIn("local tarball missing", out)
        self.assertIsNone(self.obj()["pushed"])


class VerifyClearsPushedOnlyWhenKeyIsAbsent(ArchiveTestCase):
    def mark_pushed(self):
        st = sa.load_state()
        st["objects"][NAME]["pushed"] = "2026-09-21T09:25:48+00:00"
        sa.save_state(st)

    def test_missing_key_clears_pushed(self):
        self.mark_pushed()
        rc, out = self.run_cmd(sa.cmd_verify)
        self.assertEqual(rc, 1)
        self.assertIsNone(self.obj()["pushed"])
        self.assertIsNone(self.obj()["verified"])
        self.assertIn("cleared 'pushed'", out)

    def test_auth_failure_keeps_pushed(self):
        self.mark_pushed()
        self.r2.get_mode = "auth"
        rc, _ = self.run_cmd(sa.cmd_verify)
        self.assertEqual(rc, 1)
        self.assertEqual(self.obj()["pushed"], "2026-09-21T09:25:48+00:00")

    def test_next_push_reuploads_after_clear(self):
        """The 2026-09-21 sequence end to end: phantom put marked pushed by the
        old code, verify finds the key absent, the next push recovers."""
        self.mark_pushed()
        self.run_cmd(sa.cmd_verify)
        rc, _ = self.run_cmd(sa.cmd_push)
        self.assertEqual(rc, 0)
        self.assertEqual(self.r2.objects[NAME], self.payload)
        self.assertIsNotNone(self.obj()["pushed"])


class MissingKeyDetection(unittest.TestCase):
    def cp(self, stderr="", stdout=""):
        return subprocess.CompletedProcess([], 1, stdout, stderr)

    def test_ansi_wrapped_wrangler_message(self):
        self.assertTrue(sa._is_missing_key(self.cp(MISSING)))

    def test_s3_error_code(self):
        self.assertTrue(sa._is_missing_key(self.cp(stdout="<Code>NoSuchKey</Code>")))

    def test_auth_and_network_errors_are_not_missing(self):
        self.assertFalse(sa._is_missing_key(self.cp(AUTH)))
        self.assertFalse(sa._is_missing_key(self.cp("fetch failed: ECONNRESET")))
        self.assertFalse(sa._is_missing_key(self.cp("wrangler timed out after 300s")))


if __name__ == "__main__":
    unittest.main()
