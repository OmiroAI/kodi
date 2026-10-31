# Operator guide

Keep the runtime config and credentials in a private directory outside Git. Use
`examples/config.json` and a fictional catalog from `examples/repositories.json`
as templates; populate your real values only in private storage. Directories
containing secrets should be owner-only, and credential files should be mode 0600.

## Install and configure

### Runtime continuous deployment

An operator can install a reviewed copy of `scripts/deploy_runtime.py` outside
the runtime checkout and customize the `deploy/kodi-update.service` example.
Enable `kodi-update.timer` to check main every two minutes. The updater uses the
host's existing GitHub read access; no inbound SSH endpoint or CI secret is needed.
Only the exact current main commit with a successful push QA run is activated.
Protected main and human merge approval remain the release authorization.

The updater holds Kodi's delivery lock, defers active or interrupted work, and
requires a clean runtime on main with the expected origin. It fetches and updates
only the runtime clone using a fast-forward, then restarts the coordinator.
A start failure restores the previous commit with `git reset --keep` and starts
it again. Check the updater journal and `git rev-parse HEAD` in the runtime to
verify activation. Systemd active status is a process check; functional health
still requires a disposable dashboard routing pilot after initial installation.
Keep the updater installation separate from canonical sources and worker mounts.

The coordinator requires Python 3.11+, Git, Docker, OpenSSH, OpenSSL and GitHub
CLI on the operator host. Build the sandboxed implementation/review image:

```sh
docker build -t omiro-worker:agy-1.3.3 -f deploy/Dockerfile.worker deploy
```

The checked-in QA image is a generic Python test environment. It intentionally
contains no application's dependency graph:

```sh
docker build -t omiro-qa:phase1 -f deploy/Dockerfile.qa deploy
```

Copy `scripts/qa_remote.py` to the independent QA host's private helper directory.
The existing transport uses `.local/lib/omiro-dev/qa_remote.py` and
`.local/state/omiro-qa/incoming/` on that host. Verify the SSH host key separately.
QA archives contain committed source only, have a size limit, and are extracted
into disposable workspaces. QA containers have no network, provider credentials,
host Docker socket, production mounts or hardware access.

Copy the service examples into the operator's user service directory and adjust
the checkout, config and helper paths for your installation. The default config
path is `~/.config/omiro-dev/config.json`; pass `--config` before the CLI command
when using another location. Do not commit the customized service definitions.

```sh
systemctl --user daemon-reload
systemctl --user enable --now kodi-egress.service kodi.service
```

The dashboard Compose example requires a private environment file defining
`TAILSCALE_IP` (the private bind address), `PUBLIC_HOST`, `SECRET_DIR`, `TLS_DIR`,
`GITHUB_ORG`, and optionally `KODI_SETUP_REPOSITORY`. These are operator inputs;
no organization, hostname, network address or repository inventory is embedded.
The dashboard binds to loopback and its authenticated TLS proxy to the configured
private interface. Use valid certificates and preserve Basic authentication.

```sh
docker compose --env-file /private/path/compose.env -p kodi -f deploy/compose.yaml up -d
python3 -m kodi.cli --config /private/path/config.json bootstrap
```

The pinned dashboard asset compatibility patch enables ticket-only creation
without native model credentials. Force-recreate the access container after
changing the mounted template; an Nginx reload alone does not regenerate it.
Before a dashboard upgrade, verify its content hash, every substitution, desktop
and mobile draft creation, and the native agent gates. Never give the dashboard
a Docker socket, production directory or provider/publisher credentials.

## GitHub identity and protection

Use an owner GitHub CLI login for explicit repository administration. Register
a dedicated publisher App through the authenticated `/setup/github` page or the
example manifest. The organization and repository are operator environment inputs.
Install the App only on owner-authorized repositories. Its source permissions
are contents/issues/pull requests/statuses write and checks read; it has no
workflow editing, administration, deployment or protection-bypass permission.

Provider keys and installation metadata stay outside Git. The App owner must
accept any installation permission update. The operator-owned
`github/repositories.json` next to the App config scopes tokens without changing
installation grants. Public repository visibility is not needed to use Kodi.

Require human approval and exact-commit CI/acceptance on each target branch.
The operator-only source-pinning helper takes explicit repository/App inputs:

```sh
python3 scripts/protect_check_source.py --repository ExampleOrg/project \
  --app-id 12345 --check project-tests
```

This updates an already configured protection rule. The coordinator never grants
itself access or changes protections. Its read-only check inspection uses REST
check runs and statuses, without requiring Actions workflow access.

The publisher App authors PRs so a separate human can approve. If Telegram human
approval is explicitly enabled, the matching host owner GitHub login may submit
a commit-pinned review after an authenticated owner action. Neither identity is
passed to model workers. See [Telegram control](telegram-control.md).

## Canonical repositories and QA profiles

Use separate canonical clones, never running production directories. Keep them
on `main`, clean and fast-forwardable, with a single matching origin. Kodi fetches
upstream main before implementation and before publication. It never force-resets
or deletes operator work. Dirty checkouts, divergence and unexpected branch/head
changes require inspection.

The generic suites are `system` (this project's unittest suite), `repository`
(the selected repository's `.kodi/qa.py`) and `astro` (a locked offline build).
Additional suites belong in an owner-only JSON file on the QA host:
`~/.config/omiro-dev/qa-profiles.json`.

```json
{
  "application": {
    "image": "my-application-qa:reviewed",
    "command": "python -m pytest tests -q -p no:cacheprovider"
  }
}
```

This file is private operator configuration, not issue input. It can also
override a built-in suite's command/image. Keep application dependency manifests,
custom images and hardware/deployment instructions with the application or in
private operations storage. Rebuild and validate the image after dependency
changes; QA jobs do not install packages or gain internet access.

The optional Astro image uses `deploy/Dockerfile.qa-astro` plus the selected
site's package manifests in a private build context. Runtime QA compares the
exact lock and copies preinstalled dependencies into a disposable workspace.
A new lock requires an operator rebuild. Hardware tests require a separately
authorized bench; syntax checks are not proof of physical device behavior.

## Repository onboarding and tickets

Use an explicit operator catalog and reviewers:

```sh
python3 scripts/onboard_repositories.py --config /private/path/config.json \
  --catalog examples/repositories.json --reviewer owner-login
```

The helper validates existing App access, creates canonical clones, records
required checks/reviewers, and generates read-only dashboard mounts. It cannot
grant installation access. New repositories stay disabled until their reviewed
QA entry point is merged. Explicitly enable them after validating the full path.
Automatic merging remains separately controlled and defaults off for new repos.

Register each canonical clone in the dashboard repository catalog and mount only
its source read-only. For native dashboard tasks, select one repository before moving the draft to
Ready; Kodi resolves its ID through the workspace repository catalog and matches
its name to a configured catalog key or its GitHub URL to a configured repository.
Missing, unknown or ambiguous selections are blocked. Integrations can still use
an explicit `Repo: <catalog-key>` description line, which takes precedence over
the dashboard selection. Only configured, enabled repositories can dispatch. GitHub issues labeled
`kodi:ready` are already scoped to their own repository. Requirements should
include observable acceptance criteria and manual reproduction instructions.

External assistants may create issues and request dispatch through this approved
queue. They receive no Docker, publisher, notification or production authority.
Do not import their private memory or operational transcripts into worker prompts.

## Workers and independent review

Codex uses a dedicated operator-approved login under the configured state dir.
Gemini review uses a separate Antigravity login, explicit model pin and the
sandbox image's verified CLI release. Model availability is provider-controlled;
verify it in the dedicated reviewer environment before changing the pin. Failed
launches, ERROR terminal results and denied actions never count as reviews,
even when an approving partial response is present. Reviewers inspect source and
operator build evidence rather than installing dependencies or release artifacts.

An optional local reviewer can be configured using `qwen_url`, `qwen_model` and
`qwen_env`, with a model-scoped key. Use HTTPS, or HTTP only inside an encrypted
private network. Do not disable certificate verification. No paid fallback is
automatically configured. Usage records report measured time and provider counts;
unknown token counts stay unknown rather than becoming invented prices.

## Review context budget

`review_context_max_chars` bounds the assembled review prompt in characters. It
defaults to 200000 and accepts integers from 1 through 1000000; booleans and
non-integer values are rejected before invoking a reviewer. Operators may set
250000 after checking the configured reviewer's actual token context capacity
and reserving space for its response. Character counts are not token counts.

This setting applies to the complete candidate diff, requirements, author report
and changed-file context before primary review or fallback. The independent
100000-character diff limit remains unchanged. Nothing is truncated to fit; an
over-budget error reports the actual and configured sizes. The Antigravity worker
policy is included in this budget. Repository context, harness instructions and
response tokens also need capacity in the selected provider. Increasing this budget does not grant acceptance or
resume blocked jobs. Test with a disposable prompt above the default but below
250000: the default must reject it, the override must retain it in full, and an
override below its size must reject it before either reviewer is invoked.

## Reviewer quota fallback

When Antigravity explicitly reports exhausted credits or subscription quota, the
coordinator automatically tries the operator-configured Qwen reviewer once for
that candidate. This requires all of `qwen_url`, `qwen_model` and `qwen_env` to
already be configured. No endpoint, credentials or paid provider is added
implicitly. Set `reviewer_fallback` to `null` to disable fallback; the default is
`"qwen"`. The primary reviewer remains the first choice on subsequent reviews.

Fallback uses the same complete diff and candidate SHA, validates the structured
verdict, records the actual provider/model and usage, and retains primary worker
logs. Authentication failures, ordinary rate limits, invalid reviews, denied
actions and rejected verdicts do not trigger fallback. Failed fallback blocks the
job normally. Codex cannot review its own model family. Already blocked jobs
still require inspected recovery; this change does not requeue them.

Manual verification: in a disposable coordinator environment with configured
Qwen, simulate an Antigravity quota error, then confirm one Qwen review and
commit-bound evidence naming Qwen. Repeat with fallback disabled, authentication
failure and a negative verdict; confirm no fallback acceptance. Verify independent
QA, CI and human approval are still required before merging.

## Observe, recover and approve

```sh
python3 -m kodi.cli --config /private/path/config.json list
python3 -m kodi.cli --config /private/path/config.json usage --summary
python3 -m kodi.cli --config /private/path/config.json usage --daily
python3 -m kodi.cli --config /private/path/config.json usage --tasks
journalctl --user -u kodi.service -u kodi-egress.service --since today
```

Private state includes job evidence, model output and recovery checkouts. Keep
it out of the dashboard, public source, PR attachments and model-worker mounts
for other tasks. OS-released locks serialize coordinator Git/merge operations.
Interrupted execution is blocked for inspection; uncertain publication is not
blindly repeated. Preserve artifacts and inspect branch identity before recovery.

Positively identified transport failures receive two bounded retries with
persisted budgets and backoff. Authentication, permission, billing and unknown
failures require inspection. Authenticated model streams permit five minutes of
idle reasoning, while overall worker runtime/resource limits remain in force.

Fresh rebase conflicts are handled in isolated copies with bounded author repair.
Sensitive paths, symlinks, binary conflicts, out-of-scope edits and unresolved
markers stop automation. Original and failed checkouts remain available. Rebased
QA/review regressions have a separate bounded repair budget without resetting
original coding attempts. Each new candidate reruns full offline QA and
complete-branch different-model review before a leased PR update.

Once CI passes, ready state is persisted before independent dashboard,
notification and GitHub review-request delivery. Requests are deduplicated per
job/SHA; dismissed or older-commit approvals need a new request. Existing same-SHA
approvals and pending requests are respected. Read-only GitHub requests may retry;
uncertain writes are reconciled before any repeat.

Operator-enabled auto-merge rechecks human approval, branch identity, the verified
head, current main and required checks. A changed base requires fresh validation
and usually new human approval. Native protected merge requests are commit-pinned
and serialized. Models never approve, merge, change policy or deploy. Merged and
closed PRs receive durable terminal board/notification delivery.

## Public-source releases

Keep private operations outside this tree. [The release guide](public-release.md)
explains the automated privacy check, optional private denylist, clean export and
why historical commits/PR discussions must remain private unless separately
reviewed. Publication is an explicit owner action, not a setup-script side effect.


## Usage reporting semantics

Daily reports group usage by its ledger timestamp converted to UTC. A run crossing
midnight is attributed to its completion date; the ledger cannot reconstruct
per-token timestamps. Task reports include the dashboard task ID, repository,
state and PR URL, including tasks with no recorded usage. Existing ledger and
evidence records are available immediately without a migration.

Provider totals include all recorded worker runs, including failures and recovery.
Missing or invalid input/output counts make that provider total unknown; zero
remains zero. A review iteration is a persisted, validated review verdict,
including rejected verdicts and revalidation of the same commit. Transport
retries and failed launches are worker runs, not completed review iterations.
QA runs count persisted QA evidence; coding attempts use the coordinator's
persisted attempt counter. These counts are operational activity, not acceptance.

The pinned Kandev 0.97.0 statistics routes expose read operations. Kodi workers
run outside native Kandev sessions, so native agent/token charts do not represent
Kodi worker usage. Kodi therefore mirrors summaries through the supported task
description API and exposes daily reports in its CLI. Native chart ingestion
requires a supported upstream external-usage ingestion API; do not write Kandev's
database or enable native agents/session startup to manufacture statistics.

Manual verification: run both reports against a disposable state database with
two UTC dates, a task with usage and a task without usage. Confirm task IDs match
the board, unknown tokens remain unknown, and rejected and approved review
verdicts each count once. Refresh a task description and compare its provider
and iteration totals with the task report.

## Large repository QA exports

The default sender and independent receiver still cap source archives at 50 MiB
and reject links. A trusted operator may select a larger bounded export for a
repository that requires it: set `qa_archive_max_bytes` in coordinator config and
`archive_max_bytes` plus `expanded_max_bytes` in the selected private QA profile.
Each bound must be a positive integer no greater than 512 MiB. The receiver's
profile is authoritative; request payloads cannot increase it.

`materialize_internal_links: true` in that private profile permits aliases to
committed in-tree files/directories. The extractor validates the full archive
before writing source, then copies target bytes as regular files/directories.
It never creates symlinks or hardlinks. Absolute/escaping targets, dangling or
cyclic links, file/directory collisions, special files, duplicate paths, and
expanded byte/entry budget overruns fail closed. The export is a materialized
source view: tests requiring symlink identity need a separately reviewed QA path.
No archive exclusions or guessed replacement contents are applied.

A private profile may also set `memory_mb` to an integer from 256 through 8192
for memory-intensive compilers; the default remains 2048 MiB and swap is capped
to the same value. Network isolation, CPU, PID, mount and runtime limits do not
change.

Both sender and independent helper changes require reviewed installation before
use. Configure a dedicated suite rather than overriding other repositories'
profiles. The network, credentials, mounts, runtime budget, and approval gates
remain the same. First verify a disposable archive with a file alias and a
folder alias, and confirm traversal/dangling/cycle fixtures fail before any
source file is written.

## Asset review preparation

Review material is read as bytes from the merge base and exact candidate Git
objects. UTF-8 source patches and candidate context remain complete within the
existing 100,000-character diff and 200,000-character context limits. Binary and
non-UTF-8 changes list both versions' paths, blob IDs, modes, byte sizes and SHA-256
identities. A tool-enabled reviewer must inspect those objects with read-only
Git/file tools and image tools when supported, and return blocking findings when
inspection is unsupported. The text-only Qwen endpoint cannot review assets or
receive asset-review quota fallback; configure a tool-enabled reviewer instead.

After preparation failure, retain the original blocked job, checkout and evidence.
Inspect its recorded candidate SHA and clean checkout before retrying review of
that same job; do not delete/reclone it, requeue coding, or manufacture acceptance.
A review retry alone does not change blocked state or authorize publication.
Existing independent QA evidence remains commit-bound; changed commits need fresh
QA and different-model review. Required GitHub checks and human approval remain
mandatory.

Manual verification: in a disposable repository change a raw non-UTF-8 asset
without NUL bytes alongside a source file. Confirm review includes the complete
source patch and both asset identities, then inspect both asset versions using
the listed blob IDs. Try an unsupported format and verify a blocking finding;
try a text-only reviewer and verify an actionable preparation error with no new
review evidence. Retry the original candidate with a supported reviewer and
confirm original QA evidence and checkout remain intact.

For acceptance of the coordinator change itself, record the candidate commit and
confirm independent offline QA, a different-model review of the entire candidate,
all required GitHub checks, and human approval at that commit. Local regression
tests (including simulated worker verdicts) verify gate enforcement; they do not
provide those independent acceptance records. A files-only repair must leave
acceptance pending until the authorized operator completes these steps. Preserve
the blocked job and its evidence throughout that handoff.

## Worker filesystem handoff smoke test

Each invocation mounts a fresh `<role>-worker-*` directory at `/output`.
Host logs and validated result copies are stored separately under
`<role>-evidence/attempt-*`, which is never mounted into workers. The attempt's
`worker-output.txt` identifies the retained untrusted directory. Recovery retry
archives continue to copy the host output tree; the original worker directories
remain alongside it for inspection. Legacy `<role>-output` directories are
never reused as trusted evidence. Treat their contents as untrusted, including
when inspecting or copying them manually. Results must be ordinary files opened
relative to a pinned directory descriptor with no-follow and nonblocking flags,
and are limited to 1 MiB. Invalid results cannot produce a successful invocation.

Disposable container smoke test (requires an already available worker image):
create a temporary checkout and job directory, with a sentinel containing
`untouched` outside the checkout and worker output. Use a disposable test harness
in place of the provider command which creates `/output/events.jsonl` and
`/output/stderr.log` symlinks to that sentinel's absolute host path and writes a
normal JSON `/output/result.json`. Invoke `run_worker` with the existing
`docker_args` restrictions, temporary state/auth/proxy directories and no real
credentials. Confirm the sentinel is unchanged, the result succeeds and host
logs appear only in `<role>-evidence/attempt-*`. Repeat for author and review
(the review checkout must remain read-only). Replace `result.json` with a
symlink, FIFO, directory, missing file, malformed JSON and a file over 1 MiB;
each invocation must fail promptly without acceptance evidence, retaining host
logs and usage. Retry after a valid attempt with no result and confirm that the
earlier result is never reused. Remove only the disposable fixture afterward.
Never use real configuration, login or credential files as test targets.

The local simulated regressions are in `tests/test_worker_handoff.py`. They do
not replace exact-commit independent QA, different-model review, required GitHub
checks or human approval for this patch.
## Scoped retained-candidate recovery

The host CLI supports `recover preview JOB --stage qa|review`, followed by
`recover authorize JOB --stage STAGE --digest DIGEST --owner-reference REFERENCE`
and `recover apply JOB --stage STAGE --digest DIGEST`. The reference records a
separate, scoped owner instruction; issue text and historical approvals are not
authorization. Preview opens existing state read-only and performs only read-only
Git and GitHub remote inspection; it never contacts Kandev. Board-originated
plans report pending authoritative task inspection and are not marked resumable
until authorization and apply independently check the live task state and
requirements. Its digest binds the candidate, requirements, dispatch,
evidence, identity, base and consumed budgets. Missing legacy dispatch provenance
requires operator inspection. Coding, publication and merge recovery remain
inspection-only. Apply never publishes, recreates PRs, or schedules coding.

Preview includes the current stored requirements in its JSON inventory so the
operator can inspect stale-requirements blockers. Treat this output as private
operational data; raw dispatch records and worker artifacts remain omitted.

Apply holds the existing delivery lock and records its claim before invocation.
An interrupted claim is never replayed automatically: inspect preserved artifacts
before issuing another scoped authorization. A completed retry is idempotent.
QA recovery can leave review blocked for a separate preview and authorization.
Same-commit review recovery retains QA and the existing PR. Local/mocked tests
are regression evidence; independent acceptance, CI and human approval remain
separate requirements.

`recover track JOB` refreshes status, blockers, commit, PR and usage on the
original Kandev task for board-originated jobs. Missing tasks or changed
authoritative requirements require inspection before refreshing. For internal
jobs it associates a deduplicated existing-API Kandev task in Backlog, then uses
the same status reporting. This is tracking only: it does not authorize execution or
move a task to Ready. An operator must inspect legacy provenance and deferred
work separately. Board recovery requires the authoritative task to be Blocked
with unchanged requirements. Paused, cancelled, Backlog and deferred work cannot
be recovered by time, restart or available capacity.

Manual verification in disposable fictional state: retain a clean review-failed
candidate with passed exact-SHA QA and an existing PR. Preview twice and compare
state and checkout bytes; authorize its digest and apply twice, confirming one
review invocation, unchanged QA/PR and attempts. Repeat with a dirty checkout,
changed requirements/base, unknown remote head and exhausted budget; each must
block before invoking a worker. Hold delivery.lock during apply and interrupt a
claimed retry; verify concurrent apply and restart cannot replay it. Confirm a
deferred job numbered 48 and its artifacts remain unchanged.
After changing the stored requirements, confirm preview displays the revised text,
reports stale requirements, and rejects authorization using the previous digest.

Manual tracking verification: in disposable fictional state, run `recover track
JOB` twice for a board-originated blocked job and an internal job. Confirm both
task descriptions show the retained status and actionable error, commit and PR;
the original task is reused, internal tracking creates only one task, and neither
workflow position nor recovery authorization changes. Change the original task's
requirements or remove it and confirm tracking refuses to overwrite or replace it.
## Provider-aware review assembly

The implemented provider-aware builders are opt-in via the boolean
`provider_aware_review_context` setting (default `false`). Defaults preserve the
existing changed-file context and Qwen fallback admission. Enable this mode only
after an offline size/capability preflight for every intended provider: the full
Qwen repository material can exceed the budget even for a small patch. Code and
regressions do not require a live provider experiment or configuration rollout.
The following mode descriptions apply when the setting is `true`.

Antigravity receives the complete cumulative patch, requirements and author
report, plus read-only access to the clean exact candidate checkout. Full changed
files are no longer duplicated in that prompt. It must inspect surrounding code,
callers and dependencies through tools. Checkout drift blocks preparation.
Gemini retains the existing changed-file context behavior.

Qwen independently assembles all committed candidate text files, including
unchanged callers and dependencies, for both direct review and quota fallback.
It fails closed for non-text dependencies, symbolic links, non-blob entries,
secret-shaped paths or material above the budget; it never samples files. Changed assets retain the
tool-enabled reviewer requirement. A compact primary fitting the budget does not
guarantee its text-only fallback will fit. Existing independent QA, different-model
review, CI and human approval gates remain required.

Each actual provider invocation records a commit-bound `review_context` evidence
entry with the assembled prompt's character and UTF-8 byte sizes. These measure
coordinator material including worker policy, before harness additions, not token
savings.
Provider-reported usage remains attributed separately to each provider, including
failed primary attempts; tool-read volume is not inferred from prompt length.

Manual verification: run `python3 scripts/review_context_pilot.py` for a disposable
Git fixture with two repairs and an unchanged caller. Its reproducible material
sizes are 42234 characters before, 207 compact and 42276 for Qwen. Tokens and
credits remain unmeasured. Run the multi-commit regression with
`python3 -m unittest discover -s tests -p test_review_assets.py -v`; inspect its
primary and fallback assertions, upstream exclusion, budget failure and dirty
checkout rejection. Mocked verdicts provide regression evidence only. No live
provider experiment or service operation is needed for this pilot.
## Readiness dependencies

Repository catalog entries may declare `onboarding_dependencies` as a list of
stable prerequisite IDs and `readiness` as a list of records. Each record has
`id`, `repo` (the catalog key), and a nonempty `checks` list. Each check requires
`id`, `state`, `actor`, `reason`, `action`, and an evidence reference. Only
`state: resolved` with evidence satisfies a check. Feature descriptions may add
`Dependency: <id>` lines; these supplement repository prerequisites. Keep this
operator catalog and its evidence outside Git. Preparation never changes
`enabled`, permissions, task priority or approval requirements.

Dependency-blocked Ready tasks retain their priority and original identity; sync
reports the prerequisite reason and reevaluates them on subsequent runs. It does
not move Backlog tasks to Ready. Queued jobs are checked again before claiming,
so disabling a repository or reopening a prerequisite prevents dispatch without
resetting candidate, PR, retries or evidence. CLI `list` and board progress show
separate dependency and acceptance blockers. Missing or duplicate linked records
fail closed.

Use `link-failure JOB --identity STABLE_ID --evidence-file PRIVATE_JSON` on the
trusted coordinator host to record already confirmed unrelated upstream work.
The JSON requires `task` (the separate work identity), `check`, exact 40-character
`baseline_sha` and `candidate_sha`, comparable reproduction `conditions`, sanitized
`evidence` references, an unrelatedness `reason`, responsible `actor`, and
`confirmed_unrelated: true`. Repeated records update the same identity and retain
reciprocal feature job links; changing evidence never creates replacement work.
This command neither creates external tickets nor authorizes workers to do so.
Unconfirmed failures remain normal QA/review blockers. Separate tracking does
not remove the acceptance impact: linked failures block the acceptance gate even
if the upstream task is marked resolved or a waiver flag is supplied. There is
no owner waiver implementation in this coordinator, so this change introduces
none. QA and review are also bound to the feature requirements digest; changed
requirements require fresh evidence while earlier evidence remains stored.

Manual verification in disposable state: configure an enabled fictional catalog
entry with a blocked prerequisite, place a linked feature in Ready, and sync.
Confirm no issue or worker starts and inspect the actor and next action. Resolve
the check with evidence, sync twice, and verify one original feature job. Repeat
with `enabled: false`, a missing/duplicate prerequisite and a Backlog task; each
must remain undispatched. Restart with a queued job and repeat after resolving
its prerequisite; verify its ID, candidate, PR, evidence and retry budget survive.
Record the same synthetic upstream failure twice, restart, and update its evidence;
verify one linked identity, reciprocal feature links and blocked acceptance.
Changed requirements or candidate SHA must not reuse earlier acceptance. These
local checks do not provide independent exact-commit acceptance.

## Repository coding ownership

Owner-confirmed policy: Kodi-tracked work holds its repository from coding claim
through QA, review, checks, human approval and blocked states. Merge, explicit
cancellation or confirmed PR closure releases ownership. Uncertain mutations or
running executors retain ownership until reconciled. Drafts, unstarted queued or
blocked tasks, successful learning no-ops and untracked external PRs do not hold
ownership. This does not authorize resuming paused work. Existing global stage
capacity and protected merge serialization still apply independently.

Disposable manual pilot (no runtime config or service changes): create a temporary
Store database with two Ready jobs in fictional repository `project` and a third
in `other`. Claim the first, move it through QA/review to human review, and confirm
the second cannot claim. Once stage capacity is free, confirm the `other` job can
claim despite the older waiting job. Restart Store, retain the first as blocked,
and confirm its ownership persists. Inspect CLI list's `repository_wait` and the
original board task's waiting reason. Record cancellation or reconcile a real
closed/merged disposable PR using normal lifecycle maintenance; confirm the next
same-repository job retains its ID and budgets and can claim. Repeat with an
unsettled remote mutation: terminal state alone must not release it. Never resume
paused jobs for this pilot. Automated disposable coverage is in
`tests/test_repository_serialization.py`; existing merge-maintenance regressions
cover remote lifecycle and uncertain branch updates. Independent exact-commit QA,
different-model review, GitHub checks and human approval remain separate gates.

## Background PR tracking

Publication progress automatically associates a background PR job with a Kandev
tracking task. Regular board sync also reconciles retained PR jobs from before
this behavior was installed. The stable `kodi-internal:<job-id>` identity reuses
an existing tracking task after an interrupted response; ambiguous identities or
changed requirements fail for inspection rather than creating another task.
Descriptions include the PR, delivery state, next actor and repository ownership.
Tracking does not authorize recovery or satisfy QA, checks or human approval.

Disposable manual verification: retain an internal documentation job with an open
PR and no board ID. Sync twice and confirm one linked task in Human review when
its stored state is `human_review`. Queue another task in the same repository
and inspect its blocking PR/task reference. Use the normal PR lifecycle to merge
or close the disposable PR, or explicitly cancel the disposable job; sync again
and confirm terminal status and released repository ownership. Retry after a
lost task-create response and confirm the original tracking identity is reused.
Live retained jobs are reconciled by the operator's normal coordinator cycle;
local fixtures do not establish that a particular live PR has been reconciled.

## Approval-wait delivery reservations

Global merge/update ownership is separate from repository coding ownership and
historical maintenance evidence. An idle, open, non-draft PR awaiting current
exact-SHA human approval releases its global reservation only after the stored
candidate, remote branch and clean checkout match, acceptance remains valid,
auto-merge is explicitly absent, and no mutation intent is unresolved. Uncertain
observations retain ownership. CLI list and board progress show delivery ownership
and the wait reason separately. Existing lifecycle maintenance handles closed PRs
and failed CI; approval release does not replace it or release repository coding
ownership. Later approval uses normal head, base, acceptance and CI validation.

Manual verification in disposable state only: seed an older human-review job with
matching candidate/remote branch, valid acceptance, historical maintenance intent
and missing exact-SHA approval. Add an approved PR in another repository. Run a
maintenance tick and confirm the later PR progresses while the original retains
its PR, candidate, evidence and budgets. Inspect CLI list and board progress for
released delivery ownership and the approval wait reason. Restart and repeat;
then supply current approval and confirm normal validation resumes. Repeat with
armed auto-merge, missing auto-merge data, unresolved branch-update intent, remote
head drift and API failure: each must retain ownership and exclude another writer.
Use existing closed-PR/CI reconciliation for terminal outcomes. Never modify a live
reservation for these checks. Mocked tests are regression evidence only; independent
exact-commit QA, different-model source review, CI and human approval remain gates.
