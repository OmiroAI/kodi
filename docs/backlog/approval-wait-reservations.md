# Release safe merge reservations while awaiting approval

Repo: system
Status: Backlog — planning only; not scheduled or Ready.

This ticket records requirements only. It does not authorize implementation,
moving work to Ready, starting workers or modifying/releasing a live reservation.

## Problem and scope

A nonterminal job with historical `merge_maintenance` evidence can be selected
as the exclusive merge candidate. When its current candidate needs renewed human
approval, `maintain_merges` returns without considering unrelated approved PRs.
An approval-waiting documentation job can therefore stall the global queue even
when no remote merge is armed.

This ticket isolates that approval-wait reservation regression. Coordinate with
[Keep Kodi responsive and limit delivery lock scope](../backlog-delivery-responsiveness.md)
and the [bounded stage capacity proposal](../proposals/bounded-stage-capacity.md)
to avoid duplicate ownership fixes. Broader worker concurrency and delivery-lock
scope changes remain separate proposals.

## Requirements for a separately authorized implementation

- Distinguish historical maintenance evidence from active delivery ownership.
  Waiting for human approval alone must not reserve the global merge/update slot.
- Before releasing ownership, reconcile the PR, remote branch/head, persisted
  mutation intent and remote auto-merge state. Uncertain publication, interrupted
  branch updates and armed remote merges retain ownership until safely reconciled.
  Missing or uncertain observations must not be treated as proof of safety.
- Leave the original job and PR awaiting approval. Preserve candidate identity,
  evidence, attempts and recovery budgets. Do not approve, close, cancel or
  duplicate work to release capacity.
- After safe release, consider unrelated eligible approved PRs through normal
  serialized maintenance. Retain exact-SHA independent QA and different-model
  review, required CI, current-base revalidation, stale-approval dismissal and
  human CODEOWNER approval. Only the operator-enabled deterministic coordinator
  may request protected auto-merge after these gates pass.
- Let the original job re-enter normal eligibility when approval arrives, with
  fresh validation whenever candidate or base changes invalidate acceptance.
- Expose the actual wait reason and delivery ownership separately in existing
  Kandev/status surfaces, including approval waits that still own delivery because
  remote mutation reconciliation is pending. Do not add a dashboard or framework.

## Regression and manual verification plan

Use disposable tasks and fictional repositories such as `ExampleOrg/project`;
keep runtime configuration, credentials and operational evidence outside Git.

1. Seed an older documentation job with historical `merge_maintenance` evidence,
   a matching published candidate, no unresolved mutation intent, no armed
   auto-merge and missing current approval. Queue an unrelated approved PR with
   passing gates. Advance maintenance and verify the latter progresses through
   serialized maintenance while the former remains awaiting approval with its
   candidate, evidence, attempts and budgets intact.
2. Repeat with dismissed and stale approvals, remote head drift and base changes.
   Verify historical evidence never confers permanent ownership; invalidated
   acceptance requires fresh validation and stale approvals cannot authorize a
   mutation. Inspect Kandev/status for the actual wait reason and ownership.
3. Interrupt publication and branch updates around persisted intent, remote write
   and response recording. Also arm a remote protected merge. Verify ownership
   remains until remote identity and outcome are reconciled; a second job cannot
   race a mutation. Repeat reconciliation and verify no duplicate remote write.
4. Restart and race concurrent maintenance ticks in each scenario. Verify durable
   ownership, serialized mutations, no duplicate PR, no budget reset and no lost
   evidence. Add later human approval to the original job and verify normal
   eligibility resumes with current-head/base validation and all required gates.
5. For an authorized implementation, run
   `python3 -m unittest discover -s tests -v` and
   `python3 scripts/check_public_source.py`; review the full diff for disclosures.
   Record exact-commit independent offline QA, different-model review of the
   entire candidate, required GitHub checks and human CODEOWNER approval.

These scenarios are pending acceptance requirements, not completed evidence.
Local mocked regressions cannot substitute for independent acceptance. This
planning change neither operates the live queue nor authorizes publication,
merge, deployment, permission changes or access to production.
