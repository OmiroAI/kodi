# Review finding resolution

Review evidence is an append-only ledger in the existing private state database.
Finding IDs combine the original review record ID and finding index. Reviews retain
candidate, requirements snapshot and digest, provider/model and original structured
response. Later reviews cannot erase earlier findings. Existing legacy records
without requirement bindings fail closed and require fresh validation.

Reconsideration supplies finding lineage separately from independent QA facts,
without a previous verdict summary. The reviewer must inspect the complete candidate
and return explicit `dispositions` entries containing `finding_id`, `status`,
`candidate_sha`, `requirements_identity`, `source_evidence` and `test_evidence`.
A resolving reviewer must differ from the author family and original reviewer
model. Both the original and resolving reviews must name nonblank reviewer family,
provider and model identities; the resolving review must also name its author
family. Unknown legacy identities cannot establish independence.
Missing reviewer identities, evidence, conflicting entries, confirmed defects
and unresolved disputes block acceptance. A repair requires a new SHA. Stale metadata
can resolve only findings originally classified with `category: metadata`.
Source/test claims require independent source review; strings alone are not proof
of technical correctness. Exact-candidate independent QA remains a separate gate.

Three finding reconsideration invocations are reserved persistently per job,
including attempts that fail. Restart, candidate changes and requirement edits do
not replenish this budget. Findings disable automatic quota fallback. Exhaustion
requires operator inspection; it does not authorize rerouting or resuming jobs.

Manual verification in disposable private state:

1. Record a rejecting review and approving review for the same SHA. Confirm
   `Store.gate` remains false and both original records remain inspectable.
2. Change routing metadata and requirements; confirm unresolved source findings
   persist and old acceptance fails. Inspect `finding_blockers` for finding IDs.
3. Independently inspect a dependency declaration and execute its regression.
   Have a different reviewer model record an evidenced false-positive disposition;
   verify the exact-SHA gate still requires QA and author manual test steps.
4. Repair a defect in a new candidate. Obtain complete-candidate independent QA
   and different-model review with an evidenced repair disposition and prior ID.
   Confirm failed QA or stale requirements block the gate.
5. Omit or retract the finding, attempt author/original-model resolution, and
   exhaust reconsiderations. Reopen the database and confirm blocking persists.

Local mocked regressions are not independent acceptance. Required CI and human
approval remain downstream gates; no publication or live operation is enabled here.
