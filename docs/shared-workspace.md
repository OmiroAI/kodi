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
