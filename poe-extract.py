#!/usr/bin/env python3
"""Extract user-voice signals from Claude Code sessions for building a Poe character stack.

Unlike recall-scan (which scores sessions for reusable prompt recipes), this tool mines
the USER turns across all sessions to codify how Nino thinks, decides, corrects, and
pushes back. The output is a queryable voice corpus — a "Poe" in the Altered Carbon sense.

Storage lives in the same SQLite file as recall-cli (~/.claude/recall.db) under the
voice_signals table + FTS5 index, so Poe and recipes share one corpus.

Usage:
    poe-extract.py extract [--limit N] [--since DAYS]   scan all JSONL -> corpus.jsonl
    poe-extract.py extract --session PATH                scan one JSONL -> DB (hook)
    poe-extract.py publish                               corpus.jsonl -> DB
    poe-extract.py assemble                              DB -> stack.md
    poe-extract.py query TERMS [--limit N]               FTS5 search -> markdown
    poe-extract.py run                                   extract + publish + assemble
    poe-extract.py init                                  ensure DB schema exists
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
POE_DIR = Path.home() / ".claude" / "poe"
CORPUS_PATH = POE_DIR / "corpus.jsonl"
STACK_PATH = POE_DIR / "stack.md"
RECALL_DB = Path.home() / ".claude" / "recall.db"

NOISE_PREFIXES = (
    "[Request interrupted",
    "This session is being continued",
    "Caveat: The messages below",
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
)

# Messages dominated by pasted logs/code/tool output are not user voice.
# Heuristic: high ratio of non-prose characters or very long.
MAX_USER_MSG_LEN = 4000
MIN_USER_MSG_LEN = 2  # lowered from 4 to capture bare "go" / "ok" redirects

# Signal patterns. Each tuple: (signal_type, compiled regex, short label)
def _c(p): return re.compile(p, re.IGNORECASE)

SIGNAL_PATTERNS = [
    # === CORRECTIONS — negative feedback on prior assistant action ===
    ("correction", _c(r"^\s*(no|nope|stop|wait|hold on|don't|do not)\b"), "opening-negative"),
    ("correction", _c(r"\bthat(?:'s| is)\s+(not|wrong|incorrect|bad)\b"), "that-is-wrong"),
    ("correction", _c(r"\byou(?:'re| are)\s+(wrong|incorrect|missing|off)\b"), "you-are-wrong"),
    ("correction", _c(r"\bwhy (did|would) you\b"), "why-did-you"),
    ("correction", _c(r"\b(undo|revert|roll ?back|back out|unwind)\b"), "undo"),
    ("correction", _c(r"\bnot what I (asked|wanted|meant)\b"), "not-what-i-asked"),
    ("correction", _c(r"\b(over[- ]?engineer|over[- ]?complicated|too much|scope creep)\b"), "over-engineered"),

    # === PREFERENCES — explicit rules about how things should be ===
    ("preference", _c(r"\bI (prefer|like|want|hate|dislike|don't (want|like))\b"), "i-prefer"),
    ("preference", _c(r"\bwe (prefer|always|never) (use|write|do|have|go|commit|push|call)\b"), "we-convention"),
    ("preference", _c(r"\bwe (don't|do not) (use|write|do|want|need to|commit|push|call|allow)\b"), "we-dont"),
    ("preference", _c(r"\b(always|never) (use|write|call|do|add|create|commit|push)\b"), "always-never"),
    ("preference", _c(r"\b(make sure|ensure) (you|to|that)\b"), "make-sure"),
    ("preference", _c(r"\bfrom now on\b"), "from-now-on"),
    ("preference", _c(r"\bgoing forward\b"), "going-forward"),

    # === RATIONALE — reasons behind decisions ===
    ("rationale", _c(r"\bbecause\b"), "because"),
    ("rationale", _c(r"\bthe reason (is|we|I)\b"), "the-reason"),
    ("rationale", _c(r"\b(we|I) got burned\b"), "got-burned"),
    ("rationale", _c(r"\blast time\b"), "last-time"),
    ("rationale", _c(r"\botherwise\b"), "otherwise"),
    ("rationale", _c(r"\bthat way\b"), "that-way"),

    # === DECLARATIONS — imperative rules, often first messages ===
    ("declaration", _c(r"^\s*(use|don't|do not|keep|avoid|skip|drop|remove|add)\s+\w"), "imperative-rule"),

    # === APPROVALS — validated choices (short msgs are stronger signal) ===
    ("approval", _c(r"^\s*(perfect|exactly|yes exactly|good call|nice|that's (it|right)|correct)\b"), "short-approval"),
    ("approval", _c(r"\bship it\b"), "ship-it"),
    # redirect-go: short messages that mean "you already have authorization, keep moving".
    # The prior_assistant column captures what Claude was asking — useful for
    # learning which question shapes Nino routinely overrides with "go".
    ("approval", _c(r"^\s*(go|proceed|continue|keep going|do all|all of it|do it|do the rest|move on|next|push|run it|execute)[.!]?\s*$"), "redirect-go"),

    # === REJECTIONS with alternative ===
    ("rejection", _c(r"\binstead\b"), "instead"),
    ("rejection", _c(r"\brather than\b"), "rather-than"),
]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS voice_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    project         TEXT,
    timestamp       TEXT,
    signal_type     TEXT NOT NULL,
    label           TEXT NOT NULL,
    phrase          TEXT NOT NULL,
    message         TEXT,
    prior_assistant TEXT,
    phrase_hash     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, signal_type, phrase_hash)
);

CREATE INDEX IF NOT EXISTS voice_signals_signal_idx
    ON voice_signals(signal_type, label);
CREATE INDEX IF NOT EXISTS voice_signals_project_idx
    ON voice_signals(project);
CREATE INDEX IF NOT EXISTS voice_signals_session_idx
    ON voice_signals(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS voice_signals_fts USING fts5(
    phrase, message, signal_type, label, project,
    content=voice_signals, content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS voice_signals_ai AFTER INSERT ON voice_signals BEGIN
    INSERT INTO voice_signals_fts(rowid, phrase, message, signal_type, label, project)
    VALUES (new.id, new.phrase, new.message, new.signal_type, new.label, new.project);
END;

CREATE TRIGGER IF NOT EXISTS voice_signals_ad AFTER DELETE ON voice_signals BEGIN
    INSERT INTO voice_signals_fts(voice_signals_fts, rowid, phrase, message, signal_type, label, project)
    VALUES ('delete', old.id, old.phrase, old.message, old.signal_type, old.label, old.project);
END;
"""


def db_connect() -> sqlite3.Connection:
    RECALL_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(RECALL_DB))
    conn.executescript(SCHEMA_SQL)
    return conn


def phrase_hash(phrase: str) -> str:
    """Stable hash for dedup — normalize whitespace and case."""
    norm = re.sub(r"\s+", " ", phrase.lower()).strip()[:200]
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def project_label(dirname: str) -> str:
    """Normalize a project directory name into a readable label."""
    return dirname.replace("-Users-nino-", "").replace("-", "/")


def iter_user_messages(session_file: Path):
    """Yield (timestamp, text, prior_assistant_text) for each real user turn.
    Deduplicates messages within a session (sidechain entries duplicate main chain)."""
    prior_assistant = ""
    seen_msgs: set[str] = set()
    try:
        with open(session_file, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = obj.get("type")
                ts = obj.get("timestamp", "")

                if t == "assistant":
                    msg = obj.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        texts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        if texts:
                            prior_assistant = " ".join(texts)[:600]
                    continue

                if t != "user":
                    continue

                msg = obj.get("message", {})
                content = msg.get("content", "")

                # Extract only real text user messages, skip tool_result
                text = None
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_result":
                            text = None
                            break
                        if c.get("type") == "text":
                            text = c.get("text", "")
                            break

                if not text:
                    continue

                text = text.strip()
                if len(text) < MIN_USER_MSG_LEN or len(text) > MAX_USER_MSG_LEN * 4:
                    continue
                if text.startswith(NOISE_PREFIXES):
                    continue
                # Skip messages that are mostly tags/code dumps
                if text.count("<") > 20 or text.count("```") > 6:
                    continue

                # Dedupe within session (sidechain duplicates)
                msg_key = text[:200]
                if msg_key in seen_msgs:
                    continue
                seen_msgs.add(msg_key)

                yield ts, text[:MAX_USER_MSG_LEN], prior_assistant
    except Exception:
        return


# Markers that suggest a regex hit landed inside pasted content, not Nino's voice
PASTE_MARKERS = re.compile(
    r'(\*\*[^*]+\*\*|`[^`]+`|\{"|"\}|://|\\n|\\\"|"detail":|^\s*[-*]\s|^\s*\d+\.\s)',
    re.MULTILINE,
)

# If these markers appear in the raw message, restrict scanning to the intro only
HEAVY_PASTE_MARKERS = re.compile(r'(```|^##+ |\n- \*\*|\n\d+\. \*\*)', re.MULTILINE)


def extract_signals(text: str):
    """Return list of (signal_type, label, matched_phrase) for a user message."""
    hits = []
    seen = set()

    # If the message looks like it contains pasted blocks, only scan the intro
    if HEAVY_PASTE_MARKERS.search(text):
        scan = text[:300]
    else:
        scan = text[:800]

    for stype, pat, label in SIGNAL_PATTERNS:
        m = pat.search(scan)
        if not m:
            continue
        key = (stype, label)
        if key in seen:
            continue

        # Extract surrounding sentence containing the match
        start = max(0, m.start() - 60)
        end = min(len(scan), m.end() + 120)
        phrase = scan[start:end].strip()

        # Skip if the phrase itself looks like pasted content
        if PASTE_MARKERS.search(phrase):
            continue
        # Skip short or mostly-URL phrases — but redirect-go is intentionally short.
        # The signal IS the brevity ("go" alone after a hesitation question);
        # the context lives in prior_assistant.
        if label != "redirect-go" and len(phrase) < 20:
            continue

        seen.add(key)
        hits.append((stype, label, phrase))
    return hits


def _extract_from_file(jf: Path) -> list[dict]:
    """Extract all signal records from a single JSONL file."""
    project = project_label(jf.parent.name)
    session_id = jf.stem
    records: list[dict] = []
    for ts, text, prior in iter_user_messages(jf):
        hits = extract_signals(text)
        for stype, label, phrase in hits:
            records.append({
                "project": project,
                "session_id": session_id,
                "timestamp": ts,
                "signal": stype,
                "label": label,
                "phrase": phrase,
                "message": text[:1200],
                "prior_assistant": prior[:400],
            })
    return records


def _upsert_signals(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Insert records into voice_signals, skipping duplicates. Returns inserted count."""
    inserted = 0
    for r in records:
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO voice_signals
                    (session_id, project, timestamp, signal_type, label,
                     phrase, message, prior_assistant, phrase_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["session_id"],
                    r.get("project"),
                    r.get("timestamp") or "",
                    r["signal"],
                    r["label"],
                    r["phrase"],
                    r.get("message", ""),
                    r.get("prior_assistant", ""),
                    phrase_hash(r["phrase"]),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
        except sqlite3.Error as e:
            print(f"  db error: {e}", file=sys.stderr)
    conn.commit()
    return inserted


def cmd_extract(limit: int | None, since_days: int | None, session: str | None) -> None:
    POE_DIR.mkdir(parents=True, exist_ok=True)

    # Single-session mode: parse one JSONL, upsert to DB, skip corpus.jsonl
    if session:
        jf = Path(session).expanduser().resolve()
        if not jf.exists():
            print(f"Session file not found: {jf}", file=sys.stderr)
            sys.exit(1)
        records = _extract_from_file(jf)
        conn = db_connect()
        inserted = _upsert_signals(conn, records)
        conn.close()
        print(f"Session {jf.stem}: {len(records)} signals, {inserted} new", file=sys.stderr)
        return

    cutoff = None
    if since_days is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=since_days)

    files = []
    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            if cutoff:
                mtime = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    continue
            files.append(jf)

    if limit:
        files = files[:limit]

    stats = Counter()
    written = 0

    with open(CORPUS_PATH, "w") as out:
        for i, jf in enumerate(files, 1):
            if i % 200 == 0:
                print(f"  [{i}/{len(files)}] scanned, {written} signals...", file=sys.stderr)

            stats["files_scanned"] += 1
            records = _extract_from_file(jf)
            for rec in records:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                stats[f"signal:{rec['signal']}"] += 1
                written += 1
            stats["messages_scanned"] += 0  # message stats collapsed into records path

    print(f"\nExtraction complete:", file=sys.stderr)
    print(f"  files scanned:    {stats['files_scanned']}", file=sys.stderr)
    print(f"  signals written:  {written}", file=sys.stderr)
    for k in sorted(stats):
        if k.startswith("signal:"):
            print(f"    {k[7:]:12} {stats[k]}", file=sys.stderr)
    print(f"  corpus: {CORPUS_PATH}", file=sys.stderr)


def cmd_publish() -> None:
    """Load corpus.jsonl into the DB."""
    if not CORPUS_PATH.exists():
        print(f"No corpus at {CORPUS_PATH} — run extract first.", file=sys.stderr)
        sys.exit(1)
    records: list[dict] = []
    with open(CORPUS_PATH) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    conn = db_connect()
    inserted = _upsert_signals(conn, records)
    total = conn.execute("SELECT COUNT(*) FROM voice_signals").fetchone()[0]
    conn.close()
    print(
        f"Published: {len(records)} records, {inserted} new inserts, {total} total in DB",
        file=sys.stderr,
    )


def cmd_init() -> None:
    conn = db_connect()
    count = conn.execute("SELECT COUNT(*) FROM voice_signals").fetchone()[0]
    conn.close()
    print(f"Schema ready at {RECALL_DB} — voice_signals has {count} rows", file=sys.stderr)


def cmd_query(terms: list[str], limit: int) -> None:
    """FTS5 search voice_signals, emit markdown block ready to paste."""
    if not RECALL_DB.exists():
        print(f"No DB at {RECALL_DB} — run publish first.", file=sys.stderr)
        sys.exit(1)
    conn = db_connect()
    query = " ".join(terms).strip()
    if not query:
        print("Query terms required.", file=sys.stderr)
        sys.exit(1)

    # FTS5 MATCH with phrase-first ranking
    try:
        rows = conn.execute(
            """
            SELECT v.signal_type, v.label, v.phrase, v.project, v.session_id, v.timestamp
            FROM voice_signals_fts f
            JOIN voice_signals v ON v.id = f.rowid
            WHERE voice_signals_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError as e:
        # Fall back to LIKE if FTS5 syntax rejects the query
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT signal_type, label, phrase, project, session_id, timestamp
            FROM voice_signals
            WHERE phrase LIKE ? OR message LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()

    conn.close()

    if not rows:
        print(f"No matches for: {query}", file=sys.stderr)
        return

    by_type: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_type[r[0]].append(r)

    print(f"# Poe on: {query}")
    print()
    print(f"_{len(rows)} matching signals from Nino's past sessions._")
    print()
    type_order = ["correction", "preference", "rationale", "rejection", "declaration", "approval"]
    headers = {
        "correction": "## Corrections (what Nino pushed back on)",
        "preference": "## Preferences (stated rules)",
        "rationale": "## Rationale (reasoning given)",
        "rejection": "## Alternatives (what Nino picked instead)",
        "declaration": "## Imperatives",
        "approval": "## Validated calls",
    }
    for t in type_order:
        if t not in by_type:
            continue
        print(headers[t])
        print()
        for stype, label, phrase, project, session_id, ts in by_type[t]:
            phrase_clean = re.sub(r"\s+", " ", phrase).strip()
            proj_short = (project or "?").split("/")[-1] if project else "?"
            print(f"- \"{phrase_clean}\" _({proj_short}, `{label}`)_")
        print()


def cmd_assemble() -> None:
    by_signal: dict[str, list[dict]] = defaultdict(list)
    by_signal_label: dict[tuple[str, str], list[dict]] = defaultdict(list)
    projects = Counter()

    # Prefer DB as source of truth; fall back to corpus.jsonl
    if RECALL_DB.exists():
        conn = db_connect()
        db_count = conn.execute("SELECT COUNT(*) FROM voice_signals").fetchone()[0]
    else:
        db_count = 0
        conn = None

    if db_count > 0 and conn is not None:
        rows = conn.execute(
            "SELECT signal_type, label, phrase, project FROM voice_signals"
        ).fetchall()
        conn.close()
        for stype, label, phrase, project in rows:
            rec = {"signal": stype, "label": label, "phrase": phrase, "project": project or "?"}
            by_signal[stype].append(rec)
            by_signal_label[(stype, label)].append(rec)
            projects[rec["project"]] += 1
    else:
        if conn is not None:
            conn.close()
        if not CORPUS_PATH.exists():
            print(f"No DB rows and no corpus at {CORPUS_PATH} — run extract first.", file=sys.stderr)
            sys.exit(1)
        with open(CORPUS_PATH) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                by_signal[rec["signal"]].append(rec)
                by_signal_label[(rec["signal"], rec["label"])].append(rec)
                projects[rec.get("project", "?")] += 1

    total = sum(len(v) for v in by_signal.values())

    def dedupe_by_phrase(recs: list[dict], limit: int = 20) -> list[dict]:
        """Keep one rep per near-duplicate phrase."""
        seen: set[str] = set()
        out = []
        for r in recs:
            key = re.sub(r"\s+", " ", r["phrase"].lower()).strip()[:120]
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
            if len(out) >= limit:
                break
        return out

    lines: list[str] = []
    lines.append("# Poe — A Serialized Nino")
    lines.append("")
    lines.append(
        "A character stack extracted from prior Claude Code sessions. Load this as "
        "system-prompt context when you want the assistant to vet ideas the way Nino "
        "would — with the same red lines, rationale, and taste."
    )
    lines.append("")
    lines.append(f"- **Corpus size**: {total} signals across {len(projects)} projects")
    lines.append(f"- **Generated**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")

    section_order = [
        ("correction", "## Red lines — what Nino rejects", "These are patterns where Nino pushed back, corrected, or called something wrong. Treat them as non-negotiables unless the context clearly differs."),
        ("preference", "## Rules — how Nino wants things done", "Explicit conventions Nino has declared. Follow them by default."),
        ("rationale", "## Rationale — the 'because' behind decisions", "Reasons Nino has given for choices. Use these to explain trade-offs the way Nino would."),
        ("rejection", "## Alternatives — what Nino picks instead", "When Nino rejects an approach, these show what he reaches for instead."),
        ("declaration", "## Imperatives — first-move instructions", "Common opening rules Nino issues at the start of a task."),
        ("approval", "## Validated judgment calls", "Non-obvious approaches Nino confirmed worked. Don't re-litigate these."),
    ]

    for stype, header, blurb in section_order:
        recs = by_signal.get(stype, [])
        if not recs:
            continue
        lines.append(header)
        lines.append("")
        lines.append(f"_{blurb}_")
        lines.append("")
        lines.append(f"**Signal count**: {len(recs)}")
        lines.append("")

        # Group by label, rank by frequency
        label_counts = Counter(r["label"] for r in recs)
        for label, count in label_counts.most_common():
            label_recs = by_signal_label[(stype, label)]
            reps = dedupe_by_phrase(label_recs, limit=6)
            lines.append(f"### `{label}` ({count} occurrences)")
            lines.append("")
            for r in reps:
                phrase = re.sub(r"\s+", " ", r["phrase"]).strip()
                proj = r.get("project", "?")
                lines.append(f"- \"{phrase}\" — _{proj}_")
            lines.append("")

    # Project breakdown
    lines.append("## Project footprint")
    lines.append("")
    lines.append("Where these signals came from (top 20):")
    lines.append("")
    for proj, count in projects.most_common(20):
        lines.append(f"- `{proj}` — {count}")
    lines.append("")

    STACK_PATH.write_text("\n".join(lines))
    print(f"Stack written: {STACK_PATH} ({total} signals)", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="scan sessions and write corpus.jsonl (or DB for --session)")
    e.add_argument("--limit", type=int, default=None, help="max number of session files")
    e.add_argument("--since", type=int, default=None, help="only sessions newer than N days")
    e.add_argument("--session", type=str, default=None, help="single JSONL file -> DB (hook mode)")

    sub.add_parser("init", help="ensure DB schema exists")
    sub.add_parser("publish", help="load corpus.jsonl -> recall.db")
    sub.add_parser("assemble", help="build stack.md from DB (or corpus.jsonl)")

    q = sub.add_parser("query", help="FTS5 search Poe -> markdown block")
    q.add_argument("terms", nargs="+", help="search terms")
    q.add_argument("--limit", type=int, default=25, help="max results")

    sub.add_parser("run", help="extract + publish + assemble")

    args = p.parse_args()

    if args.cmd == "extract":
        cmd_extract(args.limit, args.since, args.session)
    elif args.cmd == "init":
        cmd_init()
    elif args.cmd == "publish":
        cmd_publish()
    elif args.cmd == "assemble":
        cmd_assemble()
    elif args.cmd == "query":
        cmd_query(args.terms, args.limit)
    elif args.cmd == "run":
        cmd_extract(None, None, None)
        cmd_publish()
        cmd_assemble()


if __name__ == "__main__":
    main()
