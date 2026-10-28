# Kodi repository context

Read AGENTS.md and docs/operations.md for capability boundaries and exact-commit
acceptance. Runtime configuration, credentials and production stay outside source.
Local tests: `python3 -m unittest discover -s tests -v`.

## GitHub checks and branch identity

- Read paginated REST check runs and commit statuses. GraphQL status rollups can
  request Actions workflow metadata beyond the App's Checks-only grant; do not
  broaden permissions to work around that. Verification: paginated check-reader
  regressions in `tests/test_github_gate.py`.
- Preserve a workspace-submitted PR's recorded branch; only legacy jobs derive
  `kodi/task-<id>`. Compare the PR head, remote branch and clean checkout against
  the accepted candidate. Reusing a PR does not preserve acceptance after its
  commit or requirements change. [PR #6](https://github.com/OmiroAI/kodi/pull/6);
  `tests/test_submission.py` and `tests/test_merge_maintenance.py` cover head
  drift and rebased external branches.
- Refresh a source clone only by fast-forward. Dirty, divergent or unexpected
  origins require inspection; never reset user changes. Verification:
  `tests/test_source.py`.

## Dashboard repository routing

[PR #3](https://github.com/OmiroAI/kodi/pull/3) corrected mocks that hid blocked
native-task routing. Kandev reads return associations in
`repositories[].repository_id`, not `repository_ids`; fixtures must include an
association ID distinct from the repository ID.

Preserve explicit `Repo: <catalog-key>` routing. Otherwise require exactly one
selected repository resolving uniquely to an enabled catalog entry. Deduplicate
Ready-task syncs by board task ID before creating an issue: native tasks do not
persist the created issue URL, and identical text on different task IDs is
separate work. Verification: `tests/test_sync.py`.

Source: [routing and sync implementation](https://github.com/OmiroAI/kodi/blob/b54d0f46a30a590d173e6f3f5938f5d3c85621cd/kodi/coordinator.py),
[routing and repeat-sync regressions](https://github.com/OmiroAI/kodi/blob/b54d0f46a30a590d173e6f3f5938f5d3c85621cd/tests/test_sync.py).

## Complete candidate and asset review

Review the whole candidate against its merge base, including earlier repair
commits. Build material from committed blobs at both revisions; working-tree
files and the latest commit alone can miss changes.

Non-UTF-8 blobs can lack NUL bytes and Git's binary marker. Inspect decoding,
preserve complete UTF-8 diffs/context, and supply asset before/after blob IDs,
modes, byte counts and hashes; represent absent revisions as null. Metadata is
not asset inspection: require a tool-enabled reviewer to inspect both revisions
and block unsupported formats. Do not fall back to a text-only reviewer for
assets or truncate over-budget material into a successful verdict.
[PR #14](https://github.com/OmiroAI/kodi/pull/14); verification:
`tests/test_review_assets.py`, `tests/test_review_scope.py` and
`tests/test_review_budget.py`.

Source: [committed review material](https://github.com/OmiroAI/kodi/blob/afc8aa5d0ebb7e7416cb65badef640e50384bd8b/kodi/review_diff.py),
[review orchestration](https://github.com/OmiroAI/kodi/blob/afc8aa5d0ebb7e7416cb65badef640e50384bd8b/kodi/coordinator.py),
[asset review regressions](https://github.com/OmiroAI/kodi/blob/afc8aa5d0ebb7e7416cb65badef640e50384bd8b/tests/test_review_assets.py).

## Usage export isolation

Accounting HTTP belongs in its own export timer, outside delivery/progress
paths. A blocked receiver must not delay either path.
[PR #15](https://github.com/OmiroAI/kodi/pull/15); verification:
`tests/test_activity_delivery_isolation.py`.

## Clarification

[PR #45](https://github.com/OmiroAI/kodi/pull/45): retain requests/replies;
recheck edits before coding (`tests/test_clarification*.py`).

## PR #40: merge intent retention

Do not infer release from the PR title: current
[merge maintenance](https://github.com/OmiroAI/kodi/blob/main/kodi/coordinator.py)
retains the oldest nonterminal intent while awaiting approval, including blocked
jobs. The retention
[regression](https://github.com/OmiroAI/kodi/blob/main/tests/test_merge_maintenance.py)
is `test_failure_blocks_and_serial_slot_is_retained`.

## Finding dispositions

The [PR #21 proposal](https://github.com/OmiroAI/kodi/pull/21) now has a ledger
in `Store`. A disposition's `finding_id` links to the original review record
and SHA; its `candidate_sha` identifies the candidate being evaluated. Keep
disposition fields in the strict reviewer schema. Tests in
`tests/test_finding_resolution.py` verify schema compatibility and lineage.
Unresolved earlier findings remain blocking through `Store.gate`; local
regressions do not replace exact-commit independent QA, different-model review,
required GitHub checks or human approval.

## Implementation dispatch versus historical planning text

The supplied incident evidence for [PR #18](https://github.com/OmiroAI/kodi/pull/18)
and [PR #19](https://github.com/OmiroAI/kodi/pull/19) records proposal-only changes
accepted under stale Backlog instructions. A completed proposal is not implemented
runtime behavior. Ready dispatch now freezes the requested delivery scope before
workers run; historical planning disclaimers cannot downgrade that scope.
Implementation requires an implemented outcome, concrete behavioral/regression
evidence and independent source confirmation of delivery. Baseline QA alone does
not establish delivery. Explicit design/documentation/configuration/benchmark
scopes retain their own requirements; file extensions do not determine scope.

Regression: `tests/test_delivery_contract.py` reproduces the stale planning
sentence, proposal rejection, valid implementation evidence, explicit design
scope, restart/duplicate dispatch, forged scope and no-op learning outcomes.
These local/mocked verdicts verify gating only. Independent offline QA and a
different-model source review of the exact commit, required CI and human approval
remain separate acceptance requirements.

Manual verification: in a disposable coordinator queue, dispatch a Ready task
with `Delivery type: IMPLEMENTATION`, a behavioral requirement and a historical
“planning only” sentence. Return a proposal/deferred report and baseline test
results; confirm it blocks before QA/review with preserved dispatch/report/reason
evidence. Repeat with behavior and focused failure-path regressions; confirm
source review must independently return `delivery_met: true`. Restart and repeat
the same dispatch; confirm the original scope and requirements remain unchanged.
Dispatch an explicit `Delivery type: DESIGN` task and confirm its proposal is
judged against that scope. Do not use these checks to resume any paused job.

Delivery verdicts must fit the strict reviewer JSON schema; test the schema,
validator and acceptance gate together. Raw Qwen HTTP requests put
`chat_template_kwargs` at the top level; fallback transport tests verify this.

Clarification intake and delivery scope share the atomic enqueue: freeze scope
before the read-only intake worker, retain it across answers, and keep host-only
evidence paths. The cross-feature regression is in `tests/test_clarification.py`.

[PR #61](https://github.com/OmiroAI/kodi/pull/61) must preserve both frozen
delivery scope and the validated exact-candidate QA summary when composing review
context. The combined regression is in `tests/test_qa_review_context.py`.

[PR #71](https://github.com/OmiroAI/kodi/pull/71): workspace intake must
ignore historical `merge_maintenance` evidence only after a later `merge_released`
event, matching coordinator active ownership. Preserve the original markers and
all prior job evidence. Active reservations, uncertain remote mutations and
in-flight updates still block submission and remain serialized. Verified by the
released-intent submission regressions.
