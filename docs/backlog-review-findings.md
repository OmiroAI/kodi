# Preserve findings across conflicting reviews

Status: Backlog. Implementation may begin only after the owner moves this task
to Ready. This document records requirements; it does not enable delivery or
change runtime behavior.

## Problem

A later approval can conflict with an earlier rejection of the same candidate.
Retrying reviews must not erase unresolved substantive findings, including when
the intervening change corrects routing metadata or requirements.

## Required behavior

- Track blocking findings against the exact candidate SHA and the requirements
  evaluated. Retain every verdict, provider/model identity and reported usage,
  including rejected and superseded attempts.
- A later approval of unchanged source cannot silently discard an unresolved
  substantive finding. Conflicts remain blocked until an inspectable resolution
  provides source/dependency evidence, a verified repair, or a demonstrated
  reviewer false positive.
- Distinguish stale metadata, confirmed source defects and reviewer false
  positives. Evaluate requirement changes and metadata corrections explicitly;
  they do not resolve unrelated findings. Preserve the prior requirement context
  and record how the current requirements affect each disputed finding.
- Record resolution evidence separately from original verdicts. Workers cannot
  resolve their own findings or mark acceptance passed. Require independent
  verification of the evidence and independently executed regressions.
- After a repair, revalidate the complete candidate against current requirements.
  Bind acceptance to the exact SHA and current requirements; acceptance cannot
  carry over merely because a PR number or review result is reused.
- Bound retries. Exhausted retries or unresolved disputes stay blocked for
  inspection; do not automatically route to a more favorable reviewer.
- Retain prior verdicts for inspection, but request a fresh source review without
  copying a previous full verdict into the requested answer. Attach independently
  verified facts separately. Do not infer causality from repeated findings alone.

## Regression acceptance scenarios

1. Reject a candidate with a substantive finding, then approve the same SHA:
   the original verdict and finding survive and acceptance remains blocked.
2. Correct unrelated routing metadata, or change an unrelated requirement:
   evaluate that change explicitly while retaining the unresolved source defect.
3. Repair the defect in a new SHA: retain the old evidence, independently verify
   the repair and execute regressions, then evaluate the complete new candidate
   against current requirements before permitting acceptance.
4. Demonstrate a reviewer false positive using source/dependency evidence and
   independently executed regressions: record the independent resolution and
   retain the original verdict and provider/model usage.
5. Exhaust the retry bound with a disputed finding: remain blocked with evidence
   available for inspection and no automatic reviewer promotion.
6. Attempt worker self-resolution or self-acceptance, or reuse acceptance after
   SHA/requirements drift: reject the attempt and preserve the audit records.

## Delivery and manual verification

Once Ready, implement with the existing deterministic coordinator and Kandev UI.
Keep native Kandev agent/session startup disabled and preserve all capability
boundaries. Do not add an agent framework or dashboard.

Run `python3 -m unittest discover -s tests -v` for coordinator changes. Manually
exercise the scenarios above with fictional fixtures and inspect the retained
verdicts, resolution evidence, identities, usage and blocked/accepted state.
Verify that metadata-only corrections cannot clear unrelated defects and that
only independent evidence can resolve a dispute.

Delivery still requires exact-commit independent offline QA, different-model
review, required GitHub checks, manual testing instructions and human merge
approval. Private audit evidence, operational logs, private code and
infrastructure details must remain outside this public-source repository.
