# Kodi repository context

- [PR #15](https://github.com/OmiroAI/kodi/pull/15): export usage in its own timer,
  outside delivery/progress paths. A blocked-receiver regression verifies that
  accounting HTTP cannot delay either path.

Read AGENTS.md and docs/operations.md for capability boundaries and exact-commit
acceptance requirements. Runtime configuration, credentials and production stay
outside source. Run tests with:

```
python3 -m unittest discover -s tests -v
```

## Confirmed lessons

- GitHub check inspection uses paginated REST check runs and commit statuses.
  The installed CLI's GraphQL status rollup requests Actions workflow metadata,
  which exceeds the App's Checks-only grant. Do not add broader permissions to
  fix that query. Regression: paginated check-reader coverage.
  Verification: private-CI reader regression tests and independent acceptance.
- Review the entire candidate diff after repair, including earlier commits.
  Regression: complete-branch review coverage.
  Verification: multi-commit review-context regression coverage.
- Refresh canonical source with a fast-forward-only update; dirty, divergent or
  unexpected origins require operator maintenance. Never reset user changes.
  Regression: canonical-source validation coverage.
  Verification: canonical-source failure-path tests.

## PR #3: dashboard repository routing

- Kandev task reads return selected repository associations in
  `repositories[].repository_id`; `repository_ids` is not the task response
  field. The original [PR #3](https://github.com/OmiroAI/kodi/pull/3) tests
  mocked the wrong field and therefore missed valid selections being blocked.
  Routing and repeat-sync regressions must use the association response shape,
  including an association ID distinct from its repository ID.
- Preserve explicit `Repo: <catalog-key>` routing before native dashboard
  selection. Without that line, require exactly one selected repository that
  resolves uniquely to the configured catalog; missing, unknown or ambiguous
  routes are blocked rather than dispatched to a default repository.
- Keep repeated Ready-task syncs idempotent by checking the board task ID before
  creating a GitHub issue. Native tasks do not persist the created issue URL;
  identical content on distinct task IDs still represents distinct work.

Source: [routing and sync implementation](https://github.com/OmiroAI/kodi/blob/b54d0f46a30a590d173e6f3f5938f5d3c85621cd/kodi/coordinator.py),
[routing and repeat-sync regressions](https://github.com/OmiroAI/kodi/blob/b54d0f46a30a590d173e6f3f5938f5d3c85621cd/tests/test_sync.py).

## Workspace-authored PR intake

[PR #6](https://github.com/OmiroAI/kodi/pull/6) addresses the assumption that
accepted PRs must use a coordinator-generated task branch. Host-side submission
records the existing PR branch; legacy jobs retain their derived branch. Merge
validation must still compare the PR, recorded commit, remote head and clean
coordinator checkout. Regression coverage is in `tests/test_submission.py` and
`tests/test_merge_maintenance.py`, including head drift and rebased external
branches. Reusing a PR number does not permit reusing acceptance after its commit
or requirements change.

## Clarification

[PR #45](https://github.com/OmiroAI/kodi/pull/45): retain requests/replies;
recheck edits before coding (`tests/test_clarification*.py`).

## PR #14: binary and non-UTF-8 review material

- Build review material from committed Git blobs at the base and candidate.
  Non-UTF-8 content can lack NUL bytes and Git's binary marker; inspect blob
  decoding too. Preserve complete UTF-8 patches and changed-file context alongside
  asset records containing before/after blob IDs, modes, byte counts and SHA-256
  hashes, with absent versions represented as null.
- Asset metadata alone is not review coverage. `Coordinator.review` requires a
  tool-enabled reviewer to inspect both revisions and report blocking findings
  when a format cannot be inspected. Asset reviews reject Qwen and text-only
  quota fallback; retry the preserved job with a supported reviewer.
- Fail closed when review material exceeds its budget; do not truncate source
  or invent a successful verdict. Retry coverage verifies preserved checkout and
  evidence, while acceptance still requires independent QA and different-model
  review for the exact candidate commit. Mocked reviewer responses test gating,
  not independent acceptance.

Source: [committed review material](https://github.com/OmiroAI/kodi/blob/afc8aa5d0ebb7e7416cb65badef640e50384bd8b/kodi/review_diff.py),
[review orchestration](https://github.com/OmiroAI/kodi/blob/afc8aa5d0ebb7e7416cb65badef640e50384bd8b/kodi/coordinator.py),
[asset review regressions](https://github.com/OmiroAI/kodi/blob/afc8aa5d0ebb7e7416cb65badef640e50384bd8b/tests/test_review_assets.py).

## PR #19: QA evidence and review test runs

`Coordinator.review` supplies requirements, author output and committed review
material, but no validated QA summary. The QA-summary proposal has no comparison
run establishing savings. `REVIEW_CONTRACT` requires independent source review;
passing QA alone is insufficient and stale evidence cannot approve a new commit.

Source: [proposal and evaluation limits](https://github.com/OmiroAI/kodi/blob/beed7833049fccc3c0877b8b24fe51b45d9249e9/docs/qa-review-evidence-backlog.md),
[review orchestration and contract](https://github.com/OmiroAI/kodi/blob/beed7833049fccc3c0877b8b24fe51b45d9249e9/kodi/coordinator.py),
[exact-commit acceptance regressions](https://github.com/OmiroAI/kodi/blob/beed7833049fccc3c0877b8b24fe51b45d9249e9/tests/test_boundaries.py).

## Scheduling and reviewer reliability

`Coordinator.tick` holds `delivery.lock` through one job; startup blocks
interrupted execution. Capacity and reliability proposals do not establish
parallelism, leases, admission control, reset-aware backoff or finding disposition.

Source: [capacity proposal](https://github.com/OmiroAI/kodi/blob/9c9f73fc4fa5c06f3a8f77c1c8515f25b8932db9/docs/proposals/bounded-stage-capacity.md).

- `Coordinator.review` falls back from Antigravity to configured Qwen only on
  `QuotaExhausted`, subject to configuration and asset restrictions. Existing
  quota detection distinguishes explicit exhaustion text from a bare 429 or rate
  limit; generic errors and rejected verdicts do not trigger this fallback.
  `tests/test_reviewer_fallback.py` covers these distinctions with mocks, not
  independent acceptance evidence.
- `validate_review` checks required fields, boolean types, findings/summary
  container types and different model families. It does not reconcile summary
  prose with booleans. Finding history is now preserved by the separate ledger;
  do not infer source acceptance from schema validation alone.

Source: [snapshot](https://github.com/OmiroAI/kodi/tree/3c2ac5f3cfce3231c1a88feb7451ef8a1b4ce38f):
`docs/backlog/reviewer-reliability.md`, `kodi/coordinator.py`, `kodi/policy.py`,
`kodi/runner.py` and `tests/test_reviewer_fallback.py`.


## PR #40: merge intent retention

Do not infer release from the PR title: current
[merge maintenance](https://github.com/OmiroAI/kodi/blob/main/kodi/coordinator.py)
retains the oldest nonterminal intent while awaiting approval, including blocked
jobs. The retention
[regression](https://github.com/OmiroAI/kodi/blob/main/tests/test_merge_maintenance.py)
is `test_failure_blocks_and_serial_slot_is_retained`.

## PR #22 follow-up

[Source PR #22](https://github.com/OmiroAI/kodi/pull/22): tick still holds
`delivery.lock` across synchronous delivery; separate Telegram polling is not
coordinator concurrency. The lock regression tests exclusion, not progress during
worker execution. The narrower-lock design remains a proposal.

## PR #20: review model routing remains a proposal

The [routing proposal](https://github.com/OmiroAI/kodi/blob/main/docs/backlog/review-model-routing.md)
remains Backlog: private benchmarks and owner-approved models and thresholds are
required before implementation. [Review selection](https://github.com/OmiroAI/kodi/blob/main/kodi/coordinator.py)
uses configured models or provider defaults, not complexity. Mocked tests do not replace exact-commit independent QA,
different-model review, required GitHub checks or human approval.

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
