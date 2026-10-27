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
278 characters. The review contract increases from 460 to 1035 characters:
853 additional characters total in the assembled prompt. All additions count
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
