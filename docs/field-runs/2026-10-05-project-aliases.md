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

## Deployment and behavioral evidence

Publication and deployment were authorized after the local checks. The rollout
receipt will be recorded here after verification. Tests establish local
retrieval behavior, not a reduction in repeated mistakes by agents.

Private lesson text, source-session IDs, memory IDs, and human review receipts
remain in private Recall records. They are not part of this public receipt.
