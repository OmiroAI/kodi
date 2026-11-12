# Native Kodi dashboard

Kodi can own its operator UI as well as its delivery ledger. The native backend
is opt-in; the existing Kandev adapter remains the default. This implementation
is independent of Kandev's UI and uses Python's standard library plus buildless
HTML, CSS and JavaScript. Workers, acceptance gates and merge authority stay in
the coordinator. Native views read the ledger directly; formatted runtime status
is not copied back into editable ticket requirements.

## Experience

- Delivery board and list, repository/search filters, attention view and task
  details with blockers, exact candidate, evidence, usage and observed timing.
- Persistent independent ticket drafts; move drafts to Ready for the existing
  scheduler. Drafts and queued work with no execution evidence can be edited.
  Scope revisions are audited; edits to active/accepted work require a follow-up.
- Pause, resume and prioritize through the existing digest-bound job controls.
  Interrupted coding still needs inspected recovery. There is no dashboard API
  for inventing QA, approval, merge or terminal outcomes.
- Repository enablement, auto-merge flags, stage/provider capacity and exclusive
  delivery ownership are visible. Configuration remains operator-managed.
- Stats reads the existing usage and evidence ledger directly. Last 7/30 days
  use UTC occurrence timestamps with an exclusive end boundary; task membership
  is filtered before pagination. Zero, unknown, QA-only and review-only activity
  remains visible. Known tokens and incomplete counts are reported separately;
  subscription tokens are never converted into invented API prices.

PR approval opens the existing GitHub human review flow. No publisher or human
GitHub credentials enter the browser or workers. The native board disables
agent/session/autopilot startup and does not add another agent runtime.

## Isolated demonstration

```sh
python3 -m kodi.cli dashboard --demo
```

This creates a disposable fictional ledger, starts no coordinator or workers,
and never reads the operator configuration. The interface is explicitly marked
as sample data. It binds to loopback unless an operator selects another address.

## Explicit migration

1. Back up the operator ledger/config and inspect existing task identities. Plan
   a coordinator maintenance window and let workers finish; do not reset claims.
2. With the original adapter selected, run `python3 -m kodi.cli dashboard-import`
   to validate and preview the source task snapshot. This does not change jobs,
   tasks or the native schema.
3. After separately authorized coordinator maintenance, use `dashboard-import
   --apply`. Apply refuses active invocations/claims and existing divergent native
   work. The same snapshot is idempotent. It copies task IDs, stages, descriptions
   and associations; it does not delete or modify the source dashboard or copy
   the job/usage ledger into a competing database.
4. Set `board_backend` to `native` in the operator configuration, keeping the same
   `state_dir` and repository catalog. Imported workflow/repository identities
   are reused. Activate through the existing operator-managed service workflow.
5. Generate a strong operator token outside Git, provide it as
   `KODI_DASHBOARD_TOKEN` (at least 32 characters), and set
   `dashboard: {"enabled": true, "host": "127.0.0.1", "port": 8810}`. The existing
   `serve` process then hosts both the coordinator and UI, sharing the same
   effective configuration. For standalone inspection, `python3 -m kodi.cli
   dashboard` uses the same module; choose one listener per address. Use an
   operator-managed private TLS/authenticated access path. Network/proxy/service
   changes need their own operator authorization.

All API reads and writes require the operator token (`Authorization: Bearer`
or `X-Kodi-Token`). The browser uses the latter to coexist with proxy HTTP auth. It is held only in the
browser tab's memory, with no cookies, CORS grant or persistent browser storage.
Cross-origin writes are rejected. Static routes are explicitly allowlisted;
HTTP responses expose neither repository files nor raw worker/provider logs.

Existing job/evidence/usage records and acceptance remain authoritative. A job
whose original task was missing from the source snapshot remains in accounting;
inspect its source identity before controlling or dispatching it. Native Stats
covers Kodi observations, not unrecorded historical native-agent sessions from
another application. The legacy usage export is unnecessary for native Stats;
no exporter or existing service is stopped automatically.

## Verification

Run the complete coordinator unittest suite and public-source checker. Native
regressions cover scheduler intake, independent create/edit/reload behavior,
pre-execution revision audit, active-edit rejection, checkpoint/budget retention,
repository and acceptance gates, import identity/idempotency, authentication,
cross-origin/path boundaries, isolated demo setup and activity filtering.

Browser verification should exercise a desktop and narrow mobile viewport,
create/edit/create/reload isolation, repository/search/attention filters, draft
Ready transitions, pause/resume, stats filtering and evidence details. A demo
proves UI behavior, not live provider execution or production migration.
