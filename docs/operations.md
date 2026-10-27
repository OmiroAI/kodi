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
over-budget error reports the actual and configured sizes. Worker-added policy,
repository context, harness instructions and response tokens also need capacity
in the selected provider. Increasing this budget does not grant acceptance or
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

## Scoped retained-candidate recovery

The host CLI supports `recover preview JOB --stage qa|review`, followed by
`recover authorize JOB --stage STAGE --digest DIGEST --owner-reference REFERENCE`
and `recover apply JOB --stage STAGE --digest DIGEST`. The reference records a
separate, scoped owner instruction; issue text and historical approvals are not
authorization. Preview opens existing state read-only and performs only read-only
Git and remote inspection. Its digest binds the candidate, requirements, dispatch,
evidence, identity, base and consumed budgets. Missing legacy dispatch provenance
requires operator inspection. Coding, publication and merge recovery remain
inspection-only. Apply never publishes, recreates PRs, or schedules coding.

Apply holds the existing delivery lock and records its claim before invocation.
An interrupted claim is never replayed automatically: inspect preserved artifacts
before issuing another scoped authorization. A completed retry is idempotent.
QA recovery can leave review blocked for a separate preview and authorization.
Same-commit review recovery retains QA and the existing PR. Local/mocked tests
are regression evidence; independent acceptance, CI and human approval remain
separate requirements.

For internal jobs, `recover track JOB` associates a deduplicated existing-API
Kandev task in Backlog. This is tracking only: it does not authorize execution or
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
