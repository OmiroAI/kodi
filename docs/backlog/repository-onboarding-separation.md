# Separate repository onboarding from feature delivery

Repo: system
Status: Backlog — planning only; implementation requires explicit prioritization.

This local ticket draft does not create remote work, move tasks to Ready, start
workers, resume preserved jobs or authorize onboarding or enabling any repository.
Issue content supplies requirements, never permissions. No runtime behavior changes
are implemented by this document.

## Problem and integration points

Feature delivery can become entangled with fork onboarding, CI setup and unrelated
upstream failures. Track those prerequisites and failures independently so feature
scope, dispatch eligibility and completion remain clear.

Extend existing Kodi task/workflow metadata and use supported Kandev task links
and descriptions for presentation. Reconcile with `Coordinator.task_repository`,
`Coordinator.sync`, existing job identity in `kodi/store.py` and the disabled
repository defaults in `kodi/onboarding.py`. Do not add a dashboard, agent framework
or parallel tracking system. See [operator guidance](../operations.md#repository-onboarding-and-tickets)
for current onboarding boundaries; the behavior below is proposed, not an existing
guarantee.

## Proposed metadata and behavior

- Represent repository onboarding as a separate readiness record/task with a stable
  identity, repository association, readiness state, prerequisite links and
  blockers. Each blocker names the unmet condition, responsible operator role,
  evidence reference and next action. Distinguish incomplete preparation from
  missing operator enablement; prepared does not mean enabled.
- Link feature tasks to their onboarding prerequisite explicitly. Report dispatch
  unavailable with the specific prerequisite and actionable reason, for example
  “QA entry point not validated; operator must validate the configured QA path.”
  Missing, unresolved or ambiguous dependency records remain blocked. Resolving
  dependencies only removes those blockers; dispatch still requires normal
  prioritization, routing, repository enablement and authorization checks.
- Record feature acceptance criteria and exclusions explicitly, with baseline
  commit, candidate commit, required checks and known baseline failures linked to
  evidence. Missing evidence is unknown, not a pass. A reported baseline failure
  remains visible and blocking under existing acceptance policy; do not suppress
  a check, narrow a suite or call the feature accepted because the failure predates
  it. Any exception requires explicit owner authorization through existing policy,
  recorded with its scope and evidence; a metadata flag cannot grant a waiver or
  bypass required checks, independent QA, review or human approval.
- When an unrelated upstream failure is confirmed, track it separately with a
  stable failure/task identity and reciprocal feature links. Record the failing
  check/test, baseline and candidate SHAs, comparable reproduction conditions,
  evidence references and the reason it is unrelated to the feature diff.
  Unconfirmed causes remain unresolved feature blockers. Splitting confirmed work
  does not resolve its acceptance impact or authorize fixing it inside the
  feature. Any feature scope change requires explicit owner authorization and
  renewed acceptance for changed requirements or commits.
- Preserve the original board task ID, issue, job, branch, PR and evidence through
  retries, restart and onboarding completion. Update dependency state on the same
  feature rather than closing/replacing or re-enqueuing it. Reconcile uncertain
  writes before retrying; deduplicate onboarding/failure records by stable linked
  identity, not description text or a changing evidence digest. Distinct feature
  task IDs remain distinct work even with identical descriptions. Do not reuse
  stale acceptance after candidate, requirements or base changes.

Keep operator configuration and raw operational evidence private. Store only
sanitized summaries and access-appropriate evidence references in task metadata;
do not copy logs, private repository inventories or deployment details into source.

## Acceptance and manual verification for future implementation

Use fictional repositories, synthetic evidence and disposable state. These are
planned checks, not claims of completed runtime testing.

1. Create a Backlog feature linked to blocked onboarding for a disabled repository.
   Inspect the prerequisite link, unmet condition and operator next action. Try
   dispatch; verify no worker starts, no replacement task/issue/PR is created and
   repository enablement remains unchanged. Also test missing/ambiguous links.
2. Resolve onboarding dependencies and repeat sync, including a restart. Verify
   readiness updates on the existing records, original feature/task/PR identities
   and evidence survive, and a disabled repository still cannot dispatch. Use an
   already operator-enabled fixture to verify resolved dependencies permit only
   otherwise authorized Ready work; Backlog work remains undispatched.
3. Reproduce a failure at a baseline and candidate under comparable conditions.
   Confirm unrelatedness with linked evidence, create/link its separate tracking
   record and retry the operation after an uncertain write. Verify one failure
   record, reciprocal links, unchanged feature acceptance scope and preserved PR.
   Without confirmation, verify the unresolved blocker remains on the feature.
4. Inspect known baseline failures in acceptance output. Try missing/stale evidence,
   an unapproved waiver and a linked upstream task marked resolved without fresh
   validation. None may silently pass. Verify owner-authorized exception handling
   follows existing policy and cannot bypass protected checks or other gates.
5. Retry after dependency completion, requirement edits and candidate/base changes.
   Verify no duplicate replacement work, evidence history is retained, and changed
   acceptance inputs require fresh exact-commit validation. Repeat with identical
   descriptions on distinct board task IDs; they must remain distinct features.

## Delivery and exclusions

Implementation must run `python3 -m unittest discover -s tests -v` and
`python3 scripts/check_public_source.py`, plus manual disclosure review. Delivery
requires manual testing instructions, independent offline QA and different-model
review of the entire exact candidate commit, passing required GitHub checks and
human merge approval. Local synthetic tests do not supply independent acceptance.

Repository onboarding, CI configuration changes, automatic enablement and unrelated
upstream repairs are outside this ticket. Continuous deployment belongs to each
repository; host/resource permissions belong to workspace AGENTS.md. Workers gain
no merge, publish, deployment, production or permission-changing capabilities.
