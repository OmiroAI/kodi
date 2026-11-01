# Operator reviewer routing

Routing is disabled unless private operator configuration sets
`review_routing_enabled` to the boolean `true`. No runtime configuration is
changed by this implementation. Disable that setting to restore fixed-provider
legacy behavior, including its existing Qwen quota fallback. Set the boolean
`review_routing_override` to force the policy's conservative `unknown` route.
These settings must never come from ticket content or a candidate checkout.

`review_routing_policy` contains an `id`, `routes`, optional `fallback`, and
`changes`. Routes must cover `docs`, `low_complexity`, `stateful`, `concurrent`,
`cross_component`, and `unknown`. Each route has exactly `provider` and `model`.
The supported pins are enumerated in `kodi/review_routing.py`; this list is a
transport admission boundary, not a claim of current provider availability.
Unavailable pins fail through the existing provider error path. Qwen requires
existing private transport configuration. Fallback is only benchmarked Qwen,
only on the existing explicit Antigravity quota trigger. Assets reject any
configured text-only route or fallback before invocation.

Classification uses SHA-256 of the entire cumulative committed patch, supplied
by the existing complete review material builder. `changes` maps exact patch
hashes to operator attestations with `class` and `reason`. Docs attestations
must explicitly set `stateful`, `concurrent`, `cross_component`, and `executable`
to false; low-complexity attestations require the first three and `ambiguous`
false. This intentionally requires operator structural inspection of each
eligible patch. Filenames, source text, author reports and ticket instructions
alone never establish eligibility. Missing attestations or risk flags use the
conservative route. Other structural classes retain their separate routes.

Activation also requires private `review_routing_evidence`: `id`, `operator`,
`historical_reference`, `approved: true`, `quality_passed: true`, and
`policy_digest` (the module's canonical JSON `digest(policy)`). Its `results`
list requires exactly one matching `route` and `class` for every route class,
an exact historical `candidate_sha`, `baseline`, comparable `context_digest`,
nonnegative integer `validated_findings`, `missed_findings`, and `failures`,
and integer or null `input_tokens` and `output_tokens`. Null means unmeasured.
Operator approval attests the measured results satisfy their quality criteria;
workers cannot approve or generate real benchmark evidence. Synthetic fixtures
only test validation. No live benchmarking is part of this change.

Selection evidence binds candidate SHA, patch digest, policy/evidence identities
and digests, class, reason, primary and fallback. Successful review records also
retain the actual model/provider and routing snapshot. Existing usage records
retain measured tokens and unknown counts. Bounded routing metrics record class,
provider, fallback, failures, approval and a capped finding count; they do not
infer missed findings or detection quality from runtime verdicts. Compare these
private records with historical benchmark results before considering rollout.
A different retained routing snapshot for the same candidate blocks, as does
in-flight policy/evidence mutation; preserve evidence and use inspected recovery
rather than silently replacing acceptance. Disable routing for rollback.

Manual verification in disposable state:

1. Leave routing disabled and run the existing reviewer fallback regressions.
2. Load a synthetic policy/evidence pair based on `tests/test_review_routing.py`;
   omit evidence and confirm rejection before any model call.
3. Attest an exact committed docs patch, inspect its routing evidence, then edit
   the patch or add cheap-route instructions and confirm conservative selection.
4. Force conservative override, then disable routing and confirm legacy selection.
   Change a retained candidate's policy and confirm it blocks instead of rerouting.
5. Simulate quota exhaustion with configured Qwen and inspect the identical full
   candidate scope, selected pins and SHA. Repeat with assets or an unknown model
   and confirm refusal. These mocks do not establish independent acceptance.

Independent exact-commit QA, different-model source review, required GitHub checks
and human approval remain separate gates. No services or live jobs are resumed.
