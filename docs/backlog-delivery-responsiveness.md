# Keep Kodi responsive and limit delivery lock scope

Repo: system
Status: Backlog — planning only; not scheduled or Ready.

This local ticket records requirements, not permission to execute them. Do not
start workers or resume paused issue #9 or job #48 without separate explicit
owner authorization. No runtime behavior changes are included in this ticket.

## Problem and scope

`Coordinator.tick` currently holds `delivery.lock` across `_tick`, including
synchronous job processing. Long coding, independent QA or review runs can delay
board sync, CI observation, approval handling and unrelated approved PRs.

Keep the existing deterministic coordinator and Kandev UI. Separate worker
execution from delivery ownership so coordination and owner actions remain
responsive while workers run. This ticket owns responsiveness and delivery
ownership; coordinate interfaces with issue #9, which covers bounded parallel
execution. Do not add another agent framework, dashboard or service architecture.

## Proposed implementation requirements (pending authorization)

- Run long worker operations outside the global delivery critical section using
  the existing coordinator architecture. Persist job identity and candidate
  identity so completion can be reconciled without accepting stale results.
- Serialize remote branch updates and protected merge requests across daemon,
  CLI and restart paths. Persist mutation intent before issuing a write. Retain
  logical delivery ownership while a write outcome is uncertain, even after the
  process lock is released or its process exits. Reconcile remote identity before
  retrying or handing ownership to another mutation; never blindly repeat writes.
- Keep read-only observation, board sync and owner actions available during
  worker execution and uncertain delivery. A safe unrelated approved PR may
  progress while another job runs a worker, but cannot bypass unresolved remote
  mutation ownership.
- Prioritize approved PR maintenance over background learning. Use deterministic
  bounded fairness so continuous maintenance cannot starve ordinary eligible
  work; document and test the selected bound. Do not resume paused work.
- Revalidate the current base, recorded candidate and remote branch identity
  before mutation. Preserve stale-approval dismissal and require fresh acceptance
  whenever a candidate or base change invalidates it.
- Record delivery-lock wait and hold durations and approval-to-progress delay,
  with job/candidate correlation and defined start/end events. Distinguish
  blocked or still-waiting approvals from completed observations; preserve
  timing semantics across restart without exposing private operational data.

## Acceptance and verification plan

1. In fictional disposable fixtures, hold coding, QA and review workers open in
   turn. Advance coordinator cycles and verify board sync, CI observation and
   approval processing continue. Verify a safe unrelated approved PR progresses
   before the worker completes.
2. Race daemon/CLI delivery attempts and restart around persisted intent, remote
   write and response recording. Assert only one protected update/merge occurs,
   uncertain writes retain ownership, and reconciliation precedes any retry or
   ownership transfer. Observation and owner actions must still progress.
3. Change the base or remote head between validation and mutation. Verify
   current-base revalidation, stale-result rejection and stale-approval dismissal;
   require exact-candidate QA, different-model review, checks and human approval
   before subsequent delivery.
4. Queue approved maintenance, ordinary work and background learning together.
   Verify maintenance priority and ordinary-work progress within the documented
   fairness bound, including sustained maintenance arrivals.
5. Inspect timing evidence for lock contention, worker execution, an approval
   waiting on gates and restart recovery. Verify lock wait/hold and
   approval-to-progress measurements reflect their documented events.

For an authorized implementation, run
`python3 -m unittest discover -s tests -v` and
`python3 scripts/check_public_source.py`, and review the full diff for disclosures.
Use fictional fixtures only. Manual verification follows the disposable scenarios
above; no live worker, approval, update or merge is authorized by this document.

Delivery acceptance remains pending: record exact-commit independent offline QA,
different-model review of the complete candidate, required GitHub CI and human
CODEOWNER approval. Coding/review workers cannot publish, merge, deploy, change
workflow permissions or grant access. Only an explicitly enabled deterministic
coordinator may request protected auto-merge after all existing gates pass.
