# Track recovery and require explicit scheduling approval

Status: **Backlog — planning only**. Repository catalog key: `system`.
This document records proposed work; it grants no scheduling, retry, publication
or merge authority. Do not move the task to Ready, start workers or resume
preserved work without separate explicit owner scheduling authorization.

## Reconcile before implementation

After scheduling is explicitly authorized, inspect the related paused proposal
and original job in private operator state. Reconcile requirements and preserved
partial work, including the original issue, PR, candidate SHA, checkout,
checkpoint, evidence, artifacts and attempt budgets. Record the relationship in
that private state; do not copy operational transcripts or identifiers here.
Reuse the original implementation job and task rather than dispatching both
versions. Missing or conflicting identity requires inspection before execution.
This planning change has not inspected or resumed that private work.

## Proposed behavior

- Extend the existing deterministic coordinator, store, recovery, submission,
  board and Telegram components. Use supported Kandev task APIs; keep native
  agent/session startup disabled. Add no agent framework or separate dashboard.
- Support resuming the failed stage on the original job, issue, PR and preserved
  candidate. Persist checkpoint identity, recovery artifacts, evidence references,
  stage attempts and consumed budgets before execution. Restart or retry must
  never reset budgets or silently start implementation again.
- Give every implementation, submission, learning and recovery job exactly one
  visible Kandev record before execution. Reconcile uncertain task creation
  before retrying it; fail closed when the record cannot be confirmed. Recovery
  and repair reuse the original task. Show authorization provenance and current
  recovery status without exposing private logs or credentials.
- Persist and display distinct statuses: **Waiting for provider**, **Needs
  repair**, **Paused by owner**, and **Blocked requiring inspection**. Show the
  blocker, next permitted action and eligible retry time when known; unknown
  eligibility must not imply permission to run.
- Record separate decisions for proposal approval, implementation scheduling,
  stage retry and protected merge approval. Editing an issue or approving a
  proposal must not dispatch work. Deferred implementation and Backlog/paused
  work require explicit scheduling authorization. A stage retry authorizes only
  the eligible stage and cannot grant implementation or merge authority.
- Offer owner-only Telegram actions appropriate to the current blocker: Retry
  review, Retry QA, Use configured fallback, Repair and recheck, Details and Keep
  paused. Fallback must already be configured and satisfy review restrictions.
  Details and Keep paused never dispatch work.
- Bind callbacks to job, candidate SHA, expected state and generation. Persist
  action idempotency and atomically validate/claim the transition before work.
  Reject unauthorized, stale and duplicate actions, including concurrent taps.
  Acknowledge promptly, then reconcile and report outcomes through deduplicated,
  sanitized notifications. Recover an interrupted claim without repeating an
  uncertain side effect or consuming an attempt twice.
- Reconcile stopped workers and uncertain external effects before releasing
  capacity. Preserve evidence and quarantine uncertain execution for inspection.
  Provider admission and worker concurrency policy are outside this ticket.

## Acceptance and manual verification plan

Use disposable state, fictional repositories and mocked provider/Telegram APIs.
These checks are planned requirements, not completed acceptance evidence.

1. Fail review and QA separately, restart the coordinator, then retry the failed
   stage. Verify original job/issue/PR/task/checkpoint identity, candidate,
   artifacts, evidence and consumed budgets survive; successful stages are not
   needlessly repeated and exhausted budgets block execution.
2. Repeat and concurrently submit owner callbacks, interrupt after durable claim
   and after an uncertain side effect, then restart. Verify at most one execution
   and one budget charge, durable deduplication and a reconciled outcome.
3. Change the candidate, state or generation before a tap; also try an unrelated
   Telegram user. Verify rejection without work, budget changes or gate bypass.
4. Exercise each recovery status and valid action. Verify next action/retry time,
   prompt acknowledgement, sanitized outcomes and deduplicated notifications.
5. Exercise implementation, submission, learning and recovery end to end. Fail
   task creation and lose its response before restart. Verify no worker starts
   before a confirmed visible record, exactly one record exists per job, repairs
   reuse that task, and provenance/status remain current in Kandev.
6. Approve a proposal, authorize an issue edit, keep work paused and restart.
   Verify none schedules implementation. Separately authorize scheduling and
   verify only the reconciled original implementation is eligible. Retry approval
   alone must not resume deferred implementation.
7. Stop a worker with uncertain effects. Verify capacity remains held until safe
   reconciliation and no duplicate replacement starts during that interval.
8. Repair or replace a candidate. Verify exact-SHA independent offline QA,
   complete-candidate different-model review, required CI and human CODEOWNER
   approval remain mandatory. Old acceptance cannot authorize a changed SHA;
   stage actions cannot request protected merge without its separate approval.

Before a future implementation is ready, run
`python3 -m unittest discover -s tests -v` and the public-source checker, review
the entire diff for disclosures, and obtain exact-commit independent offline QA,
different-model review, required GitHub checks and human CODEOWNER approval.
Local mocked tests cannot substitute for those delivery gates. Coding/review
workers retain their existing prohibition on publishing, merging, deploying,
production access and permission changes.
