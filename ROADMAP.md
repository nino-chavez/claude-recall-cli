# Recall CLI — Quality Measurement Roadmap

Where this tool is, where it needs to go, and what industry-grade quality measurement requires.

## Current state (v3)

Two layers, honest about their limitations:

- **Compliance** (graded A-F): binary checks against Claude Code's documented system prompt rules. Has ground truth. Legitimate.
- **Process metrics** (descriptive): session shape, thrash ratio, cost. No ground truth. Useful for spotting outliers, not for judging quality.

**What's missing:** No outcome validation. We measure process but never check if the output was correct or useful.

---

## Phase 1: Outcome correlation (manual)

**Goal:** Build a labeled dataset linking process metrics to actual outcomes.

**What to build:**
- `/recall verify <id>` command — mark a saved entry with outcome data:
  - `outcome_verified` (bool): did the session produce working code?
  - `had_followup_fix` (bool): was there a subsequent fix session for the same work?
  - `commits_produced` (int): how many commits resulted?
  - `user_satisfaction` (1-5): manual rating
- `/recall correlate` command — once you have 50+ labeled entries, compute correlation between process metrics and outcomes. Which process metrics actually predict good outcomes?

**Why this matters:** Without this, we can't know if improving compliance scores improves actual results. This is the calibration step that every quality framework requires.

**Schema columns already added:** `outcome_verified`, `had_followup_fix` (DB migration v2).

**Effort:** Small. Mostly CLI plumbing + basic statistics.

---

## Phase 2: Automated outcome signals

**Goal:** Replace manual verification with observable signals from git and session data.

**Detectable outcomes (no manual input needed):**
- **Commit production** — did the session end with `git commit`? Detectable from Bash tool calls.
- **Follow-up fix detection** — did the same project have another session within 2 hours touching the same files? Detectable from session JSONL cross-referencing.
- **Error-free completion** — did the session end cleanly or with repeated failing commands?
- **Code churn** — were files from this session edited again in the next session? (High churn = low quality.)

**What to build:**
- Post-session outcome extractor that runs in the `SessionEnd` hook
- Cross-session file overlap analysis (detect follow-up fixes automatically)
- Outcome fields auto-populated on recall entries

**Effort:** Medium. Requires cross-session analysis and hook integration.

---

## Phase 3: Calibrated thresholds

**Goal:** Replace arbitrary thresholds with empirically derived ones from your labeled data.

**Prerequisite:** 100+ sessions with outcome labels from Phases 1-2.

**What to build:**
- Statistical analysis: for sessions with `outcome_verified=true` vs `false`, what are the actual distributions of thrash ratio, tokens per output, session shape?
- Derive thresholds from percentiles of known-good sessions (p25, p50, p75)
- Write calibrated values back to `thresholds.json` with `_calibrated_from` metadata
- Track threshold drift over time (do thresholds change as you improve?)

**Industry analog:** Statistical Process Control (SPC). You observe the process producing known-good output, measure its natural variation, and set control limits at the boundaries of that variation. Deviations outside those limits are signals, not arbitrary scores.

**Effort:** Medium. Requires enough data and basic stats (percentiles, correlation coefficients).

---

## Phase 4: Predictive quality

**Goal:** Before a session ends, predict whether it will need a follow-up fix.

**What to build:**
- Lightweight classifier trained on your labeled session data
- Features: current thrash ratio, compliance score, session shape, cost trajectory, error rate
- Real-time signal: "This session looks like it's heading toward a follow-up fix" (based on pattern similarity to past sessions that required fixes)
- Implemented as a `SessionEnd` hook that flags at-risk sessions

**Industry analog:** Predictive quality in manufacturing — sensors detect process drift before the part fails QA. We detect session drift before the code fails in the next session.

**Prerequisite:** 200+ labeled sessions with follow-up fix data.

**Effort:** Large. Requires ML-lite (logistic regression or decision tree — nothing fancy).

---

## Phase 5: Comparative benchmarks

**Goal:** Enable cross-user comparison and community baselines.

**What to build:**
- Anonymous export format for quality data (no prompt content, only metrics)
- Community baseline: aggregate compliance and process stats from opt-in users
- "How does my compliance compare to the community average?"
- Baseline drift tracking: does the community average improve when Claude Code updates?

**Industry analog:** Industry benchmarking (e.g., DORA metrics reports). Individual orgs compare their deployment frequency and change failure rate to industry medians.

**Prerequisite:** Multiple users running recall-cli with outcome labels.

**Effort:** Large. Requires data collection infrastructure and privacy design.

---

## What NOT to build

- **Prompt content analysis.** Analyzing what users type introduces privacy concerns, requires NLP infrastructure, and the signal-to-noise ratio is terrible. Structural metrics (tool calls, timing, file changes) are more reliable and cheaper.
- **Real-time intervention.** Pausing Claude mid-session to say "you're thrashing" would be annoying and disruptive. Post-session analysis is the right cadence.
- **AI-graded quality.** Using one LLM to judge another LLM's output is circular. Outcomes (did the code work?) are the ground truth, not another model's opinion.

---

## Maturity model

| Level | Name | What you can say | Status |
|-------|------|------------------|--------|
| 0 | Vibes | "That session felt productive" | Done |
| 1 | Compliance | "Claude followed its documented rules at a 79% rate" | Done |
| 2 | Descriptive | "This session was research-heavy with high token cost" | Done |
| 3 | Correlated | "Compliance scores don't predict followup fixes. Raw volume metrics do, weakly." | Done |
| 4 | Calibrated | "Thresholds derived from empirical data, not gut feel" | Next |
| 5 | Predictive | "This session pattern predicts a follow-up fix" | Blocked on better signal |
| 6 | Benchmarked | "Your compliance is in the 80th percentile of recall-cli users" | Future |

---

## What Level 3 actually found (n=349, 2026-03-24)

First real correlation analysis on 349 sessions from 30 days.

### Followup fix prediction (the real quality signal)

| Metric | r | p | Significant? |
|--------|---|---|-------------|
| edit_count | +0.19 | 0.0003 | Yes |
| prompt_count | +0.19 | 0.0004 | Yes |
| anti_pattern_count | +0.15 | 0.004 | Yes |
| tool_misuses | +0.13 | 0.016 | Yes |
| compliance_score | -0.08 | 0.11 | No |
| process_score | -0.04 | 0.44 | No |
| thrash_ratio | +0.02 | 0.71 | No |

**Key findings:**
1. **Compliance score does not predict quality.** p=0.11, not significant. Sessions that follow rules aren't less likely to need fixes.
2. **Process score is noise.** p=0.44. Session shape, thrash ratio, cost efficiency — none predict outcomes.
3. **Raw volume predicts problems.** More edits, more prompts, more anti-patterns → more followup fixes. This is basically "bigger sessions have more problems" — not actionable.
4. **Productive sessions look worse on compliance.** Sessions with commits score 79.2 on compliance vs 85.9 for no-commit sessions. More activity = more opportunities for tool misuse flags.

### What this means

- The composite scores we designed (compliance, process) measure session complexity, not session quality.
- Followup-fix auto-detection via cross-session file overlap is the most valuable infrastructure we built.
- Level 4 (calibrated thresholds) should be derived from followup-fix data, not compliance scores.
- Level 5 (predictive) needs better signal — current features explain ~4% of variance in followup fixes (r~0.19 → r²~0.04). We need either more features or acceptance that session-level process metrics are weak predictors of code quality.

### Possible next signals to explore

- **File count × thrash interaction** — does thrashing on 1 file predict fixes differently than thrashing across 10 files?
- **Late-session error rate** — errors in the last 20% of tool calls (session went sideways at the end)
- **Model switching** — did the session switch between Opus/Sonnet mid-stream?
- **Session duration** — wall-clock time (from timestamps), not just token count
