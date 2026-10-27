# Handle reviewer throttling and inconsistent verdicts

Repo: system
Status: Backlog — planning only

This ticket does not authorize scheduling, moving work to Ready, starting workers,
or resuming any paused issue or job. Those actions require separate explicit owner
authorization. This file is a local ticket draft; it does not create a remote task.

## Problem and existing integration points

Provider request failures can become generic blockers, while reviewer output can
combine conflicting booleans, approval prose and self-retracted concrete findings.
Neither failed requests nor inconsistent reviews may become acceptance evidence.

Extend the existing deterministic review path in `kodi/coordinator.py`, review
validation in `kodi/policy.py`, recovery budgets in `kodi/recovery.py` and
`kodi/store.py`, and usage reporting in `kodi/usage.py`. Integrate with stage
capacity and supported operator recovery controls; use existing Kandev capabilities
for presentation. Do not add an agent framework or dashboard.

## Required behavior

- Classify transport failures (including timeouts), server failures, temporary
  throttling, exhausted quota, authentication/permission errors and unknown
  failures. Use HTTP status plus documented structured provider signals: a 429
  alone must not imply exhausted quota. Ambiguous errors stay unknown. Emit
  sanitized category-specific reasons and next actions without raw response
  bodies, headers, credentials, endpoints or prompts.
- Add provider admission control, initially one in-flight Qwen request across
  concurrent coordinator requests. Coordinate admission with stage capacity;
  queued provider work must not consume unrelated stage capacity indefinitely.
  Release reservations on completion/failure and reconcile interrupted requests
  conservatively through existing recovery controls before replacement dispatch.
- Apply bounded retries/backoff only to eligible temporary failures. Honor valid
  provider Retry-After delay/date and documented reset metadata. Persist attempt
  reservations and next eligible times atomically before dispatch so restarts,
  simultaneous requests, clarification and fallback cannot reset retry budgets.
  Distinguish local backoff deadlines from provider reset times; absent or invalid
  metadata must never produce an invented reset time. If a provider deadline
  exceeds the permitted retry window, defer/block rather than retry early.
- Stop automatic retries on exhausted budgets, quota, authentication/permission
  errors or unknown failures and provide an actionable operator recovery reason.
  Never enable paid fallback automatically. Use only operator-enabled fallbacks,
  with configured reviewer/author family independence checked before every
  primary, fallback, clarification or independent re-review request. Preserve
  existing tool/asset capability restrictions and complete candidate context.
- Validate the final schema with strict field types and verdict consistency.
  Approval requires mutually consistent verdict fields, summary and findings;
  malformed JSON, conflicting booleans, approval prose contradicting rejection,
  or unresolved concrete defects cannot pass acceptance.
- Permit only bounded clarification or an independent re-review under explicit
  persisted budgets. Preserve every prior response, concrete finding, candidate
  SHA, provider/model identity and relationship between attempts in private
  evidence. A finding cannot disappear because a later response omits it or
  retracts it without evidence. Record an explicit evidence-backed disposition
  for each concrete finding; unresolved or contradictory dispositions block.
  Exhaustion remains blocked, never a loop until approval. Changed commits
  require fresh acceptance evidence under existing gates.
- Measure request latency separately from provider queue time. Record rejection
  category counts and clarification frequency (attempts and denominator), with
  provider/model and candidate attribution in private state. Include failed
  attempts and recovery without double-counting after restart. Expose aggregate
  measurements through existing usage/reporting surfaces; do not invent usage
  counts, reset times or prices.

## Acceptance and manual verification

Use fictional providers/repositories, synthetic responses, a controllable clock
and disposable state. No live provider requests or credentials are required.

1. Exercise temporary 429 versus explicit quota exhaustion, timeout, transport
   disconnect, 5xx, 401/403 and unknown failure. Verify category-specific sanitized
   reasons, eligible retries only and no implicit fallback. Include secret-like
   sentinels in synthetic errors and confirm they never reach reported reasons.
2. Exercise Retry-After seconds/date, documented reset metadata, missing/invalid
   metadata and a deadline beyond the retry window. Verify no early request,
   invented provider reset or unbounded wait/retry.
3. Restart between reservation and dispatch, during an interrupted request and
   during backoff. Submit simultaneous Qwen requests from separate coordinator
   instances. Verify at most one active request, durable retry limits, recovery
   of interrupted reservations and correct queue/capacity accounting.
4. Try same-family primary/fallback reviewers, disabled fallbacks and an
   asset-incompatible fallback. Verify rejection before provider dispatch; verify
   an explicitly enabled, independent capable fallback retains provenance.
5. Supply malformed JSON, wrong field types, contradictory booleans, approval
   with blocking findings, prose contradicting fields and a self-retracted
   concrete defect. Follow with omission, unsupported retraction and an
   unresolved independent re-review. Verify none creates accepted evidence;
   bounded attempts stop and all previous findings/provenance remain available.
   Also verify a consistent verdict with evidenced finding dispositions can
   proceed only through the existing independent acceptance gates.
6. Compare synthetic request timings, queue intervals, failure counts and
   clarification attempts with existing usage output before and after restart.
   Verify request latency excludes queue time and aggregates count each attempt
   once, including rejected and failed attempts.

## Dependencies, exclusions and delivery

Coordinate persisted admission/retry state with stage capacity and supported
recovery controls. Reuse available results from the historical Flash/Qwen
benchmark, complexity routing, compact context, QA evidence reuse and finding
accountability work; confirm those artifacts during implementation planning.
Model benchmarking and routing changes are outside this ticket. Existing
commit-bound QA evidence may be reused only under its existing validity rules.

Implementation validation must run `python3 -m unittest discover -s tests -v`
and `python3 scripts/check_public_source.py`, plus manual disclosure review.
Delivery requires independent offline QA and different-model review of the exact
candidate commit, required GitHub checks and human CODEOWNER approval. Local
mocked regressions are gate tests, not independent acceptance. Workers cannot
merge, publish, deploy, change permissions or access production. Keep runtime
configuration, provider credentials and raw operational evidence outside Git.
