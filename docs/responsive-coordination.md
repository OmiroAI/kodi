# Responsive coordination

Delivery ticks reserve one durable invocation before starting a local execution
thread. Coding, independent QA, source review and base repair run outside
`delivery.lock`. Existing limits and repair budgets still apply. Ticks observe
CI, synchronize the board and handle approved unrelated PRs while execution is
running. Ordinary queue selection remains ordered by job ID, after maintenance;
this change does not enable parallel worker capacity.

Each invocation has a unique claim, operation, original candidate and a snapshot
of its current stage, candidate, attempt and requirements. Result writes compare
the claim and snapshot inside a SQLite write transaction. Changes to job state,
candidate, attempt, requirements, branch or PR identity
invalidate the snapshot even when the state is subsequently restored. Metadata
refreshes and idempotent updates retain ownership. SQLite transactions commit
worker writes immediately, so control connections observe them during execution.
Usage is still recorded for stale execution; acceptance and budget writes are fenced.
The executor never publishes. A later control cycle rechecks acceptance, checkout,
head and base before publication or a leased update. Mutation intent and protected
merge requests remain serialized with the existing delivery lock. Uncertain
publication retains its existing inspection requirement.

A restart does not reclaim running invocations. The process may still have a
live worker, and neither an old timestamp nor an expired lease authorizes a
replacement. Inspect the existing `list` command's invocation record and private
worker artifacts; unknown liveness retains ownership of that job. Queue capacity
remains governed by the existing active job states; invocation identity does not
add a global slot. No worker is
stopped or resumed automatically. Workspace submission releases the same lock
around independent validation, retaining a durable fenced claim until it
reacquires the lock for publication.

Timing records are in the private state's `coordination_metrics` table:
`lock_wait` measures the successful nonblocking acquisition attempt, `lock_busy`
records a refused attempt, and `lock_hold` runs from acquisition to cycle exit.
These are control-cycle measurements, not worker runtime. `approval_observed`
records the first eligible approval observed for a job/candidate;
`approval_progress` measures elapsed wall time from that observation to dispatch
of an update or merge request. Waiting approvals have no progress record. These
records survive restart; they do not claim the time the owner actually clicked
approval, or that GitHub completed a request. Network and Git observations retain
their existing timeouts; this change removes worker runtime from lock holds.

Manual verification uses disposable state and mocked workers only:

1. Run `python3 -m unittest discover -s tests -p test_responsive_coordination.py -v`.
   Hold each fixture stage and confirm other ticks finish, observe CI and retain
   one invocation. No real provider or service is required.
2. Run `python3 -m unittest discover -s tests -p test_merge_maintenance.py -v`.
   Confirm the unrelated-worker merge, asynchronous base lease and competing
   mutation scenarios pass without issuing remote mutations.
3. Inspect a disposable invocation after simulated uncertain start, then open a
   second coordinator on that state. Confirm its old timestamp cannot dispatch
   another worker. Cancel or pause a held fixture and verify its result cannot
   become validated, including pause followed by restoration of the old state.

Local and mocked tests are regression evidence. Exact-commit independent offline
QA, different-model review, required CI and human approval remain separate
acceptance gates. No live service operation or paused-job resumption is needed
for these verification steps.
