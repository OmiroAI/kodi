# Bounded worker admission

Worker capacity is opt-in. Without `capacity`, the existing single active-job
queue rule remains in effect. An operator can configure a disposable coordinator
with the following complete policy; this does not change a running installation:

```json
{
  "capacity": {
    "stages": {"coding": 2, "qa": 1, "review": 1},
    "providers": {"codex": 2, "qa": 1, "antigravity": 1, "qwen": 1}
  }
}
```

Each limit must be an integer from 1 to 64 (booleans are rejected). Configure
capacity for every provider used, including QA and any configured review fallback.
Repository coding ownership still applies: concurrent coding tasks must belong
to different repositories. Delivery updates and merges retain the existing
exclusive lock, remote identity checks, current-base validation and acceptance
gates. Workers gain no delivery permissions.

A SQLite transaction reserves the stage and provider together. Waiting for a
provider reserves neither. Claims retain job, attempt, candidate, invocation
owner, monotonically increasing per-job generation, heartbeat and lease.
Heartbeats renew every ten seconds with a sixty-second lease. Expiry fences
writes but never releases capacity or authorizes a replacement. Restart does
not reap claims. Unknown worker liveness requires operator inspection using the
existing invocation and retained artifacts; do not delete claims to free slots.
A returned worker result releases its own capacity, including a rejected stale
result. Explicit quota exhaustion releases capacity for the existing configured
fallback. Other exceptions retain uncertain ownership for inspection; they do
not authorize a replacement. Claim history and per-job budgets remain intact.

The existing CLI `list` output and Kandev progress descriptions include capacity
occupancy, configured limits, uncertain leases and persisted wait durations.
Board-backed work is checked against Ready before coding, and against its stage
and original requirements before provider admission. Local pause/cancel changes
invalidate invocation and capacity snapshots even if the original state is later
restored. Remote board observations cannot be transactional with SQLite; a later
observation of withdrawn authority refuses admission rather than granting recovery.

## Disposable manual verification

1. Run `python3 -m unittest discover -s tests -p test_capacity.py -v`. Its scheduler
   fixture uses two fictional repositories and fake workers. Confirm both enter
   concurrently with distinct job checkout/artifact directories and complete
   under separate durable invocations. No live provider or service is needed.
2. With a temporary state database and the policy above, hold one fake QA worker
   and one fake Qwen reviewer. Attempt a second of each and a Codex worker;
   confirm QA/Qwen bounds and independent coding progress. Inspect `list` and
   board progress for the specific wait reason, occupancy and configured limits.
3. Restart the disposable coordinator while claims remain occupied. Advance an
   expired claim's clock in the fixture, and confirm no replacement is launched
   and expired ownership cannot write results. Preserve artifacts for inspection.
   Pause/cancel a waiting fixture and repeat, including pause followed by restore.
4. Repeat admission denial after reopening Store. Confirm elapsed wait continues
   from its original timestamp and attempts/budgets have not reset.
5. Run `python3 -m unittest discover -s tests -p test_merge_maintenance.py -v`
   and the responsive coordination regressions. These exercise delivery exclusion,
   current-base validation and stale invocation fencing using disposable mocks.

Local/mocked checks are regression evidence only. Exact-commit independent offline
QA, different-model source review, required GitHub checks and human approval remain
separate gates. These instructions do not authorize live limit changes, service
operations, recovery or resumption of paused jobs.
