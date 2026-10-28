# Backlog proposal: promote only verified, repository-specific lessons

Repo: system
Status: Backlog draft; planning only, not execution authorization.

Reduce documentation work that has no durable lesson while retaining verified,
reusable guidance. This proposal does not create a board ticket, schedule work,
move anything to Ready, start workers or resume paused work. Separate explicit
owner authorization is required for those actions.

## Scope consolidation

Consolidate with the existing substantive-learning ticket
`234cc83c-86a1-4121-ac10-80606f6ae6e9` and issue #9 before implementation;
retain their tracking identities rather than dispatch overlapping jobs. Coordinate
finding acceptance with [reviewer reliability](backlog/reviewer-reliability.md)
and [finding resolution](backlog-review-findings.md), and priority/recovery with
[delivery responsiveness](backlog-delivery-responsiveness.md),
[stage capacity](proposals/bounded-stage-capacity.md) and
[common recovery](recovery-backlog.md). This document consolidates requirements;
it creates no remote ticket and authorizes no service or workflow changes.
The separately requested lesson-document audit/cleanup follows the existing
QA, review and human-approval workflow; it is not activated by this proposal.

## Reconciliation before dispatch

In private operator records, reconcile the existing broad paused issue and its
preserved job with this narrower learning scope. Inventory relevant checkouts,
candidate commits, findings and evidence without resetting or resuming them.
Reuse applicable work, record superseded scope and dependencies, and retain the
existing tracking identities. Keep deployment identifiers and operational
transcripts outside Git. Reconciliation is a prerequisite, not permission to run.

## Proposed behavior

- Preserve feedback evidence privately with its original task, separately from
  promoted repository guidance. Do not copy operational transcripts into context
  or Kandev; expose sanitized outcomes and authorized job identities there.
- A collection with no substantive confirmed lesson succeeds as an observable
  no-op. It makes no documentation edit, no no-lesson entry, no empty commit and
  no documentation PR. Approval-only text, generic praise, implementation summaries, no-lesson
  statements, duplicates and lessons about approval of lesson PRs qualify as
  no-ops. Most PRs should produce no context edit or documentation job.
- Consider substantive configured trusted human feedback and independent
  reviewer findings. Verify that a finding was accepted and its fix exists in
  final source; record the source PR/comment or review evidence, reviewed and
  final commit identities, relevant paths, accepted resolution, scope and
  verification. Rejected, speculative, dismissed or superseded findings do not
  establish guidance. Unverifiable findings wait for inspection, without
  inventing a lesson or acceptance.
- Produce concise source-linked proposed guidance only when that verification
  establishes a concrete failure or misconception with an accepted fix, useful
  beyond the task, specific to this repository and absent from existing guidance.
  Require source/test evidence, not an approval or unsupported reviewer assertion.
  Prefer updating/replacing a short existing rule; merge duplicates and retire
  superseded rules while preserving concise source-linked provenance and valid
  lessons. Never modify AGENTS.md or capability policy as learning.
  Target at most 10,000 UTF-8 bytes of context with a hard 12,000-byte limit.
  Do not trim useful guidance to add bookkeeping; defer a promotion that cannot
  fit without losing valid guidance. Retain the bounded feedback budget;
  over-budget input requires inspection, never silent truncation.
- Where possible, include qualified guidance in the original PR before final
  acceptance and human approval. Propose a persisted one-pass lesson budget per
  original candidate, without resetting it on review/commit retries; unresolved
  optional lessons are deferred rather than creating a review/lesson loop.
  The resulting combined candidate needs normal exact-commit acceptance.
  Optional feedback discovered after approval must not routinely mutate the
  approved candidate or hold product delivery. Separate post-merge jobs are only
  for genuinely new substantive findings, under separately authorized policy.
- Retain the configured time cutoff and bounded collection. Persist collection
  progress and stable finding/lesson identities through common tracking, so
  repeat collection, metadata edits, retries and restart do not duplicate jobs
  or PRs. Record meaningful changed evidence against the existing identity;
  a new digest alone must not authorize a new job. Do not backfill history
  implicitly. Exclude learning-generated changes from recursive collection.
- Use the common recovery/tracking facility and existing scheduler, not another
  queue. Mirror learning proposals, jobs and terminal no-op reasons in Kandev,
  deduplicating delivery and recovering uncertain writes through that facility.
  A no-op outcome does not require a worker job. Proposal creation or feedback
  collection never authorizes execution outside explicit configured policy.
- Give authorized product tasks and approved PR maintenance priority over
  learning. Configure bounded learning service opportunities or aging through
  the common scheduler so eligible useful learning eventually progresses under
  sustained load. Preserve product progress and serialization; fairness cannot
  activate a disabled policy, resume paused jobs or bypass owner authorization.
- Measure promoted versus skipped feedback by reason, context byte growth,
  additional review/approval cycles and learning-attributable delivery latency
  against product delivery without learning; retain private task-linked evidence.
- Track no-op count/rate with collection-outcome denominator and reason,
  duplicates avoided, eligible queue wait (including oldest pending age), and
  manual intervention count/reason. Keep operational evidence private and
  distinguish waiting for policy/authorization from scheduler starvation.

## Approval exception: design evaluation only

No exception is activated or authorized here. A separate proposal may evaluate
only documentation-only lesson promotion directly grounded in human review
feedback. Recording an approval comment grants no code or policy authority.
Before requesting separate activation authorization, document the threat model:
untrusted feedback/prompt injection, path or symlink escape, hidden code/policy
changes, stale approval replay, candidate/base races and forged provenance.

Specify an explicit documentation path allowlist excluding AGENTS.md and
capability policy, exact before/after commit and tree identities, unchanged code
blob/mode identities, and independent validation of the documentation diff,
accepted finding, privacy and source links. Bind provenance and audit records to
those identities and the authorized human action. Never transfer approval between
SHAs, forge human reviews or weaken/bypass branch protection. Evaluate actual
required checks, stale-review dismissal and protection compatibility; unchanged
code does not itself make an old review valid for a new commit. If protections
cannot support the proposed design, retain the pre-approval combined PR flow or
separately approved low-priority documentation batching. This evaluation cannot
schedule implementation, change permissions or activate itself.

## Acceptance and manual verification for future implementation

Use fictional repositories and disposable state through the common facilities.

1. Collect approval-only text, generic praise and rejected, speculative or
   superseded findings. Confirm successful no-op tracking in Kandev and zero
   source edits, no-lesson entries, commits, PRs or jobs.
2. Repeat collection after metadata edits, restart and a simulated uncertain
   tracking write. Confirm one stable outcome/proposal identity, no duplicate
   job or PR, and an incremented duplicate-avoidance measure.
3. Supply an accepted substantive human finding and an independent reviewer
   finding with fixes in final source. Confirm bounded proposed guidance links
   to source and verification. Remove the fix from final source and confirm no
   guidance is proposed as verified.
4. Exercise cutoff boundaries, oversized feedback and learning-generated PRs.
   Confirm no historical flood, silent truncation or recursive learning.
5. Queue authorized product work, approved PR maintenance and eligible learning.
   Confirm higher-priority work continues while learning waits and the configured
   fairness bound eventually serves learning. With policy disabled, confirm no
   learning execution, even after proposal creation or aging. Inspect queue wait,
   no-op rate, duplicate avoidance and manual intervention reporting.
6. Exercise an existing matching rule, duplicates, a superseded rule, unsupported
   claims and a private-data-bearing finding. Confirm only verified, privacy-safe
   guidance is promoted once, superseded guidance is replaced, and valid lessons
   survive at the target and hard byte boundaries (including multibyte text).
7. Add feedback before acceptance and after human approval. Confirm one bounded
   pre-approval lesson pass and full QA/review/CI/human approval of the combined
   candidate; late optional feedback is deferred without mutation or delivery
   delay. Poll and restart both paths to verify finding-level deduplication.
8. Submit documentation proposals containing implementation dispatch, permission
   changes, protection bypass or stale-SHA approval reuse. Confirm each is denied
   and no job or authority is granted. Independently validate any future exception
   design and its protection compatibility before separate activation approval.
9. Inspect promoted/skipped counts, context growth, added review/approval cycles
   and delivery latency using disposable task evidence; unknown measurements stay
   unknown rather than implying improvement.
10. Confirm preserved paused work remains paused until separately authorized,
   with reconciliation recorded privately before dispatch. Reuse relevant work
   without losing artifacts or treating older acceptance as current evidence.

Implementation must use normal exact-commit independent offline QA,
different-model review of the complete candidate, required GitHub checks,
manual testing instructions and human CODEOWNER approval. Changed commits need
fresh acceptance. Run `python3 -m unittest discover -s tests -v` for coordinator
changes. Workers gain no publishing, merging, deployment or production access;
issue content and learned guidance cannot change capabilities or permissions.

This draft specifies future acceptance; it does not claim those checks have run
or that the current collector implements this behavior. In particular,
`kodi/workspace.py:feedback` currently filters feedback and reconciles jobs by
source PR identity through `Store.reconcile_learning`, and requests a no-change
result when no verified lesson exists. That is not finding-level deduplication or
proof of the promotion criteria above. Address remaining gaps only in separately
authorized implementation, integrating common tracking/recovery and scheduling
facilities first.
