# Delivery timing

Kodi now observes queue, execution, gate wait, retry backoff and terminal state
changes atomically with local job updates. `usage --tasks` includes
`delivery_timing`; existing Kandev task descriptions show observed wall seconds,
partial-history status, blockers, next action and actor. Accounting HTTP remains
in its separate timer. No native Stats ingestion contract is expanded.

Wall seconds use the union of intervals, with execution taking precedence over
blocked time, then waiting. Resource seconds come separately from the usage
ledger, and remain unknown when absent. An execution phase measures the observed
coordinator phase, not CPU time or provider inference time. Each interval has a
job-local event identity, phase attempt, candidate (or null), monotonic clock
session and wall timestamps. No completion ETA is produced. Recovery reservations
remain reservations, not proof of dispatch or completed work.

A job retains at most 128 phase observations before checkpointing disjoint totals
and retaining open intervals. Seven fixed interval owners bound concurrent state.
Compaction preserves totals and marks truncated detail. Evidence projections and
context fingerprints retain the latest 128 entries; recovery projections collect
at most 128 reservations. Evidence identities refer to existing ledger IDs, with
candidate/requirements changes and superseded attempts distinguished from local
gate satisfaction. Local gate satisfaction is not independent delivery acceptance.
Unknown legacy evidence contexts stay unknown. No raw error text enters timing.

New jobs have queue coverage from observation. Legacy jobs have partial history;
restart gaps and interrupted execution are unknown, never charged as execution.
Only intervals measured in one monotonic clock session contribute seconds. A
restart cannot reconstruct elapsed time before a missing end event. Wall-clock
rollback cannot make elapsed seconds negative. No prior job timestamps are used
to synthesize missing intervals. History collection cannot initiate recovery,
change gate policy or resume paused jobs.

Manual verification in disposable local state:

1. Enqueue a fictional task, advance it through coding, QA, review, CI pending,
   human review, blocked and done. Inspect `usage --tasks` and the existing task
   description; check actor/action changes and that done stops accruing time.
2. Repeat an unchanged update and reload the database in a new process. Verify
   event identities persist, unchanged observations do not restart an interval,
   and interrupted execution has unknown duration rather than restart downtime.
3. Record synthetic QA/review evidence for candidate A, then set candidate B.
   Verify A remains in history, B has no reused evidence, and usage resource
   seconds remain separate from phase wall seconds. Change requirements and
   verify evidence context is marked changed, without altering gate policy.

Automated fake-clock tests cover overlaps, precedence, retries, rollback,
compaction, absent history and atomic failure. These are regression checks;
exact-commit independent offline QA, different-model source review, required CI
and human approval remain separate acceptance requirements.
