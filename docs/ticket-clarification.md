# Ticket clarification

Optional Luna intake turns a submitted request into a concise coding ticket.
It uses the existing Codex worker image, dedicated Codex login and restricted
proxy. There is no new inference service or reviewer selection change.

## Operator configuration

`clarification` in the host-owned config accepts:

```json
{
  "enabled": false,
  "model": "gpt-6-luna",
  "effort": "low",
  "timeout": 45,
  "context_chars": 20000
}
```

The default is disabled. Enable only after deploying compatible coordinator
code and adding a **Needs input** step to the existing delivery workflow. Record
its existing ID in `board.steps["Needs input"]`; new bootstrap installations
include it. Do not recreate the workspace or workflow. An optional
`board.repository_ids` map from catalog key to registered KanDev repository ID
attaches selected repositories to new issue mirrors, keeping native editing
available without an agent profile. This does not change catalog routing.

The allowed model is explicitly Luna; effort is low, medium or high. Timeout
must be an integer from 5 to 120 seconds; context budget from 1,000 to 40,000
characters. Invalid configuration fails before new task intake. Requirements
and bounded repository context must fit; oversized input is blocked rather than
silently truncated. Model availability and credentials remain operator-owned.

## Submission and answers

- Telegram keeps repository choice and **Create ticket** confirmation. Text and
  voice drafts do not submit themselves. After confirmation, the existing issue
  intake creates its usual KanDev task and runs clarification before coding.
- KanDev keeps **Backlog** drafts and **Ready** submission. Moving to Ready runs
  clarification; editing a Backlog draft does not start a worker.
- Clear requests become concise tickets and proceed without questions.
- When essentials are missing, the same task moves to **Needs input**. Its
  description retains the original request, proposed ticket and question. If
  Telegram control is enabled, Kodi sends the question with a reply prompt.
- Reply directly to that Telegram message with a text answer. Existing private
  chat/user authorization applies. The reply is saved as data on that task;
  command-looking answer text is never executed as a command or approval.
- Alternatively, append the answer to the KanDev description and move that same
  task to **Ready**. Moving to Ready without changing the description returns it
  to Needs input without another model call.
- Kodi rechecks the accumulated conversation. It asks again only for remaining
  essentials; coding begins only after an actionable result.

Each round retains the input hash, repository base SHA, result and answer in
the coordinator database. Notification mapping is durable across restarts.
Duplicate/stale replies never overwrite an answer or create a replacement task.
Already-running, completed, cancelled and legacy tasks are not reprocessed.
Turning the feature off preserves old intake behavior and does not release
tasks already waiting for answers. Worker/configuration failures become visible
Blocked tasks; an explicit Ready move permits retry after operator correction.

## Isolation and accounting

The source and Git metadata mounts are explicitly read-only. Each invocation
gets a fresh output directory; host logs sit outside that worker-writable mount.
Returned JSON is opened relative to the output directory with no-follow and
nonblocking flags, checked as an ordinary single-link file and limited to 16 KiB.
The strict result contains only status, title, description and question. It
cannot choose repositories, change workflow policy or authorize operations.

Usage is recorded with phase `clarification`, separate from coding attempts,
review verdicts and QA records. Existing dashboard export represents this new
phase as `unknown` until a dashboard schema supports it, retaining token counts
without claiming it was coding or a review iteration. No acceptance gate changes.

## Disposable pilot

Run the unit suite and public-source check first. With an operator-authorized
worker login, point intake at a disposable committed fixture, not production:

1. Submit “Make substring search case-insensitive; add a mixed-case regression;
   preserve other matching behavior.” Expect actionable and no question.
2. Submit “Add notifications.” Expect Needs input and a concise essentials question.
3. Answer through a disposable Telegram chat. Confirm the original task ID is
   retained, then verify an equivalent KanDev description answer plus Ready.
4. Repeat the reply and restart between rounds. Check no duplicate job or lost
   answer. Test a provider timeout; coding attempts must remain zero.
5. Compare elapsed time, recorded usage and question quality. Do not make live
   model calls or Telegram delivery dependencies of unit tests.

Use a separate fixture state/config and explicit test chat; do not submit pilot
tasks into the real delivery queue. Deploy/activation and any service restart
remain separate operator operations under workspace permission rules.
