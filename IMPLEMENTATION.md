# Implementation Plan — Quality Maturity Levels 3-6

Concrete file-level plans for each phase. Each level has a gate condition that must pass before moving to the next.

Current state: 12 saved entries, 0 verified. 89 projects, ~1500 sessions/month.

---

## Level 3: Correlated (Phase 1)

**Gate to enter:** Current tool works (Level 1-2 complete).
**Gate to exit:** 50+ entries with outcome labels. At least one statistically significant correlation (p < 0.05) between a process metric and an outcome.

### Step 1: Schema extension (DB migration v3)

**File:** `recall-cli.py` → `_migrate()`

Add columns to `recipes` table:

```sql
ALTER TABLE recipes ADD COLUMN commits_produced INTEGER DEFAULT NULL;
ALTER TABLE recipes ADD COLUMN user_satisfaction INTEGER DEFAULT NULL
  CHECK(user_satisfaction BETWEEN 1 AND 5);
ALTER TABLE recipes ADD COLUMN compliance_grade TEXT DEFAULT NULL;
ALTER TABLE recipes ADD COLUMN process_score REAL DEFAULT NULL;
ALTER TABLE recipes ADD COLUMN session_shape TEXT DEFAULT NULL;
ALTER TABLE recipes ADD COLUMN thrash_ratio REAL DEFAULT NULL;
ALTER TABLE recipes ADD COLUMN tokens_per_output INTEGER DEFAULT NULL;
```

**Why store analysis results:** So we can correlate them with outcomes later without re-analyzing old sessions (which may have been deleted).

### Step 2: `/recall verify <id>` command

**File:** `recall-cli.py` → new `cmd_verify()`

```
/recall verify <id> --outcome pass|fail --satisfaction 1-5 --followup yes|no
```

Implementation:
- Look up entry by ID prefix match (same as `cmd_show`)
- UPDATE the outcome columns
- If `--outcome` not provided, auto-detect from session file:
  - Scan for `git commit` in Bash tool calls → `commits_produced`
  - Check if session JSONL still exists for the entry
- Print confirmation with updated fields

Wire into argparse:
```python
verify_p = sub.add_parser("verify")
verify_p.add_argument("id")
verify_p.add_argument("--outcome", choices=["pass", "fail"], default=None)
verify_p.add_argument("--satisfaction", type=int, choices=range(1, 6), default=None)
verify_p.add_argument("--followup", choices=["yes", "no"], default=None)
```

### Step 3: Auto-populate analysis metrics on save

**File:** `recall-cli.py` → `cmd_save()`

When saving a new entry, also run `_run_analysis()` on the session file and store the results:

```python
if session_file:
    analysis = _run_analysis(session_file)
    if "error" not in analysis:
        # Store alongside entry for future correlation
        compliance_grade = analysis["compliance"]["grade"]
        process_score = analysis["process"]["score"]
        session_shape = analysis["session_shape"]["session_shape"]
        thrash_ratio = analysis["thrash_analysis"]["thrash_ratio"]
        tokens_per_output = analysis["cost_efficiency"]["tokens_per_output"]
```

Update the INSERT to include these columns.

### Step 4: Backfill existing entries

**File:** `recall-cli.py` → new `cmd_backfill()`

One-time command to backfill analysis metrics on existing entries:

```
/recall backfill
```

For each entry with `compliance_grade IS NULL`:
- Find session file by `session_id`
- Run `_run_analysis()`
- UPDATE the analysis columns
- Report: "Backfilled 8/12 entries (4 session files not found)"

### Step 5: `/recall correlate` command

**File:** `recall-cli.py` → new `cmd_correlate()`

```
/recall correlate
```

Requirements: minimum 50 entries with `outcome_verified IS NOT NULL`.

Compute for each process metric vs outcome:
- **Point-biserial correlation** between each metric and `outcome_verified` (continuous vs binary)
- **Mean comparison**: avg thrash_ratio for pass vs fail outcomes
- **Effect size**: how different are the distributions?

Output:
```json
{
  "sample_size": 62,
  "verified_pass": 48,
  "verified_fail": 14,
  "correlations": {
    "compliance_grade_numeric": {"correlation": 0.34, "p_value": 0.006, "significant": true},
    "thrash_ratio": {"correlation": -0.12, "p_value": 0.35, "significant": false},
    "process_score": {"correlation": 0.08, "p_value": 0.54, "significant": false},
    "tokens_per_output": {"correlation": -0.21, "p_value": 0.10, "significant": false}
  },
  "mean_comparison": {
    "thrash_ratio": {"pass_mean": 1.8, "fail_mean": 2.3},
    "compliance_grade_numeric": {"pass_mean": 78, "fail_mean": 52}
  },
  "conclusion": "compliance_grade is the only metric with significant correlation to outcomes. Process metrics show no significant predictive power — consider dropping them from scoring."
}
```

Implementation: Python stdlib `statistics` module for means/stdev. For correlation, implement Pearson's r manually (avoid numpy dependency):

```python
def _pearson_r(x: list[float], y: list[float]) -> tuple[float, float]:
    """Pearson correlation coefficient and approximate p-value."""
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
    den_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0, 1.0
    r = num / (den_x * den_y)
    # t-test approximation for p-value
    import math
    if abs(r) >= 1.0:
        return r, 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    # Two-tailed p-value approximation (good enough for n > 30)
    p = 2 * (1 - _students_t_cdf(abs(t), n - 2))
    return round(r, 4), round(p, 4)
```

Use a simple t-distribution CDF approximation (Abramowitz & Stegun) to avoid scipy.

### Step 6: Update `/recall save` prompt in `recall.md`

Add instruction: after saving, prompt user "Rate this session: /recall verify <id> --outcome pass|fail --satisfaction 1-5"

### Data collection target

At ~50 sessions/day across projects, and assuming you verify 2-3 per day, reaching 50 labeled entries takes ~3 weeks. Reaching 100 takes ~6 weeks.

To accelerate: run `/recall-scan` weekly and batch-verify the top candidates.

---

## Level 4: Calibrated (Phase 3)

**Gate to enter:** 100+ verified entries. At least one significant correlation from Level 3.
**Gate to exit:** `thresholds.json` values derived from empirical data with `_calibrated_from` metadata. Control charts showing process variation.

### Step 1: `/recall calibrate` command

**File:** `recall-cli.py` → new `cmd_calibrate()`

```
/recall calibrate
```

Requires 100+ entries with outcomes.

For each process metric, compute percentiles from verified-pass sessions:
- p25, p50 (median), p75, p90
- Set thresholds at p75 of known-good (pass) sessions
- Compare to current `thresholds.json` values

Output:
```json
{
  "sample_size": 112,
  "pass_sessions": 89,
  "calibration": {
    "thrash_ratio": {
      "current_warning": 1.5,
      "empirical_p25": 1.1,
      "empirical_p50": 1.6,
      "empirical_p75": 2.4,
      "empirical_p90": 3.8,
      "recommended_warning": 2.4,
      "recommended_critical": 3.8
    },
    "tokens_per_output": {
      "current_excellent": 3000,
      "empirical_p25": 4200,
      "empirical_p50": 12000,
      "empirical_p75": 38000,
      "recommended_excellent": 4200,
      "recommended_good": 12000
    }
  },
  "action": "Run /recall calibrate --apply to update thresholds.json"
}
```

### Step 2: `--apply` flag

`/recall calibrate --apply` writes the empirical thresholds to `thresholds.json` with metadata:

```json
{
  "_calibrated_from": {
    "date": "2026-05-15",
    "sample_size": 112,
    "pass_rate": 0.79,
    "heuristic_version": 3
  }
}
```

### Step 3: Control charts

**File:** new `recall-charts.py` (optional, stdout ASCII)

```
/recall chart thrash_ratio --days 90
```

ASCII sparkline showing metric over time with control limits drawn from calibration. Flag sessions outside control limits.

```
thrash_ratio (last 90 days, n=287)
UCL: 3.8  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
           ·     ·           ·   X
avg: 1.6  ─────────────────────────────────────────
           · · ·   · · · ·     · · ·   ·
LCL: 0.0  ─────────────────────────────────────────
          Mar 1                              May 30

X = out of control (2 sessions)
```

Implementation: no dependencies. Print to stdout using box-drawing characters. The `/recall` slash command renders it in Claude's monospace output.

### Step 4: Recalibration schedule

Add to `recall.md` instructions: "Run `/recall calibrate` monthly. If recommended thresholds differ from current by >20%, apply them and bump HEURISTIC_VERSION."

---

## Level 5: Predictive (Phase 4)

**Gate to enter:** 200+ verified entries with follow-up fix labels. Calibrated thresholds.
**Gate to exit:** A classifier that predicts follow-up fixes with >65% accuracy on held-out data (better than base rate).

### Step 1: Feature extraction table

**File:** `recall-cli.py` → DB migration v4

New table `session_features`:
```sql
CREATE TABLE session_features (
    session_id      TEXT PRIMARY KEY,
    project_path    TEXT,
    analyzed_at     TEXT DEFAULT (datetime('now')),
    compliance_score REAL,
    process_score   REAL,
    session_shape   TEXT,
    thrash_ratio    REAL,
    tokens_per_output INTEGER,
    total_tokens    INTEGER,
    total_cost      REAL,
    tool_misuses    INTEGER,
    anti_pattern_count INTEGER,
    bash_calls      INTEGER,
    edit_count      INTEGER,
    unique_files    INTEGER,
    prompt_count    INTEGER,
    duration_min    REAL,
    model_primary   TEXT,
    had_commit      INTEGER,
    outcome_verified INTEGER DEFAULT NULL,
    had_followup_fix INTEGER DEFAULT NULL
);
```

### Step 2: Batch feature extraction

**File:** `recall-cli.py` → new `cmd_extract()`

```
/recall extract --days 90
```

Runs `_run_analysis()` on all sessions and populates `session_features`. This is the training dataset.

### Step 3: Follow-up fix auto-detection

**File:** `recall-cli.py` → new `_detect_followup_fixes()`

For each session, check if the same project had another session within 2 hours that edited any of the same files. If yes, mark `had_followup_fix = 1`.

```python
def _detect_followup_fixes():
    """Cross-reference sessions to detect follow-up fix patterns."""
    # For each session, get: project, timestamp, files_written
    # Sort by project + timestamp
    # For consecutive sessions in same project:
    #   If time gap < 2h AND file overlap > 0 → mark earlier session as had_followup_fix
```

This is the key automation that makes Level 5 possible without manual labeling.

### Step 4: Logistic regression classifier

**File:** new `recall-predict.py`

No external dependencies. Implement logistic regression from scratch (it's ~30 lines of math):

```python
def _sigmoid(z): return 1 / (1 + math.exp(-z))

def _logistic_regression(X, y, lr=0.01, epochs=1000):
    """Train logistic regression with gradient descent. No numpy."""
    n, m = len(X), len(X[0])
    weights = [0.0] * m
    bias = 0.0
    for _ in range(epochs):
        for i in range(n):
            z = sum(w * x for w, x in zip(weights, X[i])) + bias
            pred = _sigmoid(z)
            error = pred - y[i]
            for j in range(m):
                weights[j] -= lr * error * X[i][j]
            bias -= lr * error
    return weights, bias
```

Features: compliance_score, thrash_ratio, tokens_per_output, tool_misuses, anti_pattern_count, edit_count, prompt_count.

Target: `had_followup_fix`.

### Step 5: `/recall predict` command

```
/recall predict --session-id <id>
```

Runs the trained model on a session's features. Output:

```json
{
  "session_id": "abc123",
  "followup_fix_probability": 0.72,
  "risk_level": "high",
  "top_risk_factors": ["thrash_ratio: 4.2 (p90 = 3.8)", "tool_misuses: 5"],
  "model_accuracy": 0.68,
  "training_size": 234
}
```

### Step 6: SessionEnd hook integration

Add to `recall.md` instructions for optional hook:

```json
{
  "hooks": {
    "SessionEnd": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 ~/.claude/commands/recall-cli.py predict --last-session >> ~/.claude/recall-predictions.log 2>&1",
        "timeout": 15
      }]
    }]
  }
}
```

---

## Level 6: Benchmarked (Phase 5)

**Gate to enter:** Predictive model works. Multiple users interested.
**Gate to exit:** Published community baseline with opt-in data from 10+ users.

### Step 1: Anonymous export

**File:** `recall-cli.py` → new `cmd_export()`

```
/recall export --format anonymous
```

Exports metrics-only JSON (no session content, no file paths, no prompts):

```json
{
  "export_version": 1,
  "user_hash": "a1b2c3",
  "claude_code_version": "2.1.81",
  "heuristic_version": 3,
  "sessions": [
    {
      "date": "2026-03-24",
      "compliance_score": 70,
      "process_score": 55,
      "session_shape": "research_then_build",
      "thrash_ratio": 1.8,
      "outcome_verified": true,
      "had_followup_fix": false,
      "model_primary": "opus"
    }
  ]
}
```

Privacy: hash user identity, strip all paths/content, only export structural metrics.

### Step 2: Community aggregation

**Out of scope for this repo.** Would require:
- A simple endpoint that accepts anonymous exports (GitHub Gist, or a tiny API)
- Aggregation script that computes community percentiles
- Published baseline JSON that users can download

### Step 3: `/recall benchmark` command

```
/recall benchmark
```

Compares your metrics against a downloaded community baseline:

```json
{
  "your_compliance_avg": 72,
  "community_p50": 68,
  "community_p75": 81,
  "your_percentile": 62,
  "your_followup_fix_rate": 0.18,
  "community_followup_fix_rate": 0.24,
  "summary": "Your compliance is above median. Your fix rate is below community average (good)."
}
```

---

## Implementation order and time estimates

| Step | Level | Files changed | New commands | Data needed | Can start |
|------|-------|--------------|--------------|-------------|-----------|
| L3.1 | 3 | recall-cli.py | — | — | Now |
| L3.2 | 3 | recall-cli.py, recall.md | verify | — | Now |
| L3.3 | 3 | recall-cli.py | — | — | Now |
| L3.4 | 3 | recall-cli.py | backfill | — | After L3.1 |
| L3.5 | 3 | recall-cli.py | correlate | 50+ verified | ~3 weeks |
| L3.6 | 3 | recall.md | — | — | After L3.2 |
| L4.1 | 4 | recall-cli.py | calibrate | 100+ verified | ~6 weeks |
| L4.2 | 4 | recall-cli.py | — | After L4.1 | ~6 weeks |
| L4.3 | 4 | recall-charts.py | chart | After L4.1 | ~6 weeks |
| L5.1 | 5 | recall-cli.py | — | — | After L4 |
| L5.2 | 5 | recall-cli.py | extract | — | After L5.1 |
| L5.3 | 5 | recall-cli.py | — | 90+ days data | After L5.2 |
| L5.4 | 5 | recall-predict.py | — | 200+ labeled | ~3 months |
| L5.5 | 5 | recall-cli.py | predict | After L5.4 | ~3 months |
| L5.6 | 5 | recall.md | — | After L5.5 | ~3 months |
| L6.1 | 6 | recall-cli.py | export | After L5 | ~4 months |
| L6.2 | 6 | external | — | 10+ users | TBD |
| L6.3 | 6 | recall-cli.py | benchmark | After L6.2 | TBD |

**Critical path:** L3.1-L3.4 can all ship now. L3.5 (correlate) is blocked on 50 verified entries. Everything after that chains from data volume.

**What to build today:** L3.1 through L3.4. Start verifying sessions immediately. Run `/recall correlate` when you hit 50.
