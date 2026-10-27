# Classify delivery failures before retrying

Repo: system
Status: Backlog — planning only; not scheduled or Ready.

This local ticket records requirements, not authorization to implement, dispatch
workers or resume jobs. Keep it in Backlog until explicitly prioritized. No
runtime behavior changes or completed acceptance are claimed here.

## Problem and scope

Infrastructure and service availability failures can consume repeated coding,
independent QA and review cycles as though they were code defects. Classify the
failure before selecting recovery so an unchanged environment does not trigger
another expensive attempt.

Extend the deterministic coordinator in `kodi/coordinator.py`, existing retry
handling in `kodi/recovery.py` and private persistence in `kodi/store.py`. Carry
structured failure evidence from QA/review adapters in `kodi/runner.py` and CI
inspection in `kodi/github.py`. Present category, reason and next action through
existing Kandev/reporting surfaces. Coordinate reviewer-specific behavior with
[reviewer reliability](reviewer-reliability.md); that proposal is not implemented
behavior or a dependency on a new agent framework.

Repository check selection and QA resource configuration are separate tickets.
Repository CD and workspace host/resource permissions are outside this scope.
Classification must not change resource limits, credentials, permissions or
deployment configuration.

## Required classification and recovery

Use structured adapter signals where available. Exit codes or ambiguous message
fragments alone must not turn unknown failures into code defects or transient
failures. Record secondary evidence when failures overlap; select recovery
conservatively when the cause cannot be established.

| Category | Representative evidence | Next action and retry policy |
| --- | --- | --- |
| Code/test failure | Completed test run with assertion/compiler diagnostic, or validated blocking review finding | Return the diagnostic for bounded code repair under the existing repair budget. Require a changed candidate before repeating the failed gate; stop when the repair budget is exhausted. |
| Resource exhaustion | Explicit OOM/container resource event or structured resource-limit report | Block the affected gate. Require an operator-confirmed resource/availability change or a relevant candidate change before one new attempt. A killed process alone is insufficient proof of OOM. |
| Timeout | Recorded deadline, elapsed time and affected operation | A positively identified temporary transport timeout may use bounded backoff. A repeated execution deadline requires a relevant candidate, configuration or availability change; do not automatically send it to coding. |
| CI disabled/unavailable | Required check absent/pending with service evidence, or explicit disabled/service-unavailable response | Keep CI pending/failed. Permit bounded read-only polling for temporary service recovery; disabled CI requires operator restoration. Missing checks alone do not prove CI is disabled or passing. Never rerun coding/QA to repair missing CI. |
| Reviewer availability | Explicit temporary provider outage/throttling, exhausted quota or unsupported/unavailable model/tool capability | Back off only for positively temporary availability failures. Quota/capability/configuration failures require confirmed recovery or a relevant operator input change. Do not generate a replacement verdict or enable a paid fallback. |
| Authentication/configuration | Explicit authentication/permission rejection, invalid configuration or missing required dependency/profile | Block and request the specific operator correction without exposing credentials. Retry only after a relevant configuration/access change is confirmed; do not grant access or rewrite code to bypass it. |
| Unknown | Unrecognized, conflicting or insufficient evidence | Preserve evidence, block and request inspection. No automatic expensive retry until investigation records a relevant input/change or establishes a supported transient classification. |

Persist each failed attempt's stage, exact candidate/base identity, category,
structured reason, private evidence references, retry count, next eligible time
and required recovery action. Preserve original adapter output, exit/signal,
deadline and provider/check identity in private evidence under existing secret
handling; do not copy raw logs, credentials, endpoints or operational transcripts
into Git or public reports. Summaries must be sanitized and link only to
authorized private evidence. Classification must not overwrite prior evidence.

For eligible transient failures, propose at most two automatic retries after the
initial attempt, with persisted backoff of 30 then 120 seconds. Honor a longer
valid service retry deadline; if it exceeds a 15-minute recovery window, defer
for inspection rather than retry early. Record the actual service deadline only
when supplied; do not invent provider reset times. Exhaustion blocks until a
relevant recovery change is recorded.

Persist retry reservations before dispatch using existing serialized coordinator
and recovery patterns. Key budgets to job, stage, candidate and relevant recovery
inputs; restart, repeated ticks, reclassification and repeated operator retry
requests must not reset them. Compare relevant inputs, not timestamps or unrelated
config edits. Record a recovery generation with its changed inputs or explicit
availability evidence; each qualifying recovery permits one new attempt and a
fresh bounded transient budget, not unconditional retries on every tick. A new
candidate invalidates acceptance as required by existing exact-commit gates and
must not reset the overall code-repair budget. Interrupted or uncertain remote
writes require reconciliation before any repeat, even if transport is transient.

A failed/unavailable gate stays failed/pending. Classification is diagnostic
metadata, never acceptance, a waiver, a model verdict or authority to change
fallback policy. Preserve independent exact-commit QA, complete-candidate
different-model review, required GitHub checks and human approval. Do not rerun
unrelated successful stages solely because a service is unavailable; reuse only
evidence already valid under existing candidate/base rules.

## Acceptance and manual verification plan

Use fictional fixtures, disposable state, synthetic adapter responses and a
controllable clock; no live services or credentials are needed.

1. Inject an assertion failure, explicit OOM, ambiguous process kill, execution
   timeout, temporary transport timeout, missing/disabled CI, CI outage, reviewer
   outage/quota exhaustion, authentication/configuration error and unknown error.
   Verify the category, original private evidence reference and actionable next
   step. Ambiguous signals must remain unknown. Confirm public summaries redact
   synthetic secret-like sentinels.
2. Advance repeated ticks and restart after failure, reservation and backoff.
   Verify transient attempts occur only at eligible times, stop after two retries
   and retain their budget. Test valid, invalid and over-window service deadlines;
   verify no early request, invented reset or blind replay of uncertain writes.
3. Repeatedly request retry for an unchanged persistent failure, including after
   restart and an unrelated config edit. Assert zero further expensive calls and
   no additional coding/repair budget consumption for infrastructure failures.
4. Restore simulated CI/reviewer availability, correct the relevant private
   configuration or supply a relevant changed candidate. Record recovery evidence
   and verify one new attempt becomes eligible. Repeat ticks with the same
   recovery evidence and a persistent result; assert no loop or budget reset.
5. Verify failed/unavailable gates cannot become ready/accepted, trigger paid
   fallback or waive checks. A restored service must actually complete its gate;
   changed commits require fresh exact-commit acceptance. Verify code repair stays
   bounded and unrelated valid evidence remains governed by existing rules.

For an authorized implementation, run
`python3 -m unittest discover -s tests -v` and
`python3 scripts/check_public_source.py`, and manually inspect the complete diff
for disclosures. These scenarios are a future verification plan, not evidence
that runtime classification or retries have been implemented.

Delivery acceptance remains pending exact-commit independent offline QA,
different-model review, required GitHub checks, manual testing instructions and
human approval. Workers cannot merge, publish, deploy, access production, change
workflow permissions or grant themselves access.
