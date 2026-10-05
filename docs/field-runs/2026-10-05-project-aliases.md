# Project-alias validation — 2026-10-05

Equivalent project paths now resolve to the same stored project values during
memory searches and candidate listing. This fixes missed matches between a
legacy short name, workspace paths on different machines, and worktree paths.

## Matching is exact after alias resolution

The previous implementation performed a substring SQL `LIKE` comparison. The
new resolver uses exact stored values and refuses ambiguous short names. It
leaves source values and provenance unchanged. Status filtering still happens
before the result limit, and normal search still defaults to Approved.

See the [matching contract](../architecture.md#project-matching) for supported
path forms, ambiguity behavior, and the catalog-size limit. Local transcript
search and the unscoped candidate-count heartbeat are unaffected.

Ingestion-only normalization was rejected because it would leave existing
records inconsistent without a migration. Basename-only matching was rejected
because it could combine unrelated repositories. Read-time resolution keeps
workspace scope while recognizing a unique legacy short name. It cannot infer
ownership when the only stored identities are identical short names.

## Local checks passed

The initial real-D1 regression run against the old implementation failed eight
of nine cases. After the fix, all 34 Worker tests, the TypeScript check, and all
16 focused Python schema/outbox tests passed.

The D1 records are synthetic. Tests cover equivalent paths, worktrees, different
review states, filtering before limits, literal wildcard input, unchanged
stored paths, and an intentionally oversized project catalog. The oversized
case fails explicitly instead of resolving against a partial catalog.

One independent read-only review reached its 240-second limit without a final
verdict. It did not establish review completion. No second broad review ran.

## The deployed fix passes live retrieval checks

[PR #4](https://github.com/nino-chavez/claude-recall-cli/pull/4) merged as
`9c4f31617ad174c277d0859628916d1fbacf79df` on 2026-10-05.
[CI on that merge](https://github.com/nino-chavez/claude-recall-cli/actions/runs/37343824018)
passed. The documented Wrangler deployment published Worker version
`7d98b73a-207b-4d6f-b5c7-ae327b9d0a2c`; `/health` returned that version with
creation time `2026-10-05T16:50:18.36298Z`. No database migrations were pending.

Live checks returned the same records under equivalent scoped paths, supported
worktree paths, and unique short names. A real short name had two stored scopes:
the first check expected a match, but received the intended ambiguity error.
Repeating it with the scoped path returned the intended approved records.
Candidate and Stale controls stayed out of default search. Literal `%` did not
broaden the project filter. No synthetic production records were created.

The operator approved three revised lessons. Each became a new Approved record,
and its original became Superseded with a link to the replacement. Original
record bodies and source provenance were preserved.

Workers Builds configuration remains unverified: the existing account-operations
credential received HTTP 403 from the Builds API. This rollout used the
repository's documented manual deployment path and made no automation changes.

## Fresh-session memory acceptance did not pass

One read-only Meta publishing scenario ran in fresh Claude and Codex sessions
on the local Mac, each with a 180-second limit. Both completed. Tool transcripts
were checked separately from their final answers.

| Client | Retrieval observed | Decision observed |
|---|---|---|
| Codex | No Recall tool call; the session reported Recall unavailable | Read the current publishing skill, chose the draft-and-review route, and kept collaborator, mention, sticker, and notification claims separate |
| Claude | Recall rejected the ambiguous short name; a scoped retry returned no records for the multi-term query | Read the current skill and chose the draft route, but conflated a collaborator invite with a Story mention and did not separate sticker/notification evidence |

Neither session retrieved the newly approved lesson. Correct Codex behavior
therefore does not establish an effect from Recall. Claude's decision was
incomplete even though its final answer reported no matching approved memories.
The alias fix does not establish reliable lesson discovery or use.

The second Mac's SSH connection timed out before any session ran. Cross-machine
client acceptance remains open. Git-handoff and release lessons passed targeted
retrieval checks but were not tested in fresh behavioral scenarios. The bounded
experiment stopped after this pass; reduction in repeated mistakes remains
unmeasured.

Private lesson text, source-session IDs, memory IDs, and human review receipts
remain in private Recall records. They are not part of this public receipt.
