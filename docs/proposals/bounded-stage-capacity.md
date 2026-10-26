# Run approved tasks in parallel with bounded stage capacity

Status: Backlog — planning only.
Repo: system
Dependencies: responsive coordinator delivery ownership and provider admission controls.

This document is a ticket draft, not dispatch authorization. Do not move work to
Ready, schedule it, start workers, resume paused work, or increase live limits as
a result of this proposal. Existing paused issues/jobs require separate explicit
owner authorization. Implementation and operator activation require later approval.

## Problem and scope

One globally active job prevents independent approved work from progressing.
Extend the deterministic coordinator with operator-configurable stage and provider
capacity, using existing Kandev board/status surfaces. The initial opt-in target
is two coding tasks, a separately configured QA bound, and one Qwen review request
at a time. Preserve current live limits until an operator explicitly changes them;
issue content cannot configure capacity or authorize work.

## Proposed behavior

- Admit only explicitly approved Ready tasks for configured, enabled repositories.
  Recheck eligibility atomically when claiming work. Backlog, proposal-only,
  paused and cancelled tasks cannot acquire or renew execution authorization.
  Capacity availability and restart never imply approval or resumption.
- Require both a stage slot and the applicable provider slot before dispatch.
  Acquire them atomically with a durable job/stage claim; avoid reserving one
  while waiting for the other. A saturated provider must not block eligible work
  on another provider or stage. Use deterministic ordering among eligible tasks.
- Persist claim identity, job/stage, provider, owner, lease deadline and a fencing
  generation. Enforce capacity across concurrent coordinator processes and
  restarts. Heartbeats extend only the current claim. Reject stale owners'
  writes, stage transitions and results after ownership changes.
- Lease expiry alone must not launch a replacement: reconcile the recorded worker
  identity and confirm the previous worker has stopped before releasing its
  capacity. Uncertain liveness blocks that claim for inspection. Restart adopts
  or reconciles existing claims instead of duplicating workers. Cancellation or
  pause revokes dispatch eligibility and reconciles active work before releasing
  slots; preserved results cannot implicitly restart the task.
- Isolate writable checkouts, output directories, evidence and worker identities
  by job and attempt. Bind evidence to the exact candidate commit and claim.
  Persist per-job stage/retry budgets; waiting, restart and lease recovery must
  not reset them or mix artifacts between jobs.
- Keep branch updates and merges under the existing exclusive delivery ownership.
  Parallel workers do not acquire publishing authority. Revalidate the current
  base before merging; a changed candidate/base requires fresh exact-commit
  validation and applicable human approval. No bypass of protection or gates.
- Show queue reason, stage occupancy/limit, applicable provider occupancy/limit
  and elapsed wait on existing board/status surfaces. Persist when an eligible
  stage begins waiting so restart preserves wait time. Distinguish approval,
  pause/cancellation, stage capacity, provider capacity, delivery ownership and
  uncertain lease recovery; do not expose private paths or worker output.

No additional agent framework, dashboard, service topology, deployment changes,
permission changes or automatic live capacity increase are included.

## Acceptance and manual verification plan

Use disposable repositories, fictional tasks and isolated private runtime state.
These checks are planned; this ticket does not execute or authorize them.

1. Explicitly approve two independent Ready tasks and opt into coding capacity
   two in the disposable environment. Observe both coding workers progressing
   concurrently with distinct writable checkouts, identities, outputs and evidence.
   Verify QA obeys its independent bound and concurrent Qwen requests never exceed
   one, including fallback requests admitted through provider controls.
2. Race two coordinator instances for the last stage/provider slot, then restart
   during execution. Confirm persisted occupancy never exceeds either limit and
   the recorded worker is adopted/reconciled without duplicate launch. Simulate
   lease expiry with uncertain liveness and reject a stale owner's result; confirm
   no replacement starts until termination is confirmed.
3. Leave Backlog and proposal-only tasks queued, pause one waiting task and cancel
   another. Free capacity and restart. Confirm none starts or resumes, including
   previously paused work. Race pause/cancel with claiming and confirm eligibility
   and claim ownership prevent unauthorized dispatch.
4. Saturate Qwen while eligible coding, QA and another permitted review provider
   have free capacity. Confirm unrelated work progresses, waiting work consumes
   no unrelated slot, and board/status shows the specific queue reason, current
   occupancy/limit and wait time retained across restart.
5. Exhaust one job's retry budget and restart. Confirm its budget remains exhausted
   while another job retains its own budget; inspect evidence for cross-task
   leakage and exact candidate/claim identity.
6. Bring two candidates to delivery while changing the target base. Confirm only
   one branch update/merge owns delivery at a time and the newer base triggers
   revalidation before any protected merge request. Verify changed candidates
   cannot reuse prior acceptance or approval.

## Delivery gates

For a later implementation, run `python3 -m unittest discover -s tests -v` with
regressions for concurrent admission, recovery, eligibility races, isolation,
provider independence and serialized delivery. Run the public-source checker and
review the complete diff for disclosures. Runtime configuration and evidence stay
in private storage; fixtures use fictional organizations and reserved addresses.

A ready implementation PR requires independent offline QA at the exact candidate
commit, a different-model review of the complete candidate, required passing
GitHub checks, manual testing instructions and human CODEOWNER approval. Local
regressions are not independent acceptance. Coding/review workers cannot publish,
merge, deploy, access production, alter workflow permissions or grant access.
