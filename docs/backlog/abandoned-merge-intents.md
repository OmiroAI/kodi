# Detect and safely recover abandoned merge intents

Repo: system
Status: Backlog — planning only; not scheduled or Ready.

This ticket records requirements, not authorization to implement recovery, run
workers or retire a live marker. Retirement requires a separate explicit operator
action for the exact job and marker after inspection of a preview.

## Problem and scope

An older blocked documentation job can retain `merge_maintenance` evidence and
occupy the exclusive merge slot even when GitHub has no armed merge request.
`Coordinator.maintain_merges` selects nonterminal jobs with that evidence before
considering unrelated work and returns when the selected job is blocked.
Historical evidence alone must not establish permanent delivery ownership.

Scope is Kodi merge queue state recovery only. Preserve the blocked job, original
and failed checkouts, candidate identity, QA/review evidence, attempts, recovery
budgets and recovery history. No service, container, image or data-directory
removal; no bulk cleanup, job cancellation, branch deletion or automatic requeue.
CD belongs to each repository; host/resource permission rules remain outside
this ticket. Keep the deterministic coordinator and existing Kandev UI.

Coordinate with [approval-wait reservations](approval-wait-reservations.md),
which covers normal eligibility while awaiting approval. This ticket covers
abandoned intent diagnosis and explicitly authorized retirement, not ordinary
approval-wait release or broader concurrency changes.

## Requirements for a separately authorized implementation

- Reconcile the stored repository, PR and expected head against authoritative
  GitHub PR identity, current head, merge outcome and armed auto-merge/queue state
  under the existing `delivery.lock`. Use the same lock for daemon retries,
  previews and operator recovery. Age or blocked state alone is not proof of
  abandonment. Interrupted branch updates or uncertain publication retain
  ownership until separately reconciled.
- Report specific reasons distinguishing a verified abandoned marker awaiting
  authorization, a live/armed request, an already-merged outcome, an identity/head
  mismatch and unavailable or incomplete GitHub observations. Surface job/marker
  identity and ownership through existing status/Kandev surfaces. Never infer
  absence from authentication failures, timeouts, API errors or missing fields.
- Provide a read-only preview scoped to one job and one immutable marker identity
  or version. Show stored PR/head, observed PR/head and merge state, observation
  time, proposed local ownership change, preserved artifacts and refusal reason
  where applicable. A preview neither retires a marker nor grants authorization.
- Require explicit operator authorization bound to that preview's job, marker
  and expected head. Reacquire the delivery lock and re-read local and GitHub
  state before applying; reject changed identity, replaced markers, stale
  previews and uncertain observations. Never retire a live/armed request, even
  with authorization. Define a conservative observation/race policy: the local
  lock cannot fence external GitHub writers; any uncertainty retains ownership.
- Distinguish retirement of delivery ownership from deletion of evidence. Keep
  the original marker as history and append durable audit evidence linking the
  preview, operator authorization, GitHub observation and result. Include job,
  marker, PR/head, timestamps and prior/resulting ownership without credentials
  or raw private API transcripts. Leave the blocked job blocked and all QA/review
  and checkout artifacts intact. Retirement grants no acceptance or merge rights.
- Reconcile an already-merged PR through the existing terminal-outcome path,
  recording its authoritative identity and outcome rather than calling it an
  abandoned request. Do not overwrite unrelated failure/recovery evidence or
  repeat terminal side effects. Inconsistent merge identity requires inspection.
- Make local retirement and its audit durable together. Concurrent recoveries,
  coordinator retries and restart after interruption must yield one effective
  retirement and a stable recorded result for the same authorized operation.
  Recovery of one marker must never release a replacement marker or another job.
  Unrelated work may resume only after delivery ownership is safely reconciled;
  all normal merge gates remain mandatory.

## Regression and manual verification plan

Use disposable fixtures and fictional repositories such as `ExampleOrg/project`.
Keep operator configuration and operational evidence in private storage.

1. **Abandoned:** seed a blocked job with an old intent, preserved failed
   checkout and QA/review/recovery evidence. Return a matching unmerged PR with
   explicitly absent armed state. Verify the specific blocked reason and preview;
   no mutation occurs before explicit authorization. Authorize that single
   marker, verify audited ownership retirement, unchanged blocked job/artifacts
   and normal eligibility for unrelated approved work. Reject broad selectors.
2. **Armed:** return an armed auto-merge or queued merge, including one armed
   between preview and apply. Refuse retirement and retain ownership/evidence.
   Repeat with PR/head mismatch, incomplete observations and unresolved updates.
3. **Already merged:** return an authoritative matching merged outcome. Verify
   terminal reconciliation and deduplicated delivery on retries, with historical
   marker and failure evidence retained. Reject mismatched merge identity.
4. **Unavailable GitHub:** simulate authentication errors, timeout, malformed
   response and API failure at preview and apply. Verify an explicit unavailable
   reason, no inferred absence, no retirement and retained ownership.
5. **Concurrent recovery:** race two independently opened recovery processes
   with coordinator maintenance/retry under the delivery lock. Verify one
   effective transition with durable audit, no duplicate remote mutation and
   no release of another job or replacement marker. Interrupt before/after local
   persistence and retry; verify idempotence and retained artifacts. Change the
   head/marker after preview and verify the old authorization is rejected.

For authorized implementation, run
`python3 -m unittest discover -s tests -v` and
`python3 scripts/check_public_source.py`, then inspect the full diff for private
disclosures. These scenarios are pending tests, not completed acceptance evidence.
Record exact-commit independent offline QA, different-model review of the entire
candidate, passing required GitHub checks, manual test results and human merge
approval. Only an operator-enabled deterministic coordinator may request protected
auto-merge after existing gates pass; workers cannot merge, publish, deploy,
change permissions or grant access.
