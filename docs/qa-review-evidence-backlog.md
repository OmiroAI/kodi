# Use QA evidence to avoid redundant review test runs

Status: Backlog. Implementation waits for the owner to move this task to Ready.
This document records proposed requirements only; it changes no runtime behavior
or acceptance gates.

## Proposed behavior

Supply the reviewer with a concise summary of independent offline QA for the
exact candidate commit. Name the candidate SHA, suite and outcome, and include
available verification details from the recorded evidence. Mark unavailable
details explicitly; never infer a suite, dependency, environment or successful
result from missing information. Do not include full QA logs by default or copy
private operational evidence into source or prompts.

Validate the QA record against the candidate SHA before presenting it as
successful candidate evidence. Missing, failed, stale or mismatched records must
not be presented as a pass. Evidence is context for independent semantic source
review, never approval or a substitute for any acceptance gate.

Guide reviewer tool use toward concrete correctness uncertainties left by source
inspection. Reviewers may request or run focused checks. Avoid broad suite reruns
when exact-commit independent QA already covers them, while preserving the
reviewer's ability to investigate specific concerns.

Distinguish source-review criteria from downstream required GitHub checks and
human approval. A source-review verdict evaluates correctness and coverage of the
candidate; it must not require its own approval, later CI results or human merge
approval as prerequisites for completing source review. Those downstream gates
remain mandatory for acceptance.

## Regression and evaluation plan after Ready

- Cover matching successful evidence and missing, failed, stale and mismatched
  evidence, including candidate changes after QA. Assert that unsuccessful or
  unrelated evidence cannot appear as a successful candidate result.
- Use synthetic fixtures for an author environment where a test runner is
  unavailable while exact-commit independent QA passed. The reviewer must not
  infer that independent tests could not run from the author's environment.
- Use a synthetic correct candidate with downstream CI or human approval still
  pending. Source review may approve on its own criteria without a circular
  repair/no-change requirement; acceptance must still remain blocked by every
  unmet downstream gate.
- Cover a concrete uncertainty requiring a focused check, preserving independent
  semantic review and the ability to report blocking findings despite passed QA.
- Compare representative candidates before and after using the same candidate
  commits and review settings. Record broad suite rerun counts, tool-output
  volume, available token usage and finding quality, including missed or spurious
  findings. Keep raw runs and operational measurements in private storage;
  report unavailable measurements explicitly. No comparison has been run yet.
- Run `python3 -m unittest discover -s tests -v` for implementation changes and
  manually inspect reviewer context for concise evidence and gate separation.

Exact-commit independent offline QA, different-model review, required GitHub
checks and human approval remain required. Native Kandev agent/session startup
stays disabled. Implementation must preserve worker capability boundaries and
must not introduce publishing, merge or deployment authority.
