# Compact review context

Status: Backlog. Implementation waits for the owner to move this task to Ready.
Repository catalog key: `system`.

## Proposed change

For tool-enabled Antigravity reviews, supply the complete candidate diff against
the verified review base, all acceptance requirements and relevant author
evidence. Replace duplicated full changed-file text with targeted read-only
access to the same verified checkout, including unchanged surrounding code and
relevant callers. Include every candidate commit, including earlier repairs;
exclude changes belonging only to the base. Never sample or silently truncate
the diff. Retain fail-closed review material budgets.

Preserve the richer full-file context for tool-less Qwen unless an alternative
has independent validation. Assemble and budget sufficient Qwen context for
quota fallback independently of the compact primary prompt. A compact primary
prompt must not become the text-only fallback's source context.

Build on the existing binary/non-UTF-8 review-diff work (PR #14), rather than
implementing another asset representation. Preserve committed-blob identities,
both-revision inspection, unsupported-format blocking and the prohibition on
text-only asset reviews or asset quota fallback. See
[asset review preparation](operations.md#asset-review-preparation).

## Acceptance and validation after Ready

- Add regression coverage for complete-branch scope across multiple repair
  commits, correct base exclusion and complete untruncated patches.
- Verify read-only access to callers and unchanged surrounding code in the same
  checkout at the exact candidate commit; reject checkout identity drift.
- Verify standalone Qwen and simulated Antigravity quota fallback receive full
  requirements, author evidence, diff and richer source context. Exercise budget
  failures and retain existing asset fallback rejection coverage.
- Record assembled prompt character sizes separately for primary and fallback.
  On representative disposable candidates, measure provider-reported tokens and
  tool-read counts/volume, including source returned by tools. Compare the same
  candidate and base with the existing richer prompt. Keep unavailable token
  counts unknown; character reduction alone is not measured token reduction.
  Keep operational evidence and private source outside the public repository.
- Run `python3 -m unittest discover -s tests -v` for implementation changes and
  `python3 scripts/check_public_source.py`; review the diff for disclosures.

Manual testing: use a disposable multi-commit candidate with an earlier repair,
an unchanged caller and a separate base-only change. Confirm the complete branch
diff, inspect the caller through read-only tools, simulate quota exhaustion and
verify the richer Qwen fallback context. Repeat with an over-budget candidate
and an asset change to confirm existing fail-closed behavior.

Keep native Kandev agent/session startup disabled. Implementation acceptance
still requires exact-commit independent offline QA, different-model review,
required GitHub checks and human approval. This backlog entry neither supplies
those records nor authorizes dispatch, publication, merge or deployment.
