# Expose delivery time, retries and current blockers

Repo: system
Status: Backlog — planning only; not scheduled or Ready.

This local ticket records requirements, not implemented guarantees or permission
to start workers, resume jobs or move work to Ready. Implementation requires
explicit prioritization. No runtime behavior changes are included here.

## Problem and existing surfaces

Long deliveries make useful execution difficult to distinguish from repeated
failures, approval waits and blocked queues. Existing `Coordinator.progress`
shows state, candidate, error and aggregate run counts; `kodi/usage.py` reports
worker usage and evidence counts. Those counts are not a phase timeline or proof
of currently accepted evidence. `Store` retains commit-bound evidence and recovery
reservations, but job creation/update timestamps cannot reconstruct past phases.

Extend those existing Kodi status/activity surfaces and the Kandev statistics
integration in `kodi/activity.py` and `kodi/activity_payloads.py` where supported.
Keep accounting export in its separate timer, outside delivery and progress
paths; preserve the blocked-receiver regression in
`tests/test_activity_delivery_isolation.py`. Do not add a dashboard or framework.

## Proposed behavior

- Persist bounded, structured phase transitions with stable event, job, attempt
  and candidate identities, start/end times, phase, outcome and sanitized reason
  codes. Record pre-candidate work explicitly as candidate unknown; never assign
  it retroactively to a later commit. Include coding, QA, review, gate waits,
  recovery and terminal outcomes. Repeated observation of an unchanged phase
  must not create another transition or reset its start time.
- Report per-job executing, waiting and blocked elapsed time plus phase history,
  retry counts and current blockers. Executing means observed active work;
  waiting means an expected dependency such as approval, checks, capacity or
  eligible retry backoff; blocked means progress requires intervention or
  reconciliation. Terminal jobs stop accruing time. Display an as-of timestamp
  and distinguish open intervals from completed measurements.
- Define job wall time using disjoint intervals: executing takes precedence when
  any work is active, otherwise blocked takes precedence over waiting. Preserve
  simultaneous blockers as annotations. Union overlapping intervals within each
  category before totaling; keep summed worker/resource time separate from job
  wall time. Never add overlapping worker, gate and retry intervals together.
- Expose the next actionable step and owner using agent, operator, reviewer or
  external service. For example, approval needs reviewer action; interrupted
  work needs operator inspection; pending checks need the external service.
  Where several dependencies exist, show all blockers and a deterministic next
  step without implying that resolving it satisfies every gate. Do not provide
  completion estimates; a known retry eligibility time is only a retry deadline.
- Count actual attempts separately from retries and persisted reservations.
  Break retries down by phase/action and candidate, including failed and
  interrupted attempts. A reserved attempt with uncertain dispatch stays
  uncertain rather than becoming a completed run or disappearing on restart.
  Observability must not replenish budgets or initiate recovery.
- Attribute phase/gate evidence to the exact candidate and existing acceptance
  context. Distinguish passed attempts from currently accepted evidence, failed
  attempts, invalidated/superseded evidence and unknown validity. Show the
  invalidation reason when requirements, candidate or base changes invalidate
  acceptance under existing policy. Preserve earlier attempts for accounting;
  they cannot approve a newer candidate. Do not infer acceptance from counts,
  approval prose or missing historical records, or change gate decisions.
- Persist transition identity and timing atomically with the observed state
  change. Replay/export must be idempotent across restart and acknowledgement
  loss. Measure live durations with a monotonic clock; retain timestamp provenance
  for persisted intervals. Interrupted or clock-ambiguous periods remain unknown
  until supported observations establish their boundaries; do not bill downtime
  as execution. Identify partial/missing history and its coverage start, rather
  than synthesizing phases from `jobs.updated` or treating unknown time as zero.
- Bound event sizes, collection batches and retention with documented numeric
  limits before implementation acceptance. Compact old detail only with preserved
  deduplicated totals and explicit history coverage/truncation markers. Use
  allowlisted reason/action codes and minimal identifiers; exclude raw errors,
  logs, prompts, responses, credentials and private operational details. Keep
  private state outside Git. Export only supported allowlisted activity fields;
  unsupported/offline Kandev leaves local status usable and delivery unaffected.

## Acceptance and manual verification plan

Use fictional jobs, synthetic sanitized evidence, disposable state and a
controllable clock. These are future implementation checks, not completed tests.

1. Advance one job through execution, retry backoff, approval wait, intervention
   and completion. Compare status, phase history and statistics with the clock;
   verify current reason, next step and owner at each phase, with no ETA.
2. Replay identical transitions and export events, including lost acknowledgements.
   Restart before/after transition persistence, attempt reservation and dispatch.
   Verify stable totals, no duplicate retries and explicitly uncertain interrupted
   work. Include clock rollback and missing end events; no negative or invented
   elapsed time may appear.
3. Overlap execution on [0,10) and [5,15), with an approval wait on [0,20).
   Verify 15 seconds executing, 5 waiting and 20 observed job wall seconds;
   show 20 worker seconds separately. Add simultaneous blocked/waiting spans
   and verify precedence and blocker annotations without double-counting.
4. Record failed and passed QA/review attempts for candidate A, then invalidate
   them and move to B. Verify A remains attributed to A, B has no accepted
   evidence until existing gates pass, and retries/attempts remain distinct.
   Include changed requirements/base and pre-candidate activity.
5. Load a legacy job with only current state, timestamps and partial usage.
   Verify missing history, unknown durations/counts and coverage boundaries are
   visible. Test retained/compacted history and empty jobs without invented zeros.
6. Exceed documented collection/retention limits, replay compacted events and
   inject secret-like sentinels into synthetic raw failures. Verify bounded
   collection, stable totals and no sentinel in status or export. Hold the
   accounting receiver blocked and test unsupported statistics capability;
   delivery/progress remain independent and local observations stay available.

For an authorized implementation, run
`python3 -m unittest discover -s tests -v` and
`python3 scripts/check_public_source.py`, then manually review the complete diff
for disclosures. Delivery acceptance requires exact-commit independent QA,
different-model review, manual testing evidence, required GitHub checks and human
merge approval. Mocked regressions alone are not independent acceptance.

## Boundaries and related proposals

This ticket observes existing behavior; it does not alter gate policy, schedule
parallel work or implement repository CD. Coordinate timing semantics with
[delivery responsiveness](../backlog-delivery-responsiveness.md),
[bounded stage capacity](../proposals/bounded-stage-capacity.md) and
[reviewer reliability](reviewer-reliability.md), all still proposals. Host/resource
permissions belong to workspace instructions. Workers cannot merge, publish,
deploy, change permissions or grant access. Repository CD remains repository-owned.
