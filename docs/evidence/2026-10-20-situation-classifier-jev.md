# Situation classifier against Jev: not measured, and no experiment is worth running

2026-10-20. Question: does TypeSafe's Jev classify better than the regex in
`classify_situation` (`poe-extract.py`)? It was asked because idea-fit scored the
`recall.situation` job 0.40, above its 0.2 filter, and idea-fit's README says a score
filters but does not rank.

**Answer: no Jev call was made, and switching is not worth a follow-up.**

- The only labelled cases are the regex's own tuning set. It scores 35 of 35 on them by
  construction, so a challenger can only tie or lose there.
- The one label with real volume on drafts, `hesitation`, is already measured on real turn
  endings. That job is `hooks.hesitation`, and its results are in the private operator repo.
- Four of the other six labels are counting, length or destructive-command checks. The
  earlier Jev evaluation ruled Jev out for exactly that kind of check. The remaining two
  are short word lists that almost never fire.
- The real defect is where the classifier runs, not how accurate it is. The prompt hook
  runs this draft classifier on user prompts, where it fired on 35% of 9,655 prompts.

Jev calls: 0. Tokens: 0. Cost: $0.

## The labelled set

`test_classify_situation.py` holds every labelled case for this function: 26 positive drafts
and 9 negative drafts, 35 in all.

| Label | Positive cases | Regex result |
|---|---|---|
| `hesitation` | 13 | 13/13 |
| `destructive-action` | 4 | 4/4 |
| `hedge-dense` | 3 | 3/3 |
| `cheerleading` | 3 | 3/3 |
| `trailing-summary` | 3 | 3/3 |
| `vague-rationale` | 1 | 1/1 |
| `over-engineered` | 1 | 1/1 |
| Negative (should fire nothing) | 9 | 9/9 |

Counts include the two combination cases, so the positive column sums to more than 26.

The "10-prompt adversarial set" in the `poe-extract.py` docstrings is not part of this set.
That docstring belongs to `classify_prompt_intent`, a different function that reads user
prompts. Its 10 cases are `PROMPT_INTENT_POSITIVE` in the same test file, and that
classifier also passes all of them.

### Why this set cannot rank two classifiers

The regex patterns were written and tuned against these exact sentences. The test file is
a regression guard. It was never a held-out sample. The 2026-09-18 Jev evaluation hit the
same problem with the stop hook's golden file and set it aside for this reason (operator
`docs/evidence/2026-09-18-jev/README.md`, method step 2). With six of seven labels at
four or fewer positives, a per-label accuracy figure would rest on one to four sentences.

The brief for this run said to stop at a proposal in this case rather than hand-label new
data, so no new labels were written.

## Where Jev could even apply, label by label

Fire counts come from `~/.claude/poe/hook.log`. That is the prompt hook's one-line-per-prompt
telemetry, 9,655 rows from 2026-05-13 to 2026-10-20. They count fires on prompts, not on
drafts. Some rows are agent-written prompts that the noise filter does not catch.

| Label | What the regex checks | Fires on prompts | Jev candidate? |
|---|---|---|---|
| `hesitation` | permission-seeking closer in the last 400 chars | 69 | Already measured as `hooks.hesitation` |
| `vague-rationale` | text over 200 chars with no causal word | 3,190 | No: a length rule |
| `destructive-action` | `rm -rf`, `force-push`, `--no-verify` and similar | 96 | No: literal command strings |
| `cheerleading` | praise word list | 82 | Possible, low value |
| `hedge-dense` | three or more hedge words | 61 | No: a count |
| `trailing-summary` | closing recap phrase | 2 | Possible, almost no volume |
| `over-engineered` | two or more "what if" | 1 | No: a count |

The "No" rows follow the 2026-09-18 evaluation's "Where Jev should not go" section. It
excludes counting and length checks, which are documented Jev weak spots, and keeps
destructive-command checks in code, because their false positives were fixed by parsing
argument position and Jev sees only a string.

### The hesitation label is already measured

`anti-hesitation.py`, the Stop hook in dotfiles, imports `classify_situation` and blocks
only on `hesitation`. The "regex" arm of `hooks.hesitation` is therefore this function's
hesitation label. Its Jev comparison used real held-out turn endings, two judges from
different vendors and a pre-registered protocol. The results and the later threshold
re-score are in operator `docs/evidence/2026-09-18-jev/` and
`docs/evidence/2026-09-21-jev-ecosystem/`. Jev figures stay in that private repository
and are not repeated in this public one.

The shadow log has been collecting since 2026-09-18: 4,801 real turn endings by
2026-10-20, 4,704 of them with a Jev answer. On those 4,704 the regex blocked 47, was
overridden by its own still-ask or loop guard on 4, and stayed silent on 4,653. The
log has no reference labels, so it shows how often the two arms disagree, not which one
is right. Picking the threshold from these rows is already an open item in operator's
evidence README; run `shadow-report.py --closers` there.

## The defect worth fixing is in the prompt hook

The `prompt-hook` command (`cmd_prompt_hook` in `poe-extract.py`) calls
`classify_situation(prompt)` on every user prompt of 20 characters or more. The comment beside the call says draft-style failures are "rare in user
prompts, but cheap to also check". The log says otherwise.

- At least one situation fired on 3,378 of 9,655 prompts (35%).
- `vague-rationale` accounts for 3,190 of those fires. Any prompt over 200 characters
  without "because", "since" or a similar word trips it.
- Each fire injects advice written for an assistant draft ("Add the load-bearing 'why' or
  trim the assertion") into the context of a turn whose text came from the user.

This is a scoping problem, so a code change fixes it. A model would not. The fix is to run
only the prompt-intent classifier on prompts, or to drop `vague-rationale` from the prompt
path. It is out of scope here, because this run was shadow-only and must not change hook
behaviour.

## The idea-fit row describes a different function

`jobs.tsv` in dotfiles words the job as "Classify a user message as hesitation-override,
hedge, cheerleading or correction signal". Two of those four labels do not exist in
`classify_situation`:

- "hesitation-override" and "correction" are reply labels: `redirect-go` and `corrective`
  from `_classify_follow` and `extract_signals`, which label the user reply that follows
  an assistant turn.
- "hedge" and "cheerleading" are `classify_situation` labels, and that function labels
  assistant drafts.

The row's `incumbent` cell names `classify_situation`, so this run measured that. The job
sentence was left as written; whoever owns the registry should decide whether the row is
one job or two.

## If a measured comparison is still wanted

Only `cheerleading` and `trailing-summary` are left without a reason to rule Jev out, and
their combined volume is under 1% of prompts. If someone still wants the number, copy
experiment 4's shape from the 2026-09-18 evaluation rather than inventing one:

1. Freeze `PROTOCOL.md` before any model runs: sample, split, winning rule.
2. Sample real assistant drafts from transcripts in strata: regex fires, a praise word or
   recap phrase appears but the regex is silent, and neither. Seed 42, a 60-case dev split
   and a 150-case test split.
3. Label with two judges from different vendors, blind to both arms. Keep only cases
   where they agree.
4. Score the regex, Jev and an always-silent baseline on the test split, then weight the
   strata back to their real share before quoting a rate.

That run sends real transcript text off the machine, so it needs Nino's explicit OK
first.

## Reproduce

Fixture inventory and regex result:

```bash
/usr/bin/python3 test_classify_situation.py
```

Fire counts per label in the prompt-hook log. This prints counts only, never prompt text:

```bash
/usr/bin/python3 -c '
import json, collections, pathlib
c = collections.Counter(); n = hit = 0
for line in pathlib.Path.home().joinpath(".claude/poe/hook.log").open(errors="replace"):
    try: e = json.loads(line)
    except ValueError: continue
    n += 1; s = e.get("situations", []); hit += bool(s); c.update(s)
print(n, "prompts,", hit, "with a situation:", dict(c.most_common()))'
```
