# Shared development workspace

Create an operator-owned workspace outside the coordinator checkout:

```sh
python3 -m kodi.workspace --root /private/workspace sync
python3 -m kodi.workspace --root /private/workspace feedback
python3 -m kodi.workspace --root /private/workspace discover --organization ExampleOrg
```

Open that directory in your editor. Read its AGENTS.md, WORKFLOW.md and generated
repositories.json before working in repos/<repository>. Each repository has its
own development clone and instructions. Existing branches and edits survive sync;
sync adds missing clones and updates the registry without pulling or resetting
existing trees. Canonical sources and live service trees are separate.

The generated registry, feedback inbox, onboarding candidates and local editor
connection settings contain operator information. Keep the workspace private.
No historical chats or private model memories are imported.

## PRs authored directly with Codex

Codex can implement and commit a change in a development clone, push an ordinary
task branch and open a PR using the authorized GitHub publisher. An existing PR
can enter Kodi without recreating it or dispatching another coding agent.
Keep a separate human approver: GitHub does not allow a PR author to self-approve.

Prepare a requirements file and a JSON author report outside the repository:

```json
{
  "summary": "Fix the reported reconnect failure.",
  "test_steps": ["Disconnect and reconnect, then confirm the session resumes."],
  "acceptance_evidence": ["The reconnect path preserves the pending message."]
}
```

From the installed coordinator on the operator host, run:

```sh
python3 -m kodi.cli --config /private/path/config.json submit-pr \
  --repo project --checkout /private/workspace/repos/project \
  --pr-url https://github.com/ExampleOrg/project/pull/17 \
  --title 'Fix reconnect failure' \
  --requirements-file /private/review/requirements.md \
  --report-file /private/review/author.json
```

The checkout must be clean, committed, on the PR's branch and match its pushed
head. The PR must be open, come from the configured repository, target `main`
and have no active native auto-merge request. Only explicitly enabled catalog
repositories are accepted. Forks, sensitive paths and symlink changes require
separate operator handling; submission does not expand worker permissions.

The host-only command takes the delivery lock, clones committed source into a
separate coordinator checkout and runs independent offline QA and Gemini review.
It posts pending acceptance before validation; existing green CI or a supplied
author report cannot substitute for coordinator-created evidence. Successful
validation updates the existing PR using a head lease, posts exact-commit
acceptance and enters the normal CI and human review flow. Rebase updates dismiss
stale approvals under the configured protection rules. Protected merges remain
serialized and revalidate the current base. Deployment remains a separate action.

Repeat the command after fixing and pushing review findings. Failed checkouts
and evidence are retained, and resubmission uses the same job and PR. A repeated
submission already waiting for CI or human review at the same accepted commit
returns its existing job without running the checks again. Submissions with a
pending merge intent require operator reconciliation rather than replacement.
The original development checkout is preserved even when the coordinator rebases
its disposable copy; fetch the updated PR branch before continuing local work.

## Onboarding

Discovery reports installation-visible and public organization repositories;
it does not grant access or enable dispatch. From the coordinator source:

```sh
python3 -m kodi.onboarding ExampleOrg/new-project --profile python \
  --reviewer maintainer --workspace /private/workspace --register --publish
```

Registration extends the private operator allowlist within the existing App
installation grant, creates a canonical clone and adds a disabled catalog entry.
It cannot grant installation access or overwrite existing activation settings.
Supported baseline profiles are python, hardware-python, astro, static and
inventory. Preparation stops for a non-main default branch or existing Kodi
onboarding files. Workflow publishing uses the operator's existing gh login;
the runtime App deliberately has no workflows-write grant.

Finish exact-commit independent QA, different-model review and CI, then obtain
human approval. Configure required check identities and CODEOWNERS, and explicitly
enable the audited runtime entry. New targets start with auto-merge disabled.
Inspect existing deployment workflows before merging onboarding changes.

## Reviewed context learning

Workers load docs/kodi-context.md as bounded repository reference material,
limited to 12,000 UTF-8 bytes. It cannot change worker capabilities.
The feedback inbox collects configured human reviewers' PR reviews and comments.
Confirm findings before adding concise reusable lessons to repository context;
never copy credentials, private operational details or raw logs into public Git.

Set learning_since in private operator configuration to an ISO UTC timestamp,
then explicitly schedule `feedback --enqueue` to propose documentation jobs for
trusted feedback on PRs merged since that cutoff. Normal QA, review, CI and human
approval still apply. Reading feedback alone does not dispatch learning jobs.
