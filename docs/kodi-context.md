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

The supplied [approval comment](https://github.com/OmiroAI/kodi/pull/3#pullrequestreview-5642775987)
contains no technical finding or reusable lesson by itself. The following guidance
from [PR #3](https://github.com/OmiroAI/kodi/pull/3) is verified against the current
`Coordinator.task_repository`, `Coordinator.sync` and `tests/test_sync.py`:

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

Current source keeps the QA-summary and redundant-test-run proposal in
`docs/qa-review-evidence-backlog.md`, explicitly marked Backlog. `Coordinator.review`
supplies requirements, author output and committed review material; it does not
supply the proposed validated QA summary. Do not describe that proposal as an
existing reviewer guarantee or infer measured savings: the backlog records no
comparison run. `REVIEW_CONTRACT` still requires independent source review and
states that passing QA alone is insufficient. Exact-commit QA and different-model
review remain acceptance requirements; stale evidence cannot approve a new commit.

Source: [proposal and evaluation limits](https://github.com/OmiroAI/kodi/blob/beed7833049fccc3c0877b8b24fe51b45d9249e9/docs/qa-review-evidence-backlog.md),
[review orchestration and contract](https://github.com/OmiroAI/kodi/blob/beed7833049fccc3c0877b8b24fe51b45d9249e9/kodi/coordinator.py),
[exact-commit acceptance regressions](https://github.com/OmiroAI/kodi/blob/beed7833049fccc3c0877b8b24fe51b45d9249e9/tests/test_boundaries.py).

## PR #23: bounded stage capacity remains a proposal

[PR #23](https://github.com/OmiroAI/kodi/pull/23) did not implement parallel
execution. `docs/proposals/bounded-stage-capacity.md` remains planning only:
stage/provider slots, durable leases and fencing are proposals. `Coordinator.tick`
still holds `delivery.lock` through one job; startup blocks interrupted execution.
Implementation and capacity activation need later approval. The linked approval
comment supplies no technical lesson or authorization for later commits.

Source: [proposal](https://github.com/OmiroAI/kodi/blob/9c9f73fc4fa5c06f3a8f77c1c8515f25b8932db9/docs/proposals/bounded-stage-capacity.md),
[approval](https://github.com/OmiroAI/kodi/pull/23#pullrequestreview-5660660267).

[PR #27's approval](https://github.com/OmiroAI/kodi/pull/27#pullrequestreview-5661057303)
adds no technical lesson to PR #19's Backlog guidance or approval for later work.

## PR #26 and #30: learning proposal versus implemented behavior

Verified against current source: `docs/learning-backlog.md` remains a Backlog
draft. `kodi.workspace.feedback` queues eligible merged PR feedback by record
digest and asks for a document explanation when no durable lesson exists.
`tests/test_workspace.py` verifies identical-record retry deduplication, not
stable finding identities across changed feedback, approval-only no-ops or
scheduler fairness. Those remain proposed behavior, not implemented guarantees
or completed acceptance checks.

Source: [backlog](https://github.com/OmiroAI/kodi/blob/9abcfa9f5f8fd05c38048e9912a03423f2450e82/docs/learning-backlog.md),
[collector](https://github.com/OmiroAI/kodi/blob/9abcfa9f5f8fd05c38048e9912a03423f2450e82/kodi/workspace.py),
[regressions](https://github.com/OmiroAI/kodi/blob/9abcfa9f5f8fd05c38048e9912a03423f2450e82/tests/test_workspace.py).

## PR #24: reviewer reliability remains planned

The supplied [approval](https://github.com/OmiroAI/kodi/pull/24#pullrequestreview-5660661187)
contains no technical finding, names an earlier commit than the supplied merged
head and grants no approval for later work.

Current `docs/backlog/reviewer-reliability.md` is planning only; admission control,
reset-aware backoff, clarification and finding disposition are not guarantees.
`Coordinator.review` falls back from Antigravity to configured Qwen only on
`QuotaExhausted`, subject to asset restrictions. Bare 429s, generic errors and
rejected verdicts do not trigger fallback. `tests/test_reviewer_fallback.py`
uses mocks, not independent acceptance evidence. `validate_review` checks fields,
types and different model families; it does not reconcile summary prose with
booleans or track retracted findings.

Source: [planning scope and acceptance checklist](https://github.com/OmiroAI/kodi/blob/3c2ac5f3cfce3231c1a88feb7451ef8a1b4ce38f/docs/backlog/reviewer-reliability.md),
[review orchestration](https://github.com/OmiroAI/kodi/blob/3c2ac5f3cfce3231c1a88feb7451ef8a1b4ce38f/kodi/coordinator.py),
[review validation](https://github.com/OmiroAI/kodi/blob/3c2ac5f3cfce3231c1a88feb7451ef8a1b4ce38f/kodi/policy.py),
[quota detection](https://github.com/OmiroAI/kodi/blob/3c2ac5f3cfce3231c1a88feb7451ef8a1b4ce38f/kodi/runner.py),
[fallback regressions](https://github.com/OmiroAI/kodi/blob/3c2ac5f3cfce3231c1a88feb7451ef8a1b4ce38f/tests/test_reviewer_fallback.py).

## PR #38

[PR #38](https://github.com/OmiroAI/kodi/pull/38#pullrequestreview-5661785831)
adds no durable lesson: its supplied approval has no technical finding. Current
source still matches the PR #26/#30 guidance above; the approval grants no
acceptance for this change.

## PR #42: classification remains planned

The supplied [approval](https://github.com/OmiroAI/kodi/pull/42#pullrequestreview-5664913049)
contains no technical finding and names an earlier commit than the supplied
merged head; it adds no durable lesson or approval for later work.

Current source keeps failure classification in an explicitly planning-only
[Backlog](https://github.com/OmiroAI/kodi/blob/cf36fb5299643544488c5a13a762c6fdede8b38d/docs/backlog/delivery-failure-classification.md).
Do not infer implemented categories, recovery generations or service deadlines
from the PR title. Existing `kodi.recovery.retry` catches `TransientError`, reserves
two retries per job/action/candidate and sleeps 2 then 10 seconds, unlike the
proposed 30/120-second backoff. See `kodi/recovery.py` and `tests/test_recovery.py`.

## PR #40

The [approval](https://github.com/OmiroAI/kodi/pull/40#pullrequestreview-5664401902)
adds no technical lesson and names a different commit in its body from the merged
head; it grants no acceptance for later work. Do not infer release from the PR
title: current [merge maintenance](https://github.com/OmiroAI/kodi/blob/main/kodi/coordinator.py)
retains the oldest nonterminal intent while awaiting approval, including blocked
jobs. The retention [regression](https://github.com/OmiroAI/kodi/blob/main/tests/test_merge_maintenance.py)
is `test_failure_blocks_and_serial_slot_is_retained`.
