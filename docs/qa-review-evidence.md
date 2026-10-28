# QA context verification

The coordinator selects the latest stored QA record for the job and candidate,
including a failed rerun. It validates the payload SHA and boolean outcome;
missing or invalid payloads cannot appear as passing QA. A changed candidate
cannot inherit another commit's results. Author reports remain historical data.
The existing acceptance gates remain separate and unchanged.

The summary contains no raw logs. Public built-in suite/command labels and the
generic `offline container` environment label are allowlisted; arbitrary private
profile commands, image/host identities and suite names are withheld as unknown.
Numeric duration and test count are included only if valid and recorded. Existing
helpers do not record duration, count or environment identity, so these remain
unknown. No parsing of test logs or inference from selected suite is performed.
New coordinator QA runs persist the requested suite; old records stay unknown.

Manual verification in a disposable state database:

1. Store passing exact-SHA QA and an author report saying pytest was unavailable.
   Capture the review-provider prompt: it must retain the author limitation and
   independently report passing QA, with absent metadata unknown.
2. Append a failed QA record at the same SHA. Capture another review and confirm
   failure replaces the pass. Try malformed JSON, a wrong payload SHA and a new
   candidate: none should be described as verified passing QA.
3. Simulate explicit primary quota exhaustion with an already configured fallback.
   Compare both prompt arguments byte for byte. Requirements and complete diff
   must remain intact; reduce the context budget and confirm failure before any
   reviewer call instead of truncation.
4. Review a technically correct fixture with human approval and downstream CI
   pending. Source review may approve; publication/merge acceptance still needs
   its separate gates. Challenge a concrete untested failure path despite QA.

Prompt-size measurement (characters, not tokens): for a synthetic 40-character
SHA with passing `system` QA and unknown remaining metadata, the QA block adds
278 characters. The review contract increases from 460 to 1189 characters:
1007 additional characters total in the assembled prompt. All additions count
against the existing full-context budget. Provider-specific wrappers may add
more; Qwen also receives the contract as a system message.

For a small before/after pilot, use the same three disposable candidate commits
with identical requirements, author reports, stored QA, provider/model and tool
permissions. Include the unavailable-author-pytest fixture and one intentionally
untested semantic defect. Run baseline and updated prompts, retaining raw records
in private storage. Record prompt characters, available token usage, broad suite
rerun counts, focused checks, tool-output volume, valid findings, missed defects
and spurious findings. Compare findings against a manually prepared defect list
and report all runs, including failures and unknown usage. No reviewer pilot or
savings measurement has been performed; local mocked tests are regression
coverage, not independent candidate acceptance.

Each review preparation now stores numeric `review_context` evidence for its
candidate: `prompt_chars`, `qa_block_chars`, `without_qa_block_chars`,
`limit_chars` and `within_budget`. This includes budget rejections and records
one shared measurement for primary/fallback. Counts exclude provider wrappers;
`without_qa_block_chars` removes only the QA block, not the revised contract.
These records contain no prompt text and confer no acceptance.

Executable paired pilot (operator-run, private disposable fixtures only):

1. Freeze three fixture candidates and a private expected-defect list: a correct
   change, unavailable author pytest with successful independent QA, and a
   semantic defect outside the QA suite. Use identical requirements, author
   reports, model, tool permissions and stored QA in both arms.
2. Capture baseline prompts from the prior implementation and updated prompts
   from this implementation. Run each arm twice per candidate with fresh reviewer
   sessions, alternating arm order. Do not change live jobs or acceptance state.
3. Inspect the private tool traces and adjudicate findings against the frozen
   defect list. Count broad covered suite reruns, focused checks, valid findings,
   missed defects and spurious findings. Count prompt characters directly (or
   use updated `review_context` evidence). Failed/incomplete runs must be reported
   separately; do not enter invented zero counts.
4. Write a private JSON array with one object per completed run, e.g.
   `{"candidate_sha":"fixture", "repeat":1, "arm":"before", "prompt_chars":1500,
   "broad_reruns":1, "focused_checks":0, "valid_findings":0,
   "missed_defects":0, "spurious_findings":0}`. Use measured values and pair it
   with an `after` object for the same candidate/repeat.
5. Run `python3 scripts/qa_review_pilot.py /private/path/pilot.json` to report
   totals and after-minus-before differences for both reruns and finding quality.
   The tool rejects incomplete pairs, duplicate arms and unknown measurements.
   Report exclusions and model identity alongside those aggregates. This tool
   summarizes observations; it does not run reviewers or establish causality.

No before/after reviewer pilot has been run for this change; no savings are
claimed. Local synthetic measurements and mocked regressions are not independent
QA or source acceptance.

The orchestration regression runs `process` through independent QA and the real
review path with simulated external workers. The source reviewer is invoked
while the technical acceptance gate is false and downstream approvals are
absent. An approving technical verdict enables the existing post-review gate;
a semantic rejection blocks publication despite passing QA. The gate after
review is intentional: removing it would weaken acceptance, not fix a circular
dependency. These mocked verdicts verify ordering and prompt instructions, not
live model compliance or independent acceptance.
