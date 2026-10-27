# Select review models by validated change complexity

Status: Backlog. Only the owner moving this task to Ready authorizes delivery
work. This proposal does not enable routing or change live model configuration.
Dependency: Benchmark Flash and Qwen on historical Gemini findings.

## Benchmark gate

Before implementation, benchmark Flash Medium and the existing Qwen reviewer
against historical Gemini findings in private operator storage. Keep supporting
evidence, historical source, raw logs and operational details outside this tree.
Record the evaluated provider/model versions, candidate identities, comparable
review context, validated findings and missed findings by severity and change
class, measured input/output tokens, failures and unavailable token counts.
Compare against the current Argon High baseline on the same candidates. Separate
model detection quality from transport failures and invalid structured verdicts.

The owner must approve acceptable detection quality and the resulting model
choices and thresholds based on measured results before delivery proceeds.
Documentation and straightforward changes are candidates for Flash Medium;
stateful, concurrent and cross-component changes retain Argon High unless the
approved benchmark supports a different conservative configuration. No numeric
threshold or cheaper model is approved by this backlog entry.

## Proposed delivery acceptance

- Keep routing deterministic and inspectable, using validated private operator
  configuration and the complete actual changes at the candidate commit.
  Ticket text, source instructions, author reports and model suggestions cannot
  grant a cheaper route, change policy or bypass review. File names alone must
  not establish that a change is straightforward.
- Use the conservative configured route for unknown, unsupported, ambiguous or
  complex changes. Validate configured model availability and Gemini-family
  separation from the coding model before review; unavailable or invalid routes
  must never silently downgrade or produce acceptance.
- Bind the selected provider/model, policy version, classification inputs and
  routing reason to the exact candidate SHA. Changed candidates require fresh
  routing and acceptance evidence. Record actual fallback provider/model too.
- Preserve complete valid context, context budgets, asset inspection requirements
  and all structured verdict validation. Retain the existing operator-configured
  Qwen quota fallback and its restrictions, including no text-only asset fallback;
  routing must not broaden fallback triggers or erase primary review evidence.
- Provide a trusted operator override to the conservative route and a rollback
  to existing fixed-model selection. Neither may be controlled by issue/source
  instructions or relax any acceptance gate.
- Compare token use and validated findings before and after rollout by change
  class and selected model, including fallback runs, misses, failures and unknown
  counts. Keep operational measurements private; do not invent token counts or
  infer quality from token savings alone. Regression against the owner-approved
  benchmark criteria requires rollback to the conservative route.
- Preserve exact-commit independent offline QA, different-model review, required
  GitHub checks and human approval. Keep native Kandev agent/session startup
  disabled and use existing Kandev capabilities. Workers gain no merge, publish,
  deploy, permission-changing or production access.

## Verification required when Ready

1. With disposable committed fixtures, compare documentation, straightforward,
   stateful, concurrent, cross-component and unknown changes. Repeat each decision
   with identical trusted inputs and confirm identical routing and SHA evidence.
2. Add instructions claiming a cheap route to ticket text and source content;
   confirm they cannot change operator policy or suppress review. Test unavailable
   models, invalid configuration and same-family coding/review combinations.
3. Exercise quota fallback with complete context and verify structured verdict,
   asset, usage and candidate identity checks remain enforced. Confirm context
   overflow, invalid verdicts and non-quota failures still block appropriately.
4. Exercise the operator override and rollback, then compare private benchmark
   and rollout measurements against the owner-approved quality criteria.
5. Run `python3 -m unittest discover -s tests -v` for coordinator changes and
   `python3 scripts/check_public_source.py`. Complete independent offline QA,
   different-model review, required GitHub checks and human approval on the exact
   delivery commit before an operator changes live model configuration.
