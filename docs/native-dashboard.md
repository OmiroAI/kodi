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

## Discard and restore

Open any ticket and choose **Discard ticket**. **Show discarded** reveals the
Discarded lane (also in list view); open the retained ticket and choose **Restore
ticket**. Discard and restore retain descriptions, candidate/branch identity,
attempt budgets, evidence, history and linked PRs. Discarded tickets cannot be
edited or moved to Ready. Drafts restore to their previous stage.

Discard fences active workers immediately without claiming that remote work has
stopped. Restore refuses running or uncertain execution/capacity; use the existing
host job-control inspection and scoped remote-completion reconciliation after
independently verifying completion. Restored QA/review checkpoints require explicit
Resume. Interrupted coding/publication/base updates restore as Blocked for normal
retained-candidate recovery. Acceptance and human approval gates remain unchanged.

Existing auto-merge requests are disabled and queued PRs removed before restoration.
If remote cancellation cannot be verified, the ticket stays discarded with an
explicit error; **Retry merge cancellation** retries verification. Already completed
remote merges cannot be undone. Unresolved mutation reservations remain serialized
and require the existing merge-intent recovery rather than being silently released.

Manual verification in a disposable queue: discard a draft, Ready task, active
worker and PR-review ticket. Check default hiding, Show discarded and list view;
restore and compare the content, evidence and PR link. Confirm a discarded Ready
task never starts, a stale worker cannot advance, and uncertain execution prevents
restoration. Restore QA/review and explicitly Resume; verify the retained candidate
and unchanged attempts. Simulate remote API failure and confirm the visible retry
error and blocked restore, then retry with absent remote merge/queue requests.
