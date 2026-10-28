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
from datetime import datetime, timezone
import importlib.util
import io
import os
import shutil
import sqlite3
from unittest.mock import patch
import subprocess
import tarfile
import tempfile
import time
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

    def test_metadata_upload_is_verified_too(self):
        def phantom_metadata(bucket, op, path, *rest, timeout=None):
            if op == "put" and path.endswith("archive-state.json"):
                return subprocess.CompletedProcess([op], 0, "", "")
            return self.r2(bucket, op, path, *rest, timeout=timeout)
        sa._wrangler = phantom_metadata
        rc, out = self.run_cmd(sa.cmd_push)
        self.assertEqual(rc, 1)
        self.assertIn("archive-state.json", out)

    def test_large_r2_upload_uses_multipart_and_still_reads_back(self):
        def multipart(args, src, name):
            self.r2.objects[name] = Path(src).read_bytes()
            return subprocess.CompletedProcess([], 0, "", "")
        with patch.object(sa, "WRANGLER_MAX_BYTES", 100), patch.object(
                sa, "_r2_multipart", side_effect=multipart) as upload:
            rc, out = self.run_cmd(sa.cmd_push)
        self.assertEqual(rc, 0, out)
        upload.assert_called_once()
        self.assertIsNotNone(self.obj()["pushed"])

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



NOW = 1_800_000_000.0
H = "/h"   # plan_pack is pure: paths only need to sit under sa.HOME


def ago(days):
    return NOW - days * 86400


class SplitPackPlanner(unittest.TestCase):
    """plan_pack against claude/2026-07 as measured 2026-10-06: 1,082 files,
    543 within ten days of the 90-day deletion, one resumed session 10 days
    old holding the whole month open."""

    def setUp(self):
        self.saved_home = sa.HOME
        sa.HOME = H

    def tearDown(self):
        sa.HOME = self.saved_home

    def plan(self, groups, objects=None, retention=90, lead=15, cutoff=45, exists=True):
        return sa.plan_pack(groups, objects or {}, NOW, cutoff, lead,
                            retention=lambda tree: retention if tree == "claude" else None,
                            local_exists=lambda name: exists)

    def files(self, *ages, month="2026-07"):
        return [(f"{H}/.claude/projects/p/{month}-{i}.jsonl", ago(a)) for i, a in enumerate(ages)]

    def obj(self, files, snapshot_days_ago, verified=True):
        return {"tree": "claude", "month": "2026-07",
                "members": [p[len(H) + 1:] for p, _ in files],
                "snapshot_at": ago(snapshot_days_ago),
                "packed": "2026-01-01T00:00:00+00:00",
                "verified": "2026-01-01T00:00:00+00:00" if verified else None}

    def test_fresh_cold_month_packs_whole_as_before(self):
        f = self.files(50, 60)
        [it] = self.plan({("codex-sessions", "2026-05"): f})
        self.assertEqual(it["name"], "codex-sessions/2026-05.tar.zst")
        self.assertEqual(it["paths"], sorted(p for p, _ in f))
        self.assertEqual((it["reason"], it["held"]), ("month cold", 0))

    def test_codex_cold_files_are_not_blocked_by_a_recent_chat(self):
        files = self.files(89, 1)
        [item] = self.plan({("codex-sessions", "2026-07"): files})
        self.assertEqual(item["paths"], [files[0][0]])
        self.assertEqual(item["held"], 1)

    def test_split_packs_cold_files_and_holds_the_blocker(self):
        f = self.files(89, 80, 50, 46, 10)
        [it] = self.plan({("claude", "2026-07"): f})
        self.assertEqual(it["name"], "claude/2026-07.tar.zst")
        self.assertEqual(it["paths"], sorted(p for p, _ in f[:4]))
        self.assertEqual(it["held"], 1)
        self.assertTrue(it["reason"].startswith("split"))

    def test_no_split_until_a_file_is_within_the_lead(self):
        self.assertEqual(self.plan({("claude", "2026-08"): self.files(70, 50, 7)}), [])

    def test_blocker_gets_part2_once_cold(self):
        f = self.files(89, 80, 50, 46, 46)
        base = self.obj(f[:4], snapshot_days_ago=30)
        [it] = self.plan({("claude", "2026-07"): f}, {"claude/2026-07.tar.zst": base})
        self.assertEqual(it["name"], "claude/2026-07.part2.tar.zst")
        self.assertEqual(it["paths"], [f[4][0]])
        self.assertEqual(it["reason"], "month cold")

    def test_verified_unchanged_month_plans_nothing(self):
        f = self.files(89, 80)
        self.assertEqual(self.plan({("claude", "2026-07"): f},
                                   {"claude/2026-07.tar.zst": self.obj(f, 60)}), [])

    def test_member_written_after_its_snapshot_is_pending_again(self):
        f = self.files(80, 50)   # the 50-day file was written after a 60-day-old snapshot
        [it] = self.plan({("claude", "2026-07"): f},
                         {"claude/2026-07.tar.zst": self.obj(f, 60)})
        self.assertEqual(it["name"], "claude/2026-07.part2.tar.zst")
        self.assertEqual(it["paths"], [f[1][0]])

    def test_retention_shorter_than_cutoff_still_saves_files(self):
        # cleanupPeriodDays at its 30-day default deletes files before they are
        # 45 days cold; the at-risk floor has to win over the cutoff.
        f = self.files(20, 16, 5)
        [it] = self.plan({("claude", "2026-09"): f}, retention=30)
        self.assertEqual(it["paths"], sorted(p for p, _ in f[:2]))
        self.assertEqual(it["held"], 1)

    def test_unverified_object_without_tarball_is_rebuilt_under_its_name(self):
        f = self.files(80, 70)
        base = self.obj(f, 60, verified=False)
        [it] = self.plan({("claude", "2026-07"): f}, {"claude/2026-07.tar.zst": base}, exists=False)
        self.assertEqual(it["name"], "claude/2026-07.tar.zst")
        self.assertEqual(len(it["paths"]), 2)

    def test_every_lost_unverified_object_is_rebuilt_from_its_own_members(self):
        f = self.files(89, 85, 80)
        base = self.obj(f[:2], 60, verified=False)
        part2 = dict(self.obj(f[2:], 50, verified=False))
        plan = self.plan({("claude", "2026-07"): f},
                         {"claude/2026-07.tar.zst": base, "claude/2026-07.part2.tar.zst": part2},
                         exists=False)
        got = {it["name"]: it["paths"] for it in plan}
        self.assertEqual(got, {"claude/2026-07.tar.zst": sorted(p for p, _ in f[:2]),
                               "claude/2026-07.part2.tar.zst": [f[2][0]]})

    def test_part_names(self):
        self.assertEqual(sa.obj_name("claude", "2026-07", 3), "claude/2026-07.part3.tar.zst")
        self.assertEqual(sa.part_of("claude/2026-07.part3.tar.zst"), 3)
        self.assertEqual(sa.part_of("claude/2026-07.tar.zst"), 1)


class RetentionDays(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = sa.CLAUDE_SETTINGS
        sa.CLAUDE_SETTINGS = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        sa.CLAUDE_SETTINGS = self.saved
        self.tmp.cleanup()

    def test_reads_cleanup_period_days(self):
        Path(sa.CLAUDE_SETTINGS).write_text('{"cleanupPeriodDays": 90}')
        self.assertEqual(sa.retention_days("claude"), 90)

    def test_defaults_to_claude_codes_30_when_unset_or_unreadable(self):
        self.assertEqual(sa.retention_days("claude"), 30)
        Path(sa.CLAUDE_SETTINGS).write_text("not json")
        self.assertEqual(sa.retention_days("claude"), 30)

    def test_codex_has_no_retention(self):
        self.assertIsNone(sa.retention_days("codex-sessions"))


class PruneKeepsFilesWrittenAfterPacking(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        self.d = Path(sa.HOME) / ".codex/sessions"
        self.d.mkdir(parents=True)
        self.snap = time.time() - 1000
        self.db = Path(sa.HOME) / ".codex/state_5.sqlite"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE threads (rollout_path TEXT, archived INTEGER)")

    def make(self, name, mtime):
        p = self.d / name
        p.write_text("{}\n")
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO threads VALUES (?, 1)", (str(p),))
        os.utime(p, (mtime, mtime))
        return p

    def rel(self, p):
        return os.path.relpath(p, sa.HOME)

    def verified(self, members, snapshot):
        return {"tree": "codex-sessions", "month": "2026-07", "members": members,
                "snapshot_at": snapshot, "packed": "2026-01-01T00:00:00+00:00",
                "pushed": "x", "verified": datetime.now(timezone.utc).isoformat(),
                "member_sha256": {r: sa.sha256(Path(sa.HOME) / r) for r in members}}

    def prune(self):
        return self.run_cmd(lambda _: sa.cmd_prune(argparse.Namespace(tree=None, yes=True)))

    def test_file_written_after_snapshot_is_kept(self):
        cold = self.make("cold.jsonl", self.snap - 100)
        resumed = self.make("resumed.jsonl", self.snap + 100)
        sa.save_state({"objects": {NAME: self.verified(
            [self.rel(cold), self.rel(resumed)], self.snap)}})
        _, out = self.prune()
        self.assertFalse(cold.exists())
        self.assertTrue(resumed.exists())
        self.assertIn("written after they were packed", out)

    def test_a_later_part_covering_the_new_lines_authorizes_deletion(self):
        resumed = self.make("resumed.jsonl", self.snap + 100)
        sa.save_state({"objects": {
            NAME: self.verified([self.rel(resumed)], self.snap),
            "codex-sessions/2026-07.part2.tar.zst": self.verified([self.rel(resumed)], self.snap + 500),
        }})
        self.prune()
        self.assertFalse(resumed.exists())

    def test_unarchived_chat_is_kept_even_with_matching_backup(self):
        active = self.make("active.jsonl", self.snap - 100)
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE threads SET archived=0")
        sa.save_state({"objects": {NAME: self.verified([self.rel(active)], self.snap)}})
        self.prune()
        self.assertTrue(active.exists())

    def test_unknown_chat_is_kept(self):
        unknown = self.make("unknown.jsonl", self.snap - 100)
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM threads")
        sa.save_state({"objects": {NAME: self.verified([self.rel(unknown)], self.snap)}})
        self.prune()
        self.assertTrue(unknown.exists())

    def test_changed_bytes_with_preserved_mtime_are_kept(self):
        member = self.make("changed.jsonl", self.snap - 100)
        sa.save_state({"objects": {NAME: self.verified([self.rel(member)], self.snap)}})
        member.write_text('{"changed":true}\n')
        os.utime(member, (self.snap - 100, self.snap - 100))
        self.prune()
        self.assertTrue(member.exists())

    def test_stale_verification_prevents_deletion(self):
        member = self.make("cold.jsonl", self.snap - 100)
        obj = self.verified([self.rel(member)], self.snap)
        obj["verified"] = "2026-01-01T00:00:00+00:00"
        sa.save_state({"objects": {NAME: obj}})
        self.prune()
        self.assertTrue(member.exists())

    def test_legacy_verification_without_member_digests_keeps_file(self):
        member = self.make("legacy.jsonl", self.snap - 100)
        obj = self.verified([self.rel(member)], self.snap)
        del obj["member_sha256"]
        sa.save_state({"objects": {NAME: obj}})
        self.prune()
        self.assertTrue(member.exists())

    def test_compressed_archived_chat_matches_plain_metadata_path(self):
        member = self.make("compressed.jsonl", self.snap - 100)
        compressed = member.with_suffix(".jsonl.zst")
        member.rename(compressed)
        sa.save_state({"objects": {NAME: self.verified([self.rel(compressed)], self.snap)}})
        self.prune()
        self.assertFalse(compressed.exists())

    def test_reopened_chat_is_rechecked_at_deletion(self):
        member = self.make("reopened.jsonl", self.snap - 100)
        sa.save_state({"objects": {NAME: self.verified([self.rel(member)], self.snap)}})
        with patch.object(sa, "codex_archived", side_effect=[True, False]):
            self.prune()
        self.assertTrue(member.exists())


@unittest.skipUnless(shutil.which("zstd"), "zstd not installed")
class VerifyToleratesFilesWrittenSincePack(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        self.member = Path(sa.HOME) / ".claude/projects/p/s.jsonl"
        self.member.parent.mkdir(parents=True)
        self.member.write_text('{"a": 1}\n{"a": 2}\n')
        rel = os.path.relpath(self.member, sa.HOME)
        tarpath = Path(self.tmp.name) / "a.tar"
        with tarfile.open(tarpath, "w") as tf:
            tf.add(self.member, arcname=rel)
        blob = subprocess.run(["zstd", "-q", "-c", str(tarpath)], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=60, check=True).stdout
        self.snap = time.time()
        self.r2.objects[NAME] = blob
        sa.save_state({"objects": {NAME: {
            "tree": "claude", "month": "2026-07", "members": [rel], "file_count": 1,
            "line_count": 2, "archive_bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(), "snapshot_at": self.snap,
            "packed": "2026-10-06T00:00:00+00:00", "pushed": "x", "verified": None}}})

    def test_member_resumed_after_pack_still_verifies(self):
        with open(self.member, "a") as f: f.write('{"a": 3}\n')
        os.utime(self.member, (self.snap + 60, self.snap + 60))
        rc, out = self.run_cmd(sa.cmd_verify)
        self.assertEqual(rc, 0, out)
        self.assertIsNotNone(self.obj()["verified"])
        self.assertIn("1 written since pack", out)

    def test_unchanged_member_with_different_bytes_still_fails(self):
        # Negative control: the byte-compare must still catch a real mismatch.
        self.member.write_text('{"a": 9}\n{"a": 2}\n')
        os.utime(self.member, (self.snap - 60, self.snap - 60))
        rc, out = self.run_cmd(sa.cmd_verify)
        self.assertEqual(rc, 1)
        self.assertIn("CONTENT MISMATCH", out)


@unittest.skipUnless(shutil.which("zstd"), "zstd not installed")
class CompressedTranscripts(ArchiveTestCase):
    def test_survey_and_line_count_read_compressed_content(self):
        member = Path(sa.HOME) / ".codex/archived_sessions/session.jsonl.zst"
        member.parent.mkdir(parents=True)
        source = b'{"timestamp":"2026-03-01T12:00:00Z"}\n{"a":2}\n'
        member.write_bytes(subprocess.run(
            ["zstd", "-q", "-c"], input=source, capture_output=True,
            timeout=30, check=True).stdout)
        from unittest.mock import patch
        with patch.dict(sa.TREES, {"codex-archived": str(member.parent)}, clear=True):
            groups = sa.survey()
        self.assertIn(("codex-archived", "2026-03"), groups)
        self.assertEqual(groups[("codex-archived", "2026-03")][0][0], str(member))
        self.assertEqual(sa.count_lines(member), 2)

    def test_corrupt_compressed_transcript_is_not_counted_as_text(self):
        member = Path(self.tmp.name) / "corrupt.jsonl.zst"
        member.write_bytes(b"not a zstd stream")
        with self.assertRaises(OSError):
            sa.count_lines(member)


@unittest.skipUnless(shutil.which("zstd"), "zstd not installed")
class CompressedArchiveRoundTrip(ArchiveTestCase):
    def setUp(self):
        super().setUp()
        self.member = Path(sa.HOME) / ".codex/archived_sessions/cold.jsonl.zst"
        self.member.parent.mkdir(parents=True)
        self.member.write_bytes(subprocess.run(["zstd", "-q", "-c"],
            input=b'{"timestamp":"2026-01-01"}\n{"a":2}\n', capture_output=True,
            timeout=30, check=True).stdout)
        os.utime(self.member, (time.time()-90*86400,)*2)
        sa.save_state({"objects": {}})
        self.args.cutoff_days = 45
        self.args.split_lead_days = 15
        with patch.dict(sa.TREES, {"codex-archived": str(self.member.parent)}, clear=True):
            rc, out = self.run_cmd(sa.cmd_pack)
        self.assertEqual(rc, 0, out)
        self.run_cmd(sa.cmd_push)
        self.name = next(iter(sa.load_state()["objects"]))

    def test_restore_verifies_original_compressed_bytes_and_logical_lines(self):
        rc, out = self.run_cmd(sa.cmd_verify)
        self.assertEqual(rc, 0, out)
        obj = sa.load_state()["objects"][self.name]
        self.assertEqual(obj["line_count"], 2)
        self.assertEqual(obj["member_sha256"][os.path.relpath(self.member, sa.HOME)],
                         hashlib.sha256(self.member.read_bytes()).hexdigest())

    def test_corruption_invalidates_previous_verification(self):
        self.assertEqual(self.run_cmd(sa.cmd_verify)[0], 0)
        self.r2.objects[self.name] = b"corrupt remote"
        self.assertEqual(self.run_cmd(sa.cmd_verify)[0], 1)
        obj = sa.load_state()["objects"][self.name]
        self.assertIsNone(obj["verified"])
        self.assertNotIn("member_sha256", obj)
        self.assertTrue(self.member.exists())


class MultipartCredentials(ArchiveTestCase):
    def test_r2_credentials_stay_in_child_environment(self):
        env = {"CLOUDFLARE_API_TOKEN": "fixture-secret", "CLOUDFLARE_ACCOUNT_ID": "fixture-account"}
        response = io.BytesIO(b'{"success":true,"result":{"id":"fixture-id","status":"active"}}')
        with patch.object(sa, "_cloudflare_env", return_value=env), patch.object(
                sa.urllib.request, "urlopen", return_value=response), patch.object(
                sa.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            rc = sa._r2_multipart(self.args, str(Path(sa.OUT) / NAME), NAME).returncode
        self.assertEqual(rc, 0)
        argv = run.call_args.args[0]
        child = run.call_args.kwargs["env"]
        self.assertNotIn("fixture-secret", " ".join(argv))
        self.assertEqual(child["RCLONE_CONFIG_SESSION_ARCHIVE_R2_ACCESS_KEY_ID"], "fixture-id")
        self.assertEqual(child["RCLONE_CONFIG_SESSION_ARCHIVE_R2_SECRET_ACCESS_KEY"],
                         hashlib.sha256(b"fixture-secret").hexdigest())

    def test_inactive_token_prevents_upload(self):
        response = io.BytesIO(b'{"success":true,"result":{"id":"fixture-id","status":"disabled"}}')
        with patch.object(sa, "_cloudflare_env", return_value={"CLOUDFLARE_API_TOKEN":"fixture"}), patch.object(
                sa.urllib.request, "urlopen", return_value=response), patch.object(sa.subprocess, "run") as run:
            self.assertEqual(sa._r2_multipart(self.args, str(Path(sa.OUT) / NAME), NAME).returncode, 1)
        run.assert_not_called()


class StateReadSafety(ArchiveTestCase):
    def test_malformed_existing_state_is_not_an_empty_archive(self):
        Path(sa.STATE).write_text("not json")
        with self.assertRaises(ValueError):
            sa.load_state()


if __name__ == "__main__":
    unittest.main()
