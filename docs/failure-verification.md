# Delivery failure verification

Use disposable local state and synthetic adapters only. Run
`python3 -m unittest discover -s tests -v` and
`python3 scripts/check_public_source.py`.

Inspect `failure:<stage>` evidence in the disposable database: category, allowlisted
reason, candidate/request identity, opaque private reference, responsible actor,
next action and retry metadata must contain no adapter response text. Original
worker logs and QA JSON remain private artifacts. A killed process without an
explicit resource event stays unknown. QA repair requires a structured test
diagnostic; legacy receivers without that signal fail closed for inspection.

Simulate an OOM through `DeliveryFailure('oom_event')` in a QA adapter. Repeat
`execute` with identical job/stage/candidate and recovery inputs after reopening
the store: the adapter must not run again. Supply a changed relevant resource
revision through `recovery_inputs`: one invocation is allowed; another unchanged
failure blocks. Recovery inputs must represent inspected availability or relevant
configuration, never a timestamp or unrelated setting. This API does not requeue
blocked jobs or authorize live recovery.

Simulate HTTP 429, explicit exhausted quota, 401/403 and an unknown error containing
a secret sentinel. Confirm only ordinary throttling gets automatic backoff, with
two retries, 30/120 second minimum delays, and no sentinel in stored summaries.
A longer supplied delay is honored; invalid or over-window delays block.
Interrupted reservations require inspection rather than blind replay.

Missing CI stays unknown/pending. Required checks still determine acceptance.
With synthetic current-commit checks, combine a generic failure or error status
with `startup_failure`, then with `timed_out`. Confirm diagnostics retain
`ci_outage` and `execution_deadline` respectively, regardless of check order,
while acceptance remains false. Competing specific causes or evidence from a
different commit must not establish a single specific cause.
Reuse the diagnostics mapping for a failed poll, a changed PR head and a passing
poll: no previous failure cause may survive. In disposable coordinator state,
seed synthetic exact-candidate QA/review evidence and poll disabled, startup-failed,
timed-out and conflicting check responses through the real check reader. Confirm
one blocked transition, a sanitized `failure:ci` record, and no draft promotion or
candidate repair. Reopen the database and poll again: blocked jobs must not repeat
CI work. Simulate PR closure and confirm cancellation retains the failure record
and terminal delivery remains deduplicated.
Classification cannot make them pass or repair code to fix missing checks. Closed
PR reconciliation retains terminal outcomes. Verify QA, different-model review,
required CI and human approval remain separate gates. Local mocked tests are
regression evidence only; independent exact-commit acceptance is still pending.
