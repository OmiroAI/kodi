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
