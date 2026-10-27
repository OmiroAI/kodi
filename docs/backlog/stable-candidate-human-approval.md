# Request human approval only after the candidate is stable

Repo: system
Status: Backlog — planning only; not scheduled or Ready.

This ticket records requirements only. Implementation waits for explicit owner
prioritization. It does not authorize dispatch, publication, merge or deployment.

## Problem and scope

Requesting human review before code and verification settle causes repeated
requests when repairs invalidate exact-commit approval. Gate human-review
transitions and notifications on a stable, verified candidate, using the existing
deterministic coordinator and Kandev status surfaces.

Repository-defined continuous deployment remains independent. Host/resource
permission rules, workflow permissions, authentication and deployment
configuration are outside this ticket. Coordinate delivery ownership with
[approval-wait reservations](approval-wait-reservations.md); waiting for approval
and owning a merge/update reservation are separate concerns.

## Requirements for a separately authorized implementation

- Request human approval only after independent QA, different-model review of
  the entire candidate and every required CI gate have succeeded for the current
  candidate commit. Missing, pending, failed, stale or mismatched evidence must
  block the transition, regardless of gate completion order. Preserve manual
  testing instructions as a ready-PR requirement.
- Persist notification identity by task, PR and candidate SHA. Repeated ticks,
  restarts and delivery retries must not duplicate approval notifications for
  that identity. Reconcile uncertain delivery before repeating an external
  request; respect existing current-commit approvals and pending requests.
- On head change, show the previous and current candidate, a concise description
  of the changed code and which QA, review, CI evidence and human approvals
  became stale. Preserve historical evidence without treating it as current.
  Issue a new approval request only after the replacement candidate passes all
  prerequisite gates. Never transfer an older approval to changed code.
- Immediately before requesting approval, recheck candidate identity and gate
  evidence so observations for an earlier head cannot produce a ready request.
  Expose the actual blocking gate in existing status surfaces. Unavailable QA,
  reviewer or CI infrastructure is a blocked verification state, not a candidate
  ready for human approval; missing observations cannot imply success.
- Before merge, revalidate the PR, recorded candidate, remote branch/head, clean
  coordinator checkout, current base, required checks and current exact-commit
  human approval. A changed base requires fresh validation and, when acceptance
  is invalidated, fresh independent QA, different-model review and human approval.
  Head drift invalidates acceptance even if the PR number remains unchanged.
- Preserve serialized updates/merges and branch protection. Only the
  operator-enabled deterministic coordinator may request protected auto-merge
  after all gates pass. Workers cannot approve, merge, publish, deploy, change
  permissions or grant access. No gate may bypass protection or self-approve.

## Regression and manual verification plan

Use disposable tasks and fictional repositories such as `ExampleOrg/project`.
Keep runtime configuration, credentials and operational evidence outside Git.

1. Complete QA, different-model review and required CI in different orders.
   Advance the coordinator after each completion. Verify no approval request
   before the last successful current-commit gate, then exactly one request.
   Repeat with each gate missing, failed, pending and bound to another SHA.
2. Repeat ticks and restart around ready-state persistence and each notification
   delivery boundary, including uncertain delivery. Verify one logical request
   per task/PR/head, reconciliation of uncertain writes, and respect for existing
   pending requests and valid current-head approval. Distinct task/PR identities
   must not accidentally suppress one another.
3. Change the head while gates are completing, immediately before notification,
   and after an approval request. Deliver old gate results out of order. Verify
   old results never ready the new candidate, status identifies changed code and
   stale evidence/approvals, and a replacement request waits for fresh successful
   gates. Repeat with an old approval arriving after the new head is recorded.
4. Exercise dismissed and older-commit approvals, plus reuse of the same PR
   number for a replacement candidate. Verify no old approval authorizes changed
   code. Revalidate after base advancement and remote-head/checkout drift;
   invalidated acceptance must block merge until refreshed, without bypassing
   protected checks or human approval.
5. Simulate unavailable QA transport, reviewer capacity and CI inspection.
   Verify status names the actual blocked gate and available failure detail,
   sends no premature approval request and does not describe missing evidence as
   a pass. Restore infrastructure and finish gates; verify normal deduplicated
   approval delivery resumes.
6. For the authorized implementation, run
   `python3 -m unittest discover -s tests -v` and
   `python3 scripts/check_public_source.py`, then inspect the full diff for
   disclosures. Record independent offline QA, different-model review of the
   entire candidate, required GitHub checks and human approval for the exact
   candidate commit before acceptance.

These scenarios are pending verification requirements, not completed acceptance
evidence. Local mocked regressions establish gating behavior only; they do not
replace independent QA, independent review, CI or human approval. This planning
document changes no runtime behavior or repository-defined CD.
