# Backlog proposal: substantive, deduplicated, low-priority learning

Repo: system
Status: Backlog draft; planning only, not execution authorization.

Reduce documentation work that has no durable lesson while retaining verified,
reusable guidance. This proposal does not create a board ticket, schedule work,
move anything to Ready, start workers or resume paused work. Separate explicit
owner authorization is required for those actions.

## Reconciliation before dispatch

In private operator records, reconcile the existing broad paused issue and its
preserved job with this narrower learning scope. Inventory relevant checkouts,
candidate commits, findings and evidence without resetting or resuming them.
Reuse applicable work, record superseded scope and dependencies, and retain the
existing tracking identities. Keep deployment identifiers and operational
transcripts outside Git. Reconciliation is a prerequisite, not permission to run.

## Proposed behavior

- A collection with no substantive confirmed lesson succeeds as an observable
  no-op. It makes no documentation edit, no no-lesson entry, no empty commit and
  no documentation PR. Approval-only text and generic praise qualify as no-ops.
- Consider substantive configured trusted human feedback and independent
  reviewer findings. Verify that a finding was accepted and its fix exists in
  final source; record the source PR/comment or review evidence, reviewed and
  final commit identities, relevant paths, accepted resolution, scope and
  verification. Rejected, speculative, dismissed or superseded findings do not
  establish guidance. Unverifiable findings wait for inspection, without
  inventing a lesson or acceptance.
- Produce concise source-linked proposed guidance only when that verification
  establishes a durable lesson. Correct superseded guidance rather than append
  a log. Retain the existing 12,000 UTF-8 byte context bound and bounded feedback
  budget; over-budget input requires inspection, never silent truncation.
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
- Track no-op count/rate with collection-outcome denominator and reason,
  duplicates avoided, eligible queue wait (including oldest pending age), and
  manual intervention count/reason. Keep operational evidence private and
  distinguish waiting for policy/authorization from scheduler starvation.

## Acceptance and manual verification for future implementation

Use fictional repositories and disposable state through the common facilities.

1. Collect approval-only text, generic praise and rejected, speculative or
   superseded findings. Confirm successful no-op tracking in Kandev and zero
   source edits, no-lesson entries, commits or PRs.
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
6. Confirm preserved paused work remains paused until separately authorized,
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
`kodi/workspace.py:feedback` currently enqueues feedback by record digest and
asks for a document entry when no durable lesson exists. Replace those behaviors
only in the separately authorized implementation, integrating the common
tracking/recovery and scheduling facilities first.
