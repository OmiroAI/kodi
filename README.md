# Kodi

A reusable, operator-controlled development coordinator:

GitHub issue → isolated implementation → independent offline QA → different-model
review → pull request → GitHub CI → human approval → protected merge.

Kodi works with an explicit repository catalog. Its runtime configuration,
credentials, infrastructure inventory and application deployment integrations
belong outside this repository. The coordinator code is MIT licensed; the
[Kandev](https://github.com/kdlbs/kandev) dashboard is deployed separately under
its own AGPL-3.0 license and is not vendored here.

## Capabilities

- Sandboxed coding and review workers with bounded time, memory and network access.
- Independent QA on a separate operator-managed host with networking disabled.
- Complete-branch review and acceptance evidence bound to exact commit SHAs.
- Serialized, protected merging only for explicitly enabled repositories.
- Intake for PRs authored directly in a development workspace, retaining their
  branch and PR number while running the same independent acceptance gates.
- Bounded transport retry, isolated rebase repair and preserved recovery artifacts.
- Durable GitHub review requests and notifications after validated updates.
- Owner-only Telegram PR approval and text/voice ticket drafts with a self-hosted
  Whisper-compatible transcription endpoint.
- An existing task dashboard rather than another custom UI or agent framework.

Models cannot approve, merge, deploy, mark their own QA passed, edit workflow
permissions or grant themselves access. Workers receive no publisher, owner,
Telegram or production credentials. Runtime Git metadata is mounted read-only.

## Start here

1. Configure your GitHub App, owner reviewer and repository catalog using
   [the examples](examples/config.json).
2. Build a generic worker and QA image; add project dependencies only in a
   separate, privately configured QA profile/image.
3. Follow [operations](docs/operations.md) for installation, account setup,
   dashboard registration and a disposable acceptance pilot.
4. Add `kodi:ready` to a GitHub issue, or use an explicit `Repo: project` line
   in a dashboard draft. Only configured, enabled repositories can run.

```sh
python3 -m kodi.cli --config /private/path/config.json doctor
python3 -m kodi.cli --config /private/path/config.json list
python3 -m kodi.cli --config /private/path/config.json tick
python3 -m unittest discover -s tests -v
```

The historical default config location is `~/.config/omiro-dev/config.json`;
`--config` selects any operator-owned location. Image tags and state directory
names in the supplied service examples are defaults, not a required topology.

See [shared workspaces](docs/shared-workspace.md) for development clones,
repository onboarding and reviewed context learning.

## Telegram and usage

See [Telegram control](docs/telegram-control.md) for owner-only approval buttons,
`/prs`, `/approve project 42`, ticket confirmation, voice transcription and replay
handling. Approval respects every existing QA, CI and repository merge gate.

`python3 -m kodi.cli usage --summary` groups reported usage by provider.
Use `usage --daily` for UTC daily totals and `usage --tasks` for task totals,
coding attempts, completed review iterations and QA runs. Task descriptions in
Kandev show the same provider totals and iteration counts. Missing
counts remain unknown; subscription tokens are not converted into invented API
charges.

On a dashboard supporting external accounting, Kodi also sends completed worker,
review and QA observations into the existing Stats page. A durable outbox freezes
row-based event identities and retries without double counting. Existing ledgers
backfill in bounded batches; missing task mappings wait, and validation conflicts
are quarantined. Dashboard failures do not change job acceptance or delivery state.
Historical phase/outcome and unsupported measurements remain unknown.

Use `python3 -m kodi.cli sync-usage --status` to inspect local delivery counts and
`python3 -m kodi.cli sync-usage --limit 100` to run a bounded backfill/retry pass.
For automatic export, install `deploy/kodi-usage.service` and
`deploy/kodi-usage.timer` as user units, adjust the service working directory to
the installed coordinator, and enable `kodi-usage.timer`. Its separate process
keeps accounting HTTP outside task delivery and progress updates. Older dashboards are probed
with a five-minute failure cooldown; local usage reports and task descriptions
remain available. Quarantined records require operator investigation; do not
rewrite an acknowledged payload or copy a ledger instance into an independent
coordinator. Runtime ledger backups must retain the outbox and instance identity.

Provider credentials and optional local-reviewer endpoints are operator
configuration. No paid fallback is enabled automatically.

## Public-source boundary

This tree is intended to be reusable public source, not an operator runbook for
a specific company or product. Use fictional examples and keep private operations
outside Git. Run `python3 scripts/check_public_source.py` before committing.

A sanitized tree does not sanitize old Git history or GitHub discussions. Follow
[the release guide](docs/public-release.md) to prepare a history-free export.
Repository publication and visibility changes require an explicit owner decision.
