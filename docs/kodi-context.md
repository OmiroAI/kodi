# Kodi repository context

Kodi is a deterministic delivery coordinator, not an agent framework. Read
AGENTS.md and docs/operations.md for capability boundaries and delivery gates.
The coding and review containers cannot publish, merge or deploy. Host-only
helpers use the scoped GitHub App. Local tests run with:

```
python3 -m unittest discover -s tests -v
```

PR acceptance also requires independent offline QA on the configured QA host, different-model
review, GitHub checks and human CODEOWNER approval at the candidate commit.
Keep runtime configurations, provider credentials and production outside source.

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

The supplied [dismissed approval comment](https://github.com/OmiroAI/kodi/pull/14#pullrequestreview-5651201919)
contains no technical finding and refers to an earlier commit than the supplied
merged head. It does not establish a reusable lesson or approval for later work.
The following guidance from [PR #14](https://github.com/OmiroAI/kodi/pull/14)
is verified against the current source and regression coverage:

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

The supplied [approval comment](https://github.com/OmiroAI/kodi/pull/19#pullrequestreview-5660655179)
contains no technical finding and names an earlier commit than the supplied
merged head. It establishes no reusable technical lesson or approval for later
work. The title of [PR #19](https://github.com/OmiroAI/kodi/pull/19) does not
establish that its proposed behavior is implemented.

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


## PR #22: responsiveness and delivery lock scope

The supplied [approval comment](https://github.com/OmiroAI/kodi/pull/22#pullrequestreview-5660659216)
contains no technical finding and names an earlier commit than the supplied
merged head. It establishes no reusable technical lesson or approval for later
work. The title of [PR #22](https://github.com/OmiroAI/kodi/pull/22) does not
establish that narrower delivery locking is implemented.

- `Coordinator.tick` still holds `delivery.lock` across all of `_tick`, including
  synchronous `process` execution. The lock regression verifies non-overlapping
  ticks and release after completion; it does not prove board sync, CI observation
  or unrelated delivery can progress during a worker run.
- `serve` starts Telegram polling in a separate daemon thread with its own
  `telegram.lock`. Keep that distinction clear: separate command polling does
  not demonstrate concurrent coordinator progress.
- `docs/backlog-delivery-responsiveness.md` is explicitly planning only. Worker
  separation, durable mutation ownership, bounded fairness and timing evidence
  remain proposed requirements, not current guarantees. Do not narrow the lock
  based on the PR title or treat the backlog as authorization to execute work.

Source: [tick implementation](https://github.com/OmiroAI/kodi/blob/9c9f73fc4fa5c06f3a8f77c1c8515f25b8932db9/kodi/coordinator.py),
[lock regression](https://github.com/OmiroAI/kodi/blob/9c9f73fc4fa5c06f3a8f77c1c8515f25b8932db9/tests/test_merge_maintenance.py),
[daemon entry point](https://github.com/OmiroAI/kodi/blob/9c9f73fc4fa5c06f3a8f77c1c8515f25b8932db9/kodi/cli.py),
[Telegram polling](https://github.com/OmiroAI/kodi/blob/9c9f73fc4fa5c06f3a8f77c1c8515f25b8932db9/kodi/telegram_control.py),
[proposal and acceptance plan](https://github.com/OmiroAI/kodi/blob/9c9f73fc4fa5c06f3a8f77c1c8515f25b8932db9/docs/backlog-delivery-responsiveness.md).
