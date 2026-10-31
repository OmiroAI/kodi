# Delayed review regression verification

Reviews use the existing evidence ledger and coordinator tick. A reservation
stores a validated effective worker timeout, three total attempts, 30/120 second
backoff, a 900 second window, candidate/input identity, request identity and claim.
These values persist across restart and configuration edits. Unknown/interrupted
requests retain their claim and require inspection; expiry cannot replace them.
Completed stages for new validated candidates are separate from failed requests.

Only the shared failure classifier's positive transport/provider signals retry.
Worker execution deadlines, rejected or malformed reviews, quota, authentication
and unknown errors block. Existing explicitly configured quota fallback is
unchanged; retries do not add providers. Retry-After accepts delay seconds or
HTTP dates. Adapters may supply `DeliveryFailure(reset_at=...)` only for a
provider-documented absolute UTC epoch reset; no missing reset is estimated.
Malformed deadlines block, and deadlines outside the window are never shortened.
Provider admission is shared durably between jobs using the same reviewer models.

Waiting state displays the deadline, category and remaining attempts in existing
Kandev progress. Owner wait/block events use existing durable notification keys.
Remote branch/PR and base reconciliation must succeed before releasing repository
ownership. Before a retry, reconciliation runs again. Successful exact-candidate,
requirements-bound QA is retained; coding and QA are not redispatched. Paused,
cancelled or edited authoritative tasks cannot apply a review result. Uncertain
claims and merge mutations keep ownership. Host recovery cannot reset a delayed
review budget or replace its uncertain request.

Manual verification (disposable state and mocked transports only):

1. Run `python3 -m unittest discover -s tests -p test_delayed_review.py -v`.
   The coordinator integration test supplies a timeout then an approving verdict,
   advances a fake clock, and checks the original candidate, PR and single QA
   record. Confirm both worker invocations are review calls.
2. Run the full unittest suite and `python3 scripts/check_public_source.py`.
3. In a disposable queue with mocked GitHub/board/provider adapters, inject a
   typed transport timeout during review. Observe `review_wait` with a future
   deadline and remaining attempts. Tick before the deadline, restart the
   coordinator, and tick at the deadline. Confirm no early/duplicate request,
   no coding/QA replay, and the preserved candidate reaches validated state.
4. Repeat with a provider deadline outside the window, malformed deadline,
   uncertain request, exhausted budget, paused task and changed requirements.
   Confirm blocked/inspection behavior and no replacement provider request.
   Change the timeout configuration and attempt host recovery; confirm the
   retained effective timeout and consumed budget do not reset.

These local mocked regressions are not independent acceptance. Exact-commit
independent offline QA, different-model source review, required GitHub checks and
human approval remain separate gates. These steps do not authorize live provider
calls, service operations, production scheduling or resuming paused jobs.
