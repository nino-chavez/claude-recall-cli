# Recall CLI — Quality Measurement Roadmap

Paused 2026-03-24. Resume ~2026-06-24 with 3 months of accumulated session data.

## Implementation-session rerun — 2026-11-08

**The rerun found a specific instruction gap: reuse a worktree already assigned
by the session host before creating another checkout.** Three Claude
implementation episodes hit the same host isolation denial. All 11 rejected
edits later succeeded with identical replacement text and file-relative paths
in the assigned worktree. The four Codex episodes exposed other recoveries, but
no second recurring cause justified a configuration change.

The operator authorized this rerun and a wider date window after the first
pilot's invalid Claude sample. The investigation had a 45-minute ceiling. It
retained the earlier repository scope and exclusions, required actual code edits
and an ended implementation episode, and selected on eligibility rather than
failure or test success. The July calibration decision remains closed.

### Accepted implementation episodes

Source references below are session IDs and physical JSONL line numbers, not
transcript excerpts. Claude sources are under `~/.claude/projects/`; Codex
sources are under `~/.codex/sessions/` or `~/.codex/archived_sessions/`, including
`.jsonl.zst`. Dates are event dates in UTC. A worker's closing event establishes
its handoff, not the correctness or release of its output.

| Case | Session ID | UTC date and implementation | Assignment; code edit → result; end |
|---|---|---|---|
| C1 | `c11f19f7-cbe2-4d68-bc1e-98d01948e574` | Sep 25, Blueprint global-rules hook outcome | 9; 248 → 250; 809 |
| C2 | `2778421c-a489-45ef-955c-36b221e5101f` | Sep 25, Blueprint npm CLI fallback | 10; 235 → 238; 960 |
| C3 | `18ab1b0b-6739-45a3-aa1a-0ad75c4761ee` | Sep 25, Blueprint state-derive/global-rules paths | 9; 371 → 378; 1491 |
| C4 | `67e145da-2511-495c-9bca-b4c13ea7679e` | Sep 25, Blueprint allowlist inline comments | 9; 219 → 220; 803 |
| X1 | `01a1931e-43a8-7191-9eda-397ff99f39bc` | Oct 31, browse-tool background tabs | 9; 112 → 114; 315 |
| X2 | `01a19256-6447-75c3-b8b3-17d3d055af62` | Oct 31, browse-tool connection targets | 9; 97 → 99; 213 |
| X3 | `01a18536-6dcb-76b3-b22d-fa651fd41618` | Oct 29, Operator backup retention | 9; 89 → 91; 237 |
| X4 | `01a17455-330d-7402-a3f8-55ab6f1a44c2` | Oct 25, browse-tool capture metadata | 509; 583 → 588; 819 |

The review ranges run from each assignment through its end. X4 is restricted
to lines 509–819; later blog changes in that chat are excluded. X1–X3 are CLI
implementation workers, and X4 is a parent implementation episode. Their
parents/reviewers are not additional cases. X3 explicitly handed off uncommitted
work. Claude's four cases are separate fixes within one coordinated Blueprint
batch, with three touching the same hook. They are correlated observations,
not four independent fleet samples. The different dates, tasks and harness
roles prevent a meaningful performance ranking.

The parent independently verified assignment/edit/result/end records. Later
merge receipts confirm Blueprint PRs
[#58](https://github.com/nino-chavez/blueprint/pull/58),
[#50](https://github.com/nino-chavez/blueprint/pull/50),
[#52](https://github.com/nino-chavez/blueprint/pull/52), and
[#53](https://github.com/nino-chavez/blueprint/pull/53), and browse-tool PRs
[#7](https://github.com/nino-chavez/browse-tool/pull/7),
[#6](https://github.com/nino-chavez/browse-tool/pull/6), and
[#5](https://github.com/nino-chavez/browse-tool/pull/5). These receipts are
separate from the worker-turn completion evidence.

### Repeated worktree mismatch and paired recovery

The desktop host assigned a checkout under `.claude/worktrees/`, while the
sessions created additional `.worktrees/fix/...` checkouts. The host's Edit
guard rejected the extra locations. Each denied request has a later successful
request with the same relative file, `old_string`, `new_string`, and
`replace_all`; the checkout path changed. This comparison uses actual call IDs
and tool results, without treating an agent's success summary as proof.

| Case | Rejected edit call → result | Same edit succeeded, call → result |
|---|---|---|
| C2 | 213 → 214; 215 → 216; 217 → 218; 219 → 220 | 235 → 238; 236 → 239; 237 → 241; 240 → 242 |
| C3 | 315 → 316; 317 → 318; 319 → 320; 321 → 322; 323 → 324; 325 → 331 | 359 → 360; 361 → 363; 362 → 367; 366 → 368; 369 → 370; 371 → 378 |
| C4 | 173 → 174 | 198 → 200 |

The current owner still prescribed creating a worktree as a fresh session's
first action: dotfiles `ways-of-working/worktrees.md:18` and
`ways-of-working/working-style.md:257–262` at `e822c02`. The selected clarification
replaces that unconditional start with an assigned-worktree check, retains
`.worktrees/<branch>` for newly created checkouts, and leaves the current
host-managed worktree's cleanup to its host. Both the short rule and its full
owner change together. No isolation guard is relaxed.

This is a retrospective before/after recovery check. It supports the path
correction and a narrow instruction clarification. It does not measure whether
the new wording changes future model behavior, time, cost, or failure rates.
C1 had no matching isolation denial. Its nonzero shell result at 549/550 came
from inspection output and a command chain; that alone did not qualify as a
process defect. C3's expected doctor-output differences and a redundant-read
guard denial also did not justify new rules.

### Other recoveries and rejected changes

- **X1:** npm returned HTTP 403 at 53/54. The worker located installed Puppeteer
  source at 60/61 and used it at 65/66. Its final at 313 did not mention that
  registry error. The trace proves a failed request, not its underlying network
  cause or a missing setup instruction.
- **X2:** the worktree lacked the queried dependency directory at 26/27. The
  worker linked existing dependencies at 62/63 and later removed that link.
  The final at 211 disclosed sandbox limits on the full test suite. The current
  browse-tool README already documents installation at lines 98–103,
  commit `1357ada`. A missing local dependency and X1's HTTP rejection do not
  establish the same root cause, so no bootstrap rule was added.
- **X3:** failures at 80/81 and 122/123 were intermediate tests for the feature
  under implementation. The new module/integration followed at 89/91 and 128.
  They were excluded from process-error findings; no candidate was retained.
- **X4:** PR creation omitted the required `repository_full_name` field at
  667/670. The corrected call at 672/677 opened PR #5. The final at 816 omitted
  the schema retry. One recovered API-shape error did not establish recurrence.

The recoveries omitted from two final summaries show why full tool traces can
add diagnostic information. Omission of incidental errors does not establish
intentional concealment, and these observations do not validate model-generated
quality scores or an unattended self-improvement loop.

### Collection and review limits

The date search reached September 25 for Claude and October 25 for Codex.
Automatic reviews, native subagents, prose-only work and unrelated projects
remained excluded. A nominally machine-authored opening is only a screening cue:
the existing nomination filter also matches continued conversations, which can
contain genuine implementation work.

The initial Codex eligibility search was incomplete because
`poe-extract.py:_iter_session_files` enumerates plain `.jsonl` files. The parent
found 1,974 compressed files under `sessions/` and 430 under `archived_sessions/`
in the local snapshot, then used the existing
`session-archive.py:transcript_lines` decoder. This is a sampling-route correction;
it does not prove Poe missed prior ingestion. No parser or scheduled job was
added. Two bounded helpers timed out without final reports. The parent recovered
candidate locations from their retained tool results and verified the accepted
source records and Claude finding directly. The completed Codex diagnostic
report was also checked against the cited tool results and current owner.

## Bounded run-diagnostic pilot — 2026-11-08

**No configuration change is justified by this pilot. The cross-harness result
is inconclusive because the proposed Claude sample consisted of automatic
commit reviews.** The existing `recall-scan.py:is_machine_authored` filter
already rejects that opening-prompt pattern. This was a sampling error in the
investigation, not a missing filter to implement.

The question came from Ben Holmes's
[agent self-improvement talk](https://www.youtube.com/watch?v=pz74r-4dGfQ): can
tool-call evidence from completed runs reveal a recurring, preventable problem
that merits a small skill or configuration change? The initial investigation
had a 30-minute ceiling, eight discovery runs, and at most two reserved cases.
A patch required the same cause in independent runs, a missing or wrong
instruction or implementation still present at its owner, and a meaningful
before/after check. No score, prose grading, new miner, or scheduled job was
introduced. The July calibration decision below remains closed.

Scope was retained local runs in Nino-owned `tools/`, `methods/`, and
`platform/operator`, initially November 1–7, widened only to October 20.
Employer/client work, brand-generation work, the active investigation, native
subagents, and background housekeeping were excluded. Discovery used existing
Poe source-identity helpers and inspected tool events; those helpers do not
replace the nomination filter or supply a full tool-result parser.

### Sample disposition

These are source identifiers, not transcript excerpts. Dates and diagnostic
line numbers refer to retained local transcripts. Completion events establish
that a worker turn ended, not that its output was correct.

| Role and disposition | Session ID | Project and date |
|---|---|---|
| Claude automatic review — excluded | `f291055c-d95b-4cce-b349-c8f6beecab10` | Operator, November 2 |
| Claude automatic review — excluded | `4f5f36fa-4ab8-409c-b3d1-ed792c046041` | Blueprint, November 7 |
| Claude automatic review — excluded | `f9dc00c5-96d0-4c65-a186-71f398f19eed` | Agentic Field Kit, November 7 |
| Claude automatic review — excluded | `8a1ae9aa-bbac-40cd-bc5d-0512ef377ae8` | browse-tool, October 31 |
| Codex CLI worker — retained | `01a19d86-d12e-7120-b1f0-8e86929b036d` | Operator publisher, November 2 |
| Codex CLI worker — retained | `01a19d86-d14d-7ac1-9453-918e97ba4ec0` | Operator reader, November 2 |
| Codex CLI worker — retained | `01a19d00-700a-7bc2-97e7-90c12e8ddcbd` | music-librarian, November 2 |
| Codex CLI worker — retained | `01a1a37a-e7d3-7b22-beea-c140cbb28ac7` | browse-tool, November 3 |

The two Operator workers contributed to the same change, merged as Operator
PR #33 (`91bb62c`); they are not independent task replicates. The parent checked
the existing machine-authored filter and actual event dates after discovering
the Claude selection error. No replacement Claude implementation run qualified
in the scoped retained files. File modification dates alone were misleading:
some recently modified files contained older events. Neither reserved case was
used for a before/after test because no patch qualified.

### Findings checked against current owners

- **Operator reader:** a missing plugin SDK module at transcript line 62,
  `2026-11-02T18:32:08Z`, preceded completion at line 240. The initial report
  suggested a possible bootstrap-documentation gap, but the parent found the
  install/test/build sequence already in `platform/operator/docs/work-dashboard.md`
  under “Install and open” (lines 95–98 at `91bb62c`). The missing-module event
  does not establish a missing instruction or a recurring root cause.
- **browse-tool:** the November 3 worker attempted the nonexistent `browse-tool`
  command (line 35) and could not load `playwright` (lines 41 and 46), then ended
  at line 59. At `1357ada`, `package.json` declares the `browse-*` commands and
  `puppeteer-core`, not Playwright; the README documents installation, PATH,
  and the individual commands. These events do not justify another setup rule.
- **Operator publisher:** `python` was unavailable at line 78,
  `2026-11-02T18:34:18Z`; the worker ended at line 337. Operator's development
  instructions already use `python3` (`README.md:163` at `91bb62c`). No wrong
  owning instruction was established.

The parent verified those event diagnostics and source locations independently
of the discovery report. No recurring cause cleared the patch threshold in the
retained Codex slice. That limited finding is not a clean bill of health for
either harness, a performance comparison, or evidence that the proposed
self-improvement loop cannot help. No behavioral patch or improvement claim was
made. A future experiment needs an eligible sample before diagnostic review;
this pilot does not authorize more sampling or automatic calibration.

## Resume run executed 2026-07-02

`extract --days 90` + `correlate` on n=838 sessions (pass rate 0.498, followup-fix rate 0.142). Followup-fix predictors, sorted:

| Metric | r (Jul, n=838) | r (Mar, n=349) |
|--------|----------------|----------------|
| prompt_count | +0.227 | +0.18 |
| anti_pattern_count | +0.213 | +0.15 |
| edit_count | +0.202 | +0.18 |
| focused_thrash | +0.198 | +0.14 |
| tool_misuses | +0.192 | +0.12 |
| thrash_ratio | +0.135 | +0.03 |
| compliance_score | -0.096 | -0.08 |

Every live signal strengthened with 2.4x the data, but none cleared the r > 0.25 gate for building `/recall calibrate` — max r² ≈ 0.05. Verdict per the gate: do not build calibrate; the "accept the ceiling" branch (item 6) applies unless external signals (git diffs, CI results) get wired in. The quality-measurement half costs nothing passively (extraction is manual), so it stays as-is rather than being archived. Separate observation from the same audit: the recipe library has had no saves since 2026-03-19 — the save/reuse habit never formed; the live value is Poe + followup-fix detection.

---

## What we built (2026-03-24)

### Recall library (working, stable)
- `/recall save|find|list|show|use|stats` — session recipe library with SQLite + FTS5
- `/recall-scan` — batch scan sessions for recall-worthy patterns
- Auto-population of analysis metrics on save

### Quality analysis (working, honest about limitations)
- `/recall analyze` — single-session compliance grade + process metrics
- `/recall quality` — trends across recent sessions
- `/recall verify` — manual outcome labeling (satisfaction, pass/fail, followup)
- `/recall backfill` — retroactive analysis on older entries
- Two-layer architecture: compliance (graded, from baseline.json) vs process (descriptive, from thresholds.json)

### Automated outcome pipeline (working, most valuable piece)
- `/recall extract --days N` — batch feature extraction from all sessions
- Followup-fix auto-detection via cross-session file overlap (4-hour window)
- Commit auto-detection from Bash tool calls
- `/recall correlate` — Pearson correlation with p-values, zero dependencies
- DB: `session_features` table with 349 rows, 24 columns, fully automated

---

## What we learned (the important part)

### Correlation results (n=349, 30 days, heuristic v4)

**Followup fix prediction (sorted by effect size):**

| Metric | r | p | Significant? | New in v4? |
|--------|---|---|-------------|------------|
| edit_count | +0.18 | 0.0006 | Yes | |
| prompt_count | +0.18 | 0.0008 | Yes | |
| anti_pattern_count | +0.15 | 0.006 | Yes | |
| focused_thrash | +0.14 | 0.007 | Yes | New |
| tool_misuses | +0.12 | 0.027 | Yes | |
| compliance_score | -0.08 | 0.14 | No | |
| tokens_per_output | +0.07 | 0.17 | No | |
| process_score | -0.05 | 0.38 | No | |
| model_switches | -0.04 | 0.45 | No | New, dead |
| thrash_ratio | +0.03 | 0.60 | No | |
| duration_min | +0.01 | 0.85 | No | New, dead |
| late_error_rate | +0.00 | 1.00 | No | New, dead |

**Max r² = 0.03.** Process metrics explain ~3% of followup-fix variance. The rest is task complexity, domain knowledge, code quality (which we can't observe from session structure), and noise.

### What was wrong

1. **Compliance scoring penalized productivity.** Sessions with commits scored 79.2 vs 85.9 for no-commit sessions. More tool calls = more chances to trigger misuse flags. Fixed by normalizing to misuse *rate* (per 100 Bash calls), which reduced the gap from 19 points to 7.

2. **Prompt clarity punished deliberate architecture discussions.** The original metric penalized sessions where coding started late. Fixed by classifying session *shape* (research_then_build, direct_execution, etc.) instead of scoring prompt-to-output latency.

3. **Composite scores (compliance_score, process_score) don't predict anything.** They measure session complexity, not session quality. The individual raw signals (edit_count, focused_thrash, anti_pattern_count) are weak but real predictors; the composites wash them out.

4. **Thrash ratio is noise.** r=+0.03, p=0.60. Edits per unique file doesn't predict followup fixes. But `focused_thrash` (max edits to a single file) does (r=+0.14, p=0.007). The signal is in *concentrated* rework, not distributed rework.

5. **Three of four new exploratory features were dead signals.** Late error rate (JSONL error structure unreliable), duration (wall-clock time irrelevant), model switches (doesn't predict anything). Only focused_thrash carried signal.

6. **The original plan assumed manual labeling.** We designed `/recall verify` for manual outcome labels, estimated 3 weeks to 50 entries. Instead, automated commit detection + followup-fix cross-referencing labeled 349 sessions in seconds with zero manual input.

### What's actually valuable

In order of utility:

1. **Followup-fix auto-detection.** Cross-session file overlap analysis. Zero manual input. The most novel and useful thing in the repo.
2. **The correlation framework.** Proves what works and what doesn't. Prevents scoring theater.
3. **The recall library itself.** Save/search/reuse session patterns. The original feature, still the daily driver.
4. **Compliance checker.** Binary rule checks from baseline.json. Legitimate but doesn't predict outcomes.
5. **Process metrics.** Descriptive telemetry. Useful for outlier detection, not quality judgment.

---

## Backlog — situation gating for injected priors (added 2026-07-20)

The prompt-hook's `relevant_signals` are keyword-matched with no situational
fit check. Evidence from the 2026-07-20 A/B config probe: a "we always push
for north star features" prior was injected into a restraint-shaped question
(should a shared contract be thrown out?) and tilted the response toward a
fleet-wide rewrite; bare-model runs without the injection pushed back 2/2.
Interim mitigation shipped: a caution line appended after the signals block
in `cmd_prompt_hook`. Real fix: classify the prompt's decision shape
(expand vs restrain vs compare) and filter priors whose label rewards the
opposite direction — reuse `classify_situation`'s machinery; the label
taxonomy (`preference/we-convention`, `rejection/instead`, ...) already
carries most of the needed signal.

## What to do when resuming (~June 2026)

### Immediate (day 1)

1. **Re-extract with 3 months of data:**
   ```
   /recall extract --days 90
   /recall correlate
   ```
   With ~1000+ sessions, weak signals may strengthen or disappear. This is the first thing to do.

2. **Update baseline.json** if Claude Code has updated its system prompt (check version with `claude --version`, compare to `_claude_code_version` in baseline.json).

### If correlations strengthen (any r > 0.25 for followup fix)

3. **Build `/recall calibrate`** — derive thresholds from percentiles of known-good sessions (sessions with commits and no followup fix). Write to thresholds.json with `_calibrated_from` metadata.

4. **Try interaction terms:**
   - `edit_count * focused_thrash` — big session hammering one file
   - `anti_pattern_count / prompt_count` — anti-pattern density (normalized)
   - `focused_thrash / unique_files` — concentration of rework

5. **Try the logistic regression** from IMPLEMENTATION.md Level 5. With 1000+ sessions, even weak individual predictors might combine usefully.

### If correlations stay flat (all r < 0.20)

6. **Accept the ceiling.** Process metrics from session JSONL are structurally limited — they can't see code correctness, test results, or domain appropriateness. Document this honestly and focus the tool on what it does well: the recall library and followup-fix detection.

7. **Consider external signals** that might break through:
   - **Git diff size** — run `git diff --stat` after sessions with commits. Large diffs that get reverted = quality signal.
   - **CI/CD results** — if a session's commits trigger CI, did CI pass? (Requires project-specific integration.)
   - **File age** — editing old stable files vs new files. Regressions in stable code are worse.

   These require reading git state, not just session JSONL. They're a fundamentally different data source.

### What NOT to revisit

- **late_error_rate** — JSONL error structure is unreliable. Don't retry without a better error detection method.
- **duration_min** — wall-clock time is noise. Don't try to make it work.
- **model_switches** — no signal. Not worth exploring further.
- **AI-graded quality** — using Claude to judge Claude is circular. Stick to observable outcomes.
- **Prompt content analysis** — privacy risk, NLP complexity, low signal-to-noise. The structural metrics are already at the ceiling.

---

## Data state at pause

| Table | Rows | Key columns |
|-------|------|-------------|
| recipes | 12 | 6 with analysis metrics, 1 manually verified |
| recipes_fts | 12 | Full-text search index |
| session_features | 349 | 24 feature columns, auto-labeled outcomes |

- `recall.db` at `~/.claude/recall.db`
- `baseline.json` pinned to Claude Code 2.1.81
- `thresholds.json` — arbitrary (not calibrated from data)
- `HEURISTIC_VERSION = 4`

---

## Maturity model

| Level | Name | Status | What we can say |
|-------|------|--------|-----------------|
| 0 | Vibes | Done | "That felt productive" |
| 1 | Compliance | Done | "Claude followed rules at 79% rate" |
| 2 | Descriptive | Done | "Research-then-build session, 2.7 thrash ratio" |
| 3 | Correlated | **Done** | "Compliance doesn't predict fixes. Focused thrash does, weakly (r=0.14)." |
| 4 | Calibrated | Blocked | Needs stronger signal or more data |
| 5 | Predictive | Blocked | r²=0.03 is too low for useful prediction |
| 6 | Benchmarked | Future | Needs multiple users |

**The honest conclusion:** Session-level process metrics from JSONL are inherently weak predictors of code quality. The maximum variance explained is ~3%. This may improve with more data (1000+ sessions) or external signals (git diffs, CI results), but it may also be a hard ceiling. The tool's real value is the recall library and the followup-fix detection infrastructure, not the quality scores.
