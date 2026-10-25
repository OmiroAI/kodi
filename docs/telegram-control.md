# Owner Telegram control

Kodi's existing bot can accept tickets and human PR approvals in its configured
private owner chat. It runs a long-polling adapter thread inside the existing
coordinator process; there is no new agent framework, web server or public port.

## Use it

- Send text such as `project: Make the volume menu clearer`. Select the repository
  if omitted, inspect the draft, and tap **Create ticket**.
- Send a voice note. Kodi downloads it into memory, transcribes it on the
  configured self-hosted Whisper endpoint, and shows the transcript as a
  ticket draft. It never interprets a transcript as an approval command.
- Use `/drafts` to recover unfinished drafts. Changing repository invalidates
  older Create buttons. Cancel a draft and send revised text to correct it.
- Use `/prs` for current ready PRs and their summaries, manual checks, exact
  commit and **Approve** button. Ready notifications also include this button.
- Use `/approve project 42` to approve the current verified commit directly.
  Repository aliases accept both catalog keys and GitHub repository names.
- `/help` shows the available commands.

Confirming a ticket creates a GitHub issue and adds `kodi:ready`, using the
existing App publisher and queue. It appears on the board during the next sync.
Only enabled, operator-configured repositories can be targeted.

## Approval identity and boundaries

Only explicitly allowlisted Telegram user IDs in the configured private chat
can submit messages/actions. The single configured approver user may approve.
Forwarded messages, bot messages, other chats and other senders are ignored.
Approval callbacks contain opaque, owner-bound, expiring action IDs. They bind
an immutable task/commit pair; changed PRs must be approved through a fresh card.

Approval requires a Kodi-tracked task in human_review, matching open/non-draft
PR head, exact-commit independent QA/model review and passing GitHub checks.
The host's existing GitHub CLI owner login must match `github_approver`; that
human identity submits an APPROVE review with an explicit commit_id and audit
action ID. The App does not approve its own PRs. Telegram credentials, the owner
login and publisher credentials are never mounted into model workers.

When control is enabled, the coordinator additionally requires an approved
review by a configured human on the exact commit SHA. A review of an older
commit cannot authorize a newer head, including a concurrent update during an
approval request. Existing branch protection, required checks and per-repository
auto_merge policy remain. Approval does not enable auto-merge on a repository.

## Delivery and recovery

Private `state_dir/telegram.sqlite` stores drafts, expiring approval actions,
update offset and outcomes. Replayed updates cannot create duplicate tickets.
An interrupted issue creation is reconciled through its unique body marker;
uncertain creation with no matching issue is not blindly repeated. Replayed
approval actions first check existing same-SHA human reviews. Unknown outcomes
require checking GitHub or obtaining a new card, rather than carrying approval
to another commit. The bot consumes notification-only historical updates on its
first activation without interpreting them as commands. Offsets persist on restart.
Only one poller can hold `state_dir/telegram.lock` at a time. An existing webhook
is rejected rather than removed. Model execution can continue while the adapter
handles Telegram, with separate SQLite connections and immutable approval targets.

Voice input defaults to five minutes and 10 MiB. Audio remains in memory and is
not retained by Kodi. The private ticket draft contains its transcript. Whisper
has no external API fallback; a failed transcription asks the owner to retry or
send text. HTTP Whisper endpoints should be inside the trusted encrypted mesh;
HTTPS uses ordinary certificate verification. Ticket previews show up to 3,100
characters; longer drafts explicitly say that the full text will be used.

## Operator setup

Use an existing dedicated notification bot and a private chat belonging to the
owner. Do not copy credentials into repository files, tickets or worker mounts.
The configured human must be a repository reviewer and have review access through
the host's normal GitHub CLI login. The App remains the only publisher identity.

```sh
python3 scripts/setup_telegram_control.py --config /path/to/config.json \
  --output /path/to/config.telegram-staged.json --github-approver owner-login \
  --whisper-url http://whisper-host:8008/v1/audio/transcriptions
```

The setup helper verifies the private chat, existing webhook state, host GitHub
identity and repository reviewer configuration. It writes a separate owner-only
configuration file without starting polling. Verify the Whisper endpoint with a
known non-private sample. After human merge approval, install the approved code,
atomically activate the staged config, and restart the coordinator only when no
job is running. Send `/start`, then a text draft and a voice note. Confirm that
no issue is created until the Create button is pressed. Test a ready PR approval
and verify GitHub records the configured human on the displayed commit. Verify an
old card cannot approve an updated PR. Keep normal protected merge requirements.
