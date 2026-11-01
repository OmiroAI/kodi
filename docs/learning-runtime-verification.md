# Learning admission regression verification

The common Store claim path gives eligible delivery jobs priority and, after
`learning_schedule.max_product_claims` eligible product claims (default 8,
range 1–1000), serves the oldest eligible learning job. The counter is durable
and bounded; metadata recollection does not reset it. A busy repository or
unavailable capacity is not an eligible service opportunity. Scheduling remains
disabled unless the private operator configuration explicitly sets
`learning_schedule.authorized` to true. This source change does not enable it.
Repository enablement, the source repository identity, merged-source cutoff,
queued state and authoritative board Ready state are checked before selection.
Aging cannot authorize paused, deferred or cancelled jobs.

Accepted model findings are collected from the original task's private review
ledger only after final-candidate acceptance, with an independently verified
fixed disposition. False positives, unresolved findings and recursive learning
are excluded. Those inputs remain untrusted lesson candidates. A context-changing worker must
return an exact promotion manifest identifying the finding, new guidance, any
superseded text, and source/test paths and excerpts in the accepted commit. The
coordinator checks these references and the complete proposed replacement before
committing; independent QA and source review still decide semantic correctness. Learning edits are restricted to ordinary UTF-8
`docs/kodi-context.md` files of at most 12,000 bytes, through the existing
QA/review/checks/human-approval path. There is no documentation approval exception.

Use disposable state and fictional repositories for manual verification:

1. Enqueue one learning item and four product items in otherwise available
   repositories. Set the private test configuration's bound to two and explicitly
   authorize learning. Claim and finish two product items, reopen Store, and
   recollect identical feedback. Confirm the next claim serves the original
   learning identity, then another product progresses.
2. Repeat with authorization disabled, a changed source repository, a later
   cutoff, and paused/deferred/cancelled learning. Confirm no learning claim,
   unchanged candidate/retry state, and an actionable waiting reason.
3. Resolve an original independent finding with exact-candidate source/test
   evidence from another reviewer. Confirm collection retains task, reviewer,
   reviewed and accepted SHA identities. Repeat after restart. Change the
   disposition to false positive or remove source evidence; confirm exclusion.
4. Track a learning job twice through the existing internal tracking facility.
   Confirm the same Backlog task, source identity and bounded collection counters.
   Tracking must not move it to Ready. Complete a no-change worker result and
   inspect its successful outcome without a commit or PR.
5. Try a learning edit outside curated context, a symlink, and 6,001 two-byte
   characters. Confirm rejection before committing. Repeat with 6,000 characters
   and confirm the existing independent acceptance gates remain required.

Local tests do not supply independent acceptance. No live operation, paused-job resumption or configuration rollout is performed.

During an existing required review repair, the coordinator offers at most one
optional lesson pass per original task, persisted across retries and restart.
This adds no invocation or review cycle. Unsupported optional feedback does not
start a separate loop. New post-merge finding identities can create separately
tracked low-priority work; previous completed jobs remain terminal. Recollection
of the same findings cannot reopen or duplicate them. Scheduling still requires
explicit owner policy and, when tracked, authoritative Ready state.

Authorized filtered and recursive collections produce terminal common-tracking
no-ops without a worker, candidate or PR. Kandev receives a stable source identity
and private-evidence digest rather than raw feedback; the digest also binds the
Ready-state requirement comparison. Tracking creation remains Backlog. Bounded
per-job observations expose promotion/skipped counts, context byte size and
growth, optional additional review cycles (zero), collection/duplicate counts,
and elapsed delivery seconds. Candidate promotion counts describe mechanically
validated proposals; they are not independent acceptance or merged outcomes.
Terminal PR reconciliation refreshes elapsed delivery time.

Additional disposable verification:

6. Supply a promotion manifest with exact accepted source/test excerpts and one
   exact superseded rule. Confirm unrelated guidance survives. Try an invented
   excerpt, unknown finding, repeated finding, stale SHA, private text and an
   unrelated deletion; each must block before a commit. Apply the same manifest
   again and confirm one-time promotion rejects it.
7. Return substantive blocking review feedback on an ordinary task. Confirm the
   next required repair offers the optional lesson pass once. Restart and repeat
   feedback; confirm no extra lesson invocation or review loop. Combined edits
   still need new exact-commit acceptance.
8. Finish a learning job, recollect identical findings, then collect one genuinely
   new finding. Confirm one new queued learning identity containing only that
   finding, original terminal state retained, and no execution authorization from
   collection. Inspect tracking text for a digest and absence of raw feedback.

A documentation-only approval exception remains disabled. A future exception
would require separately approved protection/workflow design, exact code and tree
identity checks, independent validation, provenance and an auditable threat
model. Threats include untrusted findings injecting policy, documentation paths
hiding executable changes, symlink escape, stale acceptance after a tree change,
and comment text impersonating approval. The implemented path restricts learning
to an ordinary curated context file, revalidates repairs and base changes, and
retains all existing acceptance and protection gates. It transfers no approvals
and infers no authority from comments.
