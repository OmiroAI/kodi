# Native Kodi dashboard

Opt in with `board_backend: "native"`; Kandev stays the default. The buildless
Python/HTML/CSS/JS UI uses Kodi's ledger, scheduler and acceptance gates. Drafts,
filters, blockers, evidence, capacity and usage are native views. Scope edits are audited; active work needs a follow-up.

## Preview

`python3 -m kodi.cli dashboard --demo` creates a disposable fictional ledger,
reads no operator config and starts no workers. Loopback is the default; select a
private address with `--host`.

## Activation

Back up config/ledger and inspect task identities. After separately authorized
maintenance and worker completion, `dashboard-import` previews the snapshot;
`dashboard-import --apply` preserves IDs/jobs/usage and refuses active claims or
divergent native work. It never switches or stops services. Select the native
backend with the same `state_dir` and catalog.

Set `KODI_DASHBOARD_TOKEN` outside Git (32+ characters), then configure
`dashboard: {"enabled": true, "host": "127.0.0.1", "port": 8810}` to embed in `serve`.
Use private TLS/authenticated access. Service changes need separate approval.

APIs require `X-Kodi-Token` or Bearer auth. Browser tokens stay in tab memory.
Cross-origin writes fail; static routes are allowlisted. Controls cannot invent
acceptance, grant permissions, or reset recovery budgets.

Verification: run unittests, the privacy checker, and desktop/mobile draft,
filter, checkpoint and stats checks. Human UI approval remains separate.
