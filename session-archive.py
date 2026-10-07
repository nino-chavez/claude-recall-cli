#!/usr/bin/env python3
"""Cold-archive agent session transcripts to object storage.

Groups transcripts into solid monthly tarballs (zstd -19), writes a sha256
manifest, uploads via rclone, and verifies by pulling the object back and
diffing it against local before anything is considered archived.

    session-archive.py plan                 # what would be archived (default)
    session-archive.py pack                 # build tarballs + manifest locally
    session-archive.py push  --remote NAME  # rclone copy to remote
    session-archive.py verify --remote NAME # download, sha256 + content diff
    session-archive.py prune --yes          # delete locals ONLY for verified objects
    session-archive.py rotate --yes         # age out archives past --keep-months (default 12)

Design notes
------------
* Grouped by the month of each file's FIRST record timestamp, not mtime --
  mtime is when a session was last appended, which is not when the work happened.
* A month is eligible only when EVERY file in it is older than --cutoff-days.
  Partial months are never split, so an object is always a complete month.
* Compression is plain `zstd -19` on a solid tar. Measured on this corpus:
  per-file 3.6x, solid 4.1x, --long=31 4.1x, -22 --ultra 4.2x. Window tuning
  and --ultra buy nothing, so they are not used.
* Encryption is NOT handled here. Point --remote at an `rclone crypt` remote
  wrapping R2; rclone encrypts the finished tarball on upload.
* Rotation has two halves. The authoritative one is server-side: an R2 bucket
  lifecycle rule (`wrangler r2 bucket lifecycle add <bucket> expire-12mo
  --expire-days 365`) expires objects without anything having to run locally.
  `rotate` here is the client-side half -- it drops the local .tar.zst copies
  and, crucially, leaves a TOMBSTONE in archive-state.json: object name, month,
  member list, file/line counts and sha256 are kept forever. So "what did I
  work on in May 2026" stays answerable after the bytes are gone.
* `pushed` means the object was read back from the remote with a matching
  sha256, not that the upload command exited 0. A put exited 0 on 2026-09-21
  without storing anything, and the stale flag blocked that month for weeks.
  If verify later finds the key absent, it clears `pushed` so the next push
  re-uploads; any other verify failure leaves the flag alone.
* prune is gated on a passing verify recorded in the state file -- never on
  ingest watermarks. poe-extract's watermark proves ingestion by poe-extract;
  artifact-miner.py rglobs the whole corpus with no watermark at all, so
  watermark coverage does not mean a file is finished being read.
"""
from __future__ import annotations
import argparse, glob, hashlib, json, os, re, shutil, subprocess, sys, tarfile, tempfile
from collections import defaultdict
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
TREES = {
    "claude":          "~/.claude/projects",
    "codex-archived":  "~/.codex/archived_sessions",
    "codex-sessions":  "~/.codex/sessions",
}
OUT   = os.path.expanduser("~/.claude/archived_sessions")
STATE = os.path.join(OUT, "archive-state.json")
TS    = re.compile(r'"timestamp":\s*"(\d{4})-(\d{2})')

def log(m): print(m, flush=True)

def load_state():
    try:
        with open(STATE) as f: return json.load(f)
    except Exception: return {"objects": {}}

def save_state(s):
    os.makedirs(OUT, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f: json.dump(s, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE)

def content_month(path, scan=400):
    """(month, source) for a transcript.

    source is "content" when a record timestamp was found, "mtime" when the file
    carries none in the first `scan` lines and the month had to be derived from
    the filesystem. Some files -- notably subagent workflow `journal.jsonl` --
    use a timestamp shape this regex does not match, and an earlier version of
    this function returned None for them, which silently excluded 114 files from
    every archive. Never return None: an unclassifiable file must still be
    archived, and the fallback must be visible in `plan`.
    """
    try:
        with open(path, errors="ignore") as f:
            for i, line in enumerate(f):
                m = TS.search(line)
                if m: return f"{m.group(1)}-{m.group(2)}", "content"
                if i > scan: break
    except OSError:
        pass
    try:
        d = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        return f"{d.year:04d}-{d.month:02d}", "mtime"
    except OSError:
        return None, "error"

def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""): h.update(chunk)
    return h.hexdigest()

def count_lines(path):
    n = 0
    with open(path, errors="ignore") as f:
        for _ in f: n += 1
    return n

def survey(cutoff_days):
    """-> {(tree, month): [paths]} for months fully older than the cutoff."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - cutoff_days * 86400
    groups, newest = defaultdict(list), defaultdict(float)
    fallback = []
    for tree, root in TREES.items():
        root = os.path.expanduser(root)
        if not os.path.isdir(root): continue
        for p in glob.glob(root + "/**/*.jsonl", recursive=True):
            mo, src = content_month(p)
            if not mo:
                fallback.append((p, "unreadable")); continue
            if src == "mtime": fallback.append((p, "mtime"))
            k = (tree, mo)
            groups[k].append(p)
            newest[k] = max(newest[k], os.path.getmtime(p))
    survey.last_fallback = fallback
    return {k: sorted(v) for k, v in groups.items() if newest[k] < cutoff}

def obj_name(tree, month): return f"{tree}/{month}.tar.zst"

def cmd_plan(args):
    groups = survey(args.cutoff_days)
    state = load_state()
    if not groups:
        log(f"Nothing older than {args.cutoff_days} days. Nothing to archive."); return 0
    tot_raw = tot_files = 0
    log(f"{'object':34} {'files':>6} {'raw':>9} {'est @4.1x':>10}  status")
    for (tree, mo), paths in sorted(groups.items()):
        raw = sum(os.path.getsize(p) for p in paths)
        tot_raw += raw; tot_files += len(paths)
        st = state["objects"].get(obj_name(tree, mo), {})
        mark = "verified" if st.get("verified") else ("pushed" if st.get("pushed") else
               ("packed" if st.get("packed") else "new"))
        log(f"  {obj_name(tree,mo):32} {len(paths):>6} {raw/1e9:>8.2f}G {raw/4.1/1e9:>9.2f}G  {mark}")
    log(f"\n  TOTAL {tot_files:,} files  {tot_raw/1e9:.2f} GB raw  ~{tot_raw/4.1/1e9:.2f} GB archived")
    fb = getattr(survey, "last_fallback", [])
    if fb:
        n_m = sum(1 for _, k in fb if k == "mtime")
        n_e = sum(1 for _, k in fb if k == "unreadable")
        log(f"  {n_m} file(s) had no parseable record timestamp -- month taken from mtime")
        if n_e: log(f"  {n_e} file(s) UNREADABLE and excluded -- investigate")
    log(f"  free tier headroom: R2 gives 10 GB-month at no cost")
    return 0

def cmd_pack(args):
    if not shutil.which("zstd"):
        log("zstd not found -- brew install zstd"); return 1
    groups = survey(args.cutoff_days)
    state = load_state()
    os.makedirs(OUT, exist_ok=True)
    for (tree, mo), paths in sorted(groups.items()):
        name = obj_name(tree, mo)
        dest = os.path.join(OUT, name)
        prior = state["objects"].get(name, {})
        if prior.get("verified"):
            # Verified in the remote. The local tarball is a disposable cache --
            # deleting it to reclaim disk must NOT cause a re-pack and re-upload
            # on every subsequent run.
            log(f"  skip (verified in remote) {name}"); continue
        if prior.get("packed") and os.path.exists(dest):
            log(f"  skip (packed) {name}"); continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        raw = sum(os.path.getsize(p) for p in paths)
        lines = sum(count_lines(p) for p in paths)
        log(f"  packing {name}: {len(paths)} files, {raw/1e9:.2f} GB ...")
        with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as t: tarpath = t.name
        try:
            with tarfile.open(tarpath, "w") as tf:
                for p in paths: tf.add(p, arcname=os.path.relpath(p, HOME))
            with open(dest, "wb") as out:
                r = subprocess.run(["zstd", "-19", "-T0", "-c", tarpath],
                                   stdout=out, stderr=subprocess.PIPE)
            if r.returncode != 0:
                log(f"    zstd failed: {r.stderr.decode()[:200]}"); continue
        finally:
            os.unlink(tarpath)
        digest, size = sha256(dest), os.path.getsize(dest)
        state["objects"][name] = {
            "tree": tree, "month": mo, "members": [os.path.relpath(p, HOME) for p in paths],
            "file_count": len(paths), "raw_bytes": raw, "line_count": lines,
            "archive_bytes": size, "sha256": digest,
            "packed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pushed": None, "verified": None,
        }
        save_state(state)
        log(f"    -> {size/1e9:.2f} GB ({raw/size:.1f}x)  sha256 {digest[:16]}")
    _write_manifest(state)
    return 0

def _write_manifest(state):
    mpath = os.path.join(OUT, "MANIFEST.jsonl")
    with open(mpath, "w") as f:
        for name, o in sorted(state["objects"].items()):
            f.write(json.dumps({"object": name, **{k: v for k, v in o.items() if k != "members"},
                                "member_count": len(o.get("members", []))}) + "\n")
    log(f"  manifest -> {mpath}")

def _wrangler(bucket, *args, timeout=None):
    """R2 object ops via wrangler + the account-ops API token from 1Password.

    wrangler's own OAuth token has no R2 scope on this machine, so we inject
    CLOUDFLARE_API_TOKEN. The token value is read by op into this process's env
    and never written to disk.

    A timeout comes back as returncode 124, not an exception, so callers treat
    it like any other failed transfer.
    """
    env = dict(os.environ)
    if "CLOUDFLARE_API_TOKEN" not in env:
        r = subprocess.run(["op", "read",
             "op://Developer Secrets/Cloudflare account-ops claude-code/credential"],
            capture_output=True, text=True)
        if r.returncode == 0:
            env["CLOUDFLARE_API_TOKEN"] = r.stdout.strip()
    # Not hardcoded: this repo is public, and an account id in public source is
    # a stable identifier tied to a real account. Read it from the environment,
    # or from the same 1Password item that holds the token (plain `account_id`
    # field, not a concealed one).
    if "CLOUDFLARE_ACCOUNT_ID" not in env:
        r = subprocess.run(["op", "read",
             "op://Developer Secrets/Cloudflare account-ops claude-code/account_id"],
            capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            env["CLOUDFLARE_ACCOUNT_ID"] = r.stdout.strip()
    try:
        return subprocess.run(["wrangler", "r2", "object", *args],
                              capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"wrangler timed out after {timeout}s")


def _rclone(remote, *args, timeout=None):
    try:
        return subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"rclone timed out after {timeout}s")

def _transfer_timeout(nbytes):
    """Seconds allowed for one object transfer. The healthy 273 MB put of
    claude/2026-06 took 40s; the 2026-09-21 put that silently stored nothing
    sat for ten minutes and then exited 0. Generous, but bounded."""
    return 300 + int(2 * nbytes / 1e6)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# wrangler prints R2's NoSuchKey as "The specified key does not exist.", wrapped
# in ANSI colour codes. Only this answer means "the object is not there"; auth or
# network failures must not be mistaken for it, or a bad token would trigger a
# re-upload of everything every week.
_MISSING_KEY = ("the specified key does not exist", "nosuchkey")

def _is_missing_key(r):
    text = _ANSI.sub("", f"{r.stderr or ''}\n{r.stdout or ''}").lower()
    return any(s in text for s in _MISSING_KEY)

def _remote_get(args, name, td, nbytes):
    """Download object `name` into directory td. -> (local_path, CompletedProcess)."""
    local = os.path.join(td, os.path.basename(name))
    if args.bucket:
        r = _wrangler(args.bucket, "get", f"{args.bucket}/{name}", f"--file={local}", "--remote",
                      timeout=_transfer_timeout(nbytes))
    else:
        r = _rclone(args.remote, "copy", f"{args.remote}/{name}", td,
                    timeout=_transfer_timeout(nbytes))
    return local, r

def _confirm_in_remote(args, name, o):
    """-> (ok, reason). The object exists in the remote and its sha256 matches.

    An exit code of 0 from the upload is not evidence the object landed: the
    2026-09-21 put of codex-sessions/2026-07 exited 0, its wrangler log stopped
    at "Creating object", and R2 never had the key. wrangler has no `head`, so
    this is a full get. A failed get still leaves a 0-byte file, which is why the
    check is the hash and not the file's existence.
    """
    with tempfile.TemporaryDirectory() as td:
        local, r = _remote_get(args, name, td, o.get("archive_bytes") or 0)
        if r.returncode != 0:
            why = "not in remote" if _is_missing_key(r) else "read-back failed"
            return False, f"{why}: {_ANSI.sub('', (r.stderr or r.stdout or '').strip())[:200]}"
        if not os.path.exists(local) or os.path.getsize(local) == 0:
            return False, "read-back produced no bytes"
        got = sha256(local)
        if got != o["sha256"]:
            return False, f"read-back sha256 {got[:16]} != {o['sha256'][:16]}"
    return True, "sha256 matches"

def cmd_push(args):
    tool = "wrangler" if args.bucket else "rclone"
    if not shutil.which(tool): log(f"{tool} not found"); return 1
    state = load_state()
    todo = [n for n, o in state["objects"].items() if o.get("packed") and not o.get("pushed")]
    if not todo: log("  nothing packed-and-unpushed"); return 0
    ok_all = True
    for name in sorted(todo):
        o = state["objects"][name]
        src = os.path.join(OUT, name)
        if not os.path.exists(src):
            # pushed can now be cleared after the local tarball was removed. pack
            # re-creates it on the next run; nothing to send until then.
            log(f"  push {name}: local tarball missing -- run pack first"); ok_all = False; continue
        size = os.path.getsize(src)
        if args.bucket:
            log(f"  push {name} -> r2://{args.bucket}/{name}  ({size/1e6:.1f} MB)")
            r = _wrangler(args.bucket, "put", f"{args.bucket}/{name}", f"--file={src}", "--remote",
                          timeout=_transfer_timeout(size))
        else:
            dst = f"{args.remote}/{os.path.dirname(name)}"
            log(f"  push {name} -> {dst}")
            r = _rclone(args.remote, "copy", src, dst, "--progress", "--s3-chunk-size", "64M",
                        timeout=_transfer_timeout(size))
        if r.returncode != 0:
            log(f"    FAILED: {(r.stderr or r.stdout)[:300]}"); ok_all = False; continue
        ok, why = _confirm_in_remote(args, name, o)
        if not ok:
            log(f"    FAILED confirm after upload ({why}) -- not marking pushed")
            ok_all = False; continue
        log(f"    confirmed in remote ({why})")
        o["pushed"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_state(state)
    for f in ("MANIFEST.jsonl", "archive-state.json"):
        src = os.path.join(OUT, f)
        if args.bucket: _wrangler(args.bucket, "put", f"{args.bucket}/{f}", f"--file={src}", "--remote",
                                  timeout=_transfer_timeout(os.path.getsize(src) if os.path.exists(src) else 0))
        else: _rclone(args.remote, "copy", src, args.remote, timeout=600)
    return 0 if ok_all else 1

def cmd_verify(args):
    """Pull each pushed object back down; check sha256, then extract and diff
    member list + per-file line counts against what is still on local disk."""
    state = load_state()
    todo = [n for n, o in state["objects"].items() if o.get("pushed") and not o.get("verified")]
    if not todo: log("  nothing pushed-and-unverified"); return 0
    ok_all = True
    for name in sorted(todo):
        o = state["objects"][name]
        log(f"  verify {name}")
        with tempfile.TemporaryDirectory() as td:
            local, r = _remote_get(args, name, td, o.get("archive_bytes") or 0)
            if r.returncode != 0 or not os.path.exists(local):
                log(f"    FAILED download: {(r.stderr or r.stdout)[:200]}"); ok_all = False
                if _is_missing_key(r):
                    # The remote says the key is absent, so `pushed` is false.
                    # Left set, push skips this object forever and verify fails
                    # forever (codex-sessions/2026-07, 2026-09-21 onward). Clear
                    # it so the next push re-uploads. Any other failure (auth,
                    # network) leaves `pushed` alone.
                    o["pushed"] = None
                    save_state(state)
                    log(f"    key absent in remote -- cleared 'pushed'; next push re-uploads it")
                continue
            got = sha256(local)
            if got != o["sha256"]:
                log(f"    FAILED sha256: {got[:16]} != {o['sha256'][:16]}"); ok_all = False; continue
            log(f"    sha256 ok")
            ex = os.path.join(td, "x"); os.makedirs(ex)
            d = subprocess.run(["zstd", "-d", "-c", local], capture_output=True)
            tp = os.path.join(td, "a.tar")
            with open(tp, "wb") as f: f.write(d.stdout)
            with tarfile.open(tp) as tf: tf.extractall(ex, filter="data")
            members = sorted(m for m in o["members"])
            bad = 0; checked = 0; lines = 0
            for rel in members:
                x = os.path.join(ex, rel)
                if not os.path.exists(x):
                    log(f"    MISSING in archive: {rel}"); bad += 1; continue
                lines += count_lines(x)
                loc = os.path.join(HOME, rel)
                if os.path.exists(loc):
                    checked += 1
                    if sha256(x) != sha256(loc):
                        log(f"    CONTENT MISMATCH: {rel}"); bad += 1
            if lines != o["line_count"]:
                log(f"    LINE COUNT MISMATCH: {lines:,} != {o['line_count']:,}"); bad += 1
            if bad:
                log(f"    FAILED ({bad} problems)"); ok_all = False; continue
            log(f"    restored {len(members)} members, {lines:,} lines, "
                f"{checked} byte-compared against local -- OK")
            state["objects"][name]["verified"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_state(state)
    return 0 if ok_all else 1

def cmd_prune(args):
    """Delete local transcripts covered by a VERIFIED remote object.

    --tree scopes it. Claude's own `cleanupPeriodDays` already retires
    ~/.claude/projects on a 90-day clock, so pruning that tree here would just
    race a mechanism that already works. Codex has no retention of any kind --
    oldest session on disk is 2025-09-12 and nothing has ever removed one -- so
    that is what this is for.
    """
    state = load_state()
    ready = {n: o for n, o in state["objects"].items() if o.get("verified")}
    if args.tree:
        ready = {n: o for n, o in ready.items() if o.get("tree") in args.tree}
        log(f"  scoped to tree(s): {', '.join(args.tree)}")
    if not ready: log("  nothing verified in scope -- refusing to delete anything"); return 0
    victims = []
    for n, o in ready.items():
        for rel in o["members"]:
            p = os.path.join(HOME, rel)
            if os.path.exists(p): victims.append((n, p, os.path.getsize(p)))
    tot = sum(v[2] for v in victims)
    log(f"  {len(victims):,} local files covered by {len(ready)} VERIFIED objects, {tot/1e9:.2f} GB")
    if not args.yes:
        log("  dry run. re-run with --yes to delete."); return 0
    removed = 0
    for _, p, _ in victims:
        try: os.remove(p); removed += 1
        except OSError as e: log(f"    could not remove {p}: {e}")
    log(f"  removed {removed:,} files, {tot/1e9:.2f} GB reclaimed")
    return 0

def cmd_rotate(args):
    """Age out archives older than --keep-months. Tombstones are never removed.

    The bytes are the expendable part; the index is not. A rotated object keeps
    its full record in archive-state.json (members, counts, digest) so the
    history of what existed survives the deletion of what it contained.
    """
    from datetime import date
    state = load_state()
    today = date.today()
    horizon = (today.year * 12 + today.month) - args.keep_months
    stale = []
    for name, o in sorted(state["objects"].items()):
        if o.get("rotated"): continue
        y, m = (int(x) for x in o["month"].split("-"))
        if (y * 12 + m) < horizon: stale.append((name, o))
    if not stale:
        log(f"  nothing older than {args.keep_months} months. "
            f"oldest kept: {min((o['month'] for o in state['objects'].values()), default='-')}")
        return 0
    freed = sum(o["archive_bytes"] for _, o in stale)
    log(f"  {len(stale)} object(s) past {args.keep_months} months, {freed/1e9:.2f} GB local:")
    for name, o in stale:
        log(f"    {name:34} {o['month']}  {o['file_count']:>5} files  {o['line_count']:>9,} lines")
    if not args.yes:
        log("  dry run. re-run with --yes.")
        log("  note: the remote copy is expired by the bucket lifecycle rule, not by this command,")
        log("        unless you also pass --remote.")
        return 0
    for name, o in stale:
        lp = os.path.join(OUT, name)
        if os.path.exists(lp):
            os.remove(lp); log(f"    removed local {name}")
        if args.remote:
            r = _rclone(args.remote, "delete", f"{args.remote}/{name}")
            log(f"    remote delete {name}: {'ok' if r.returncode == 0 else r.stderr[:120]}")
        state["objects"][name]["rotated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["objects"][name]["local_present"] = False
        save_state(state)
    _write_manifest(state)
    log(f"  {freed/1e9:.2f} GB reclaimed. {len(stale)} tombstone(s) retained in archive-state.json")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="plan",
                    choices=["plan", "pack", "push", "verify", "prune", "rotate"])
    ap.add_argument("--cutoff-days", type=int, default=45)
    ap.add_argument("--remote", default=os.environ.get("SESSION_ARCHIVE_REMOTE", ""))
    ap.add_argument("--bucket", default=os.environ.get("SESSION_ARCHIVE_BUCKET", ""),
                    help="R2 bucket name; uses wrangler instead of rclone "
                         "(objects must be under 315 MB)")
    ap.add_argument("--tree", action="append",
                    help="prune only: restrict to a tree "
                         "(claude | codex-sessions | codex-archived); repeatable")
    ap.add_argument("--keep-months", type=int, default=12,
                    help="rotate only: months of cold archive to retain")
    ap.add_argument("--yes", action="store_true", help="prune/rotate: actually delete")
    a = ap.parse_args()
    if a.command in ("push", "verify") and not (a.remote or a.bucket):
        log("--bucket NAME (wrangler) or --remote NAME (rclone) required"); return 2
    return {"plan": cmd_plan, "pack": cmd_pack, "push": cmd_push,
            "verify": cmd_verify, "prune": cmd_prune, "rotate": cmd_rotate}[a.command](a)

if __name__ == "__main__":
    sys.exit(main())
