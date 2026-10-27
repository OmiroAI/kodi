# Retry transient reviewer failures after a bounded delay

Repo: system
Status: Backlog — planning only; not Ready or scheduled.

This local ticket draft does not create a remote task or authorize implementation,
worker startup, live timeout/configuration changes, or resumption of paused work.
Separate explicit owner approval is required before scheduling implementation.

## Problem and shared scope

A reviewer timeout can leave an otherwise QA-passing candidate blocked until an
operator retries it. Plan durable delayed recovery of the original review stage,
preserving the job, checkout, candidate SHA, successful valid QA evidence and PR
identity. This proposal does not establish implemented retry guarantees.

Extend the existing deterministic coordinator, runner failure handling, recovery
budgets and store rather than adding a second retry engine. Use existing Kandev
status and Telegram delivery capabilities; do not add an agent framework or UI.

Coordinate ownership with these related Backlog tickets:

- Handle reviewer throttling and inconsistent verdicts
  (`d5c2970b-b093-469b-929d-f533ae28caae`), recorded in
  [reviewer reliability](reviewer-reliability.md): share failure classification,
  provider admission, retry metadata and budgets. This ticket owns durable delayed
  review retries; verdict consistency and finding resolution remain there.
- Track recovery and require explicit scheduling approval
  (`c35b0ab1-a9ed-460f-bd01-d69f8de6982d`): share recovery eligibility and owner
  controls. Recovery must not turn Backlog or deferred work into authorized work.
- Release safe merge reservations while awaiting approval
  (`bc944b9f-b536-4c3d-8a97-f8801538a090`): share reconciled delivery ownership;
  review waiting must not introduce a competing reservation-release mechanism.

Align with [delivery responsiveness](../backlog-delivery-responsiveness.md) and
[bounded stage capacity](../proposals/bounded-stage-capacity.md), both proposals.
Do not assume proposed leases or parallel execution already exist.

## Required behavior for an authorized implementation

- Preserve sanitized actionable categories for request/socket timeouts, temporary
  connection failures, transient server failures and short-term throttling.
  Distinguish exhausted quota, authentication/permission errors, invalid requests
  and unknown failures. Use positive structured evidence; a bare 429 is not quota
  exhaustion. Do not expose raw errors, response bodies, sensitive headers,
  endpoints, prompts or credentials. Unknown or ambiguous failures need inspection.
- Retry only positively identified transient request failures. Persist bounded
  backoff, `next_retry_at`, consumed attempt count, total budget and retry-window
  deadline. Define whether the initial request counts and display remaining retry
  attempts consistently. Persist effective policy so restarts, manual retries and
  configuration changes cannot replenish an existing budget.
- Make request timeout, backoff bounds, attempt limit and total retry window
  operator configurable with documented validated defaults and finite upper
  bounds. Reject booleans, invalid types, non-finite values, nonpositive bounds
  and inconsistent ranges before dispatch; explicitly define any disable value.
  Select and test defaults during implementation without changing live settings.
- Honor valid provider Retry-After seconds or HTTP dates and documented reset
  signals. Never dispatch before either local backoff or a valid provider
  deadline. Ignore malformed metadata safely; distinguish local eligibility from
  a provider reset and never guess quota reset times. A provider deadline beyond
  the retry window must defer/block with an actionable reason, not retry early.
- Resume only the failed review stage on the original preserved job/candidate.
  Do not restart implementation or rerun successful same-SHA QA while its evidence
  remains valid. Recheck candidate, requirements and relevant base identity before
  dispatch and acceptance. Drift invalidates stale actions and acceptance under
  existing gates; a PR number cannot substitute for candidate identity.
- Atomically persist request identity, attempt reservation and durable claim
  before dispatch. Use shared provider admission and fencing/idempotency across
  concurrent scheduler ticks, CLI retries and daemon restarts. Reconcile a request
  interrupted before/during dispatch before replacement; lease expiry alone must
  not duplicate a possibly active request. Uncertain outcomes require inspection
  when safe reconciliation is unavailable. Honor configured provider capacity.
- Recheck owner authorization when scheduling and claiming. Pause/cancellation
  overrides scheduled retries, including races with claiming. Backlog, deferred
  or otherwise ineligible jobs never dispatch through recovery. Stale claim
  results cannot change state or authorize publication.
- Distinguish waiting for retry from blocked requiring inspection in Kandev and
  status. Show sanitized failure category, next retry time, remaining attempts
  and final outcome. Persist bounded Telegram notification deduplication per
  job/candidate/wait transition; repeated ticks and restarts must not repeat alerts.
- On exhaustion, stop with an actionable blocker naming the category, exhausted
  budget and existing owner-controlled inspection/recovery options. Repeated
  manual retry cannot reset budgets. Rejected verdicts, invalid reviews, quota,
  auth and unknown failures are not transient transport retries. Never enable
  paid fallback, broaden permissions or route repeatedly until approval.
- Release delivery ownership during review backoff only after remote state is
  reconciled and safe, allowing unrelated eligible work to progress. Uncertain
  branch updates, publication and armed merges retain ownership until reconciled;
  a retry deadline cannot bypass serialization or authorize a remote write.

## Acceptance and manual verification plan

Use fictional disposable repositories, synthetic provider responses, isolated
durable state and a controllable clock. These are future checks, not completed
acceptance evidence; no live provider calls or credentials are needed.

1. Produce a timeout after successful exact-SHA independent QA, advance to just
   before/at eligibility, then return a valid different-model review. Confirm one
   delayed request on the original job/SHA, unchanged PR identity and QA evidence,
   no coding or redundant QA, and correctly consumed durable budgets.
2. Exercise repeated timeout, socket/connection failures, 5xx and temporary 429;
   check bounded backoff and exhaustion. Contrast explicit quota exhaustion,
   401/403, invalid requests/reviews, unknown errors and review rejection: none
   enters transport retry or creates acceptance. Inject secret-like sentinels
   and verify sanitized category-specific status and notifications.
3. Check Retry-After seconds/date, documented reset metadata, missing/malformed
   signals and deadlines beyond the permitted window. Verify no early retry or
   invented reset. Validate all timeout/backoff/attempt/window defaults, boundaries
   and invalid values before any provider request.
4. Restart during backoff, before dispatch and during a request. Race scheduler
   ticks from separate instances and repeat manual retry. Verify no duplicated
   request, replenished budget or provider-capacity breach; uncertain in-flight
   work must reconcile or remain blocked rather than blindly replay.
5. Pause/cancel waiting jobs and leave deferred/Backlog jobs eligible by time
   alone. Race owner actions with claims and restart; none may dispatch. Change
   candidate SHA, requirements or relevant base while waiting and during a request;
   reject stale scheduled actions/results and require applicable fresh acceptance.
6. Observe Kandev/status through waiting, success and exhaustion. Confirm next
   retry, category and remaining attempts match durable state. Repeat ticks and
   restarts in each wait and verify bounded deduplicated Telegram updates.
7. During backoff, progress unrelated eligible work after safe remote-state
   reconciliation. Repeat with uncertain branch update, publication and armed
   merge state: delivery ownership remains serialized until reconciliation.
8. Verify recovery never bypasses exact-commit independent offline QA,
   different-model complete-candidate review, required GitHub checks or human
   CODEOWNER approval. Local mocked verdicts verify gates, not independent
   acceptance. Record manual instructions and exact-commit evidence for delivery.

For a later authorized implementation, run
`python3 -m unittest discover -s tests -v` and
`python3 scripts/check_public_source.py`, then review the entire diff for
disclosures. Keep runtime configuration, credentials and operational evidence in
private storage outside Git. Coding/review workers cannot merge, publish, deploy,
access production, alter workflow permissions or grant themselves access.
