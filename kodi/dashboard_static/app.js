'use strict';
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[
        c
      ],
  );
const num = (value) =>
  value == null ? 'Unknown' : new Intl.NumberFormat().format(Math.round(value));
const seconds = (value) =>
  value >= 3600
    ? `${(value / 3600).toFixed(1)}h`
    : value >= 60
      ? `${Math.round(value / 60)}m`
      : `${Math.round(value)}s`;
const state = {
  data: null,
  view: 'board',
  token: '',
  search: '',
  repo: '',
  attention: false,
  done: false,
  list: false,
  editing: null,
  detail: null,
  days: 7,
  statsRepo: '',
  provider: '',
  next: null,
  statsTasks: [],
  statsSeq: 0,
};
const views = {
  board: [
    'Delivery board',
    'Create work, follow progress, and resolve blockers.',
  ],
  stats: ['Stats & usage', 'Understand where time and model activity go.'],
  repos: [
    'Repositories',
    'Dispatch settings and work across your repositories.',
  ],
  system: [
    'System health',
    'Capacity, active workers, and delivery ownership.',
  ],
};
const groups = [
  { name: 'Backlog', kind: 'backlog', stages: ['Backlog'] },
  { name: 'Ready', kind: 'ready', stages: ['Ready'] },
  { name: 'In progress', kind: 'progress', stages: ['Coding', 'QA', 'Review'] },
  {
    name: 'Needs you',
    kind: 'attention',
    stages: ['Human review', 'Needs input'],
  },
  { name: 'Blocked', kind: 'blocked', stages: ['Blocked'] },
  { name: 'Done', kind: 'done', stages: ['Done'] },
];
const kind = (task) =>
  task.job?.state || task.stage.toLowerCase().replaceAll(' ', '_');
function notify(message) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(state.token ? { 'X-Kodi-Token': state.token } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const value = await response.json();
  if (response.status === 401) {
    if (!$('login').open) $('login').showModal();
    throw new Error('Connect with the operator token.');
  }
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}
function metric(label, value, note, icon, css = '') {
  return `<div class="metric ${css}"><div class="metric-top">${esc(label)}<span class="metric-icon">${icon}</span></div><div class="metric-value">${esc(value)}</div><div class="metric-note">${esc(note)}</div></div>`;
}
function repositoryOptions(id) {
  const select = $(id),
    old = select.value;
  select.innerHTML =
    (id === 'ticket-repo' ? '' : '<option value="">All repositories</option>') +
    state.data.repositories
      .map(
        (r) =>
          `<option value="${esc(r.key)}">${esc(r.key)}${r.enabled ? '' : ' · disabled'}</option>`,
      )
      .join('');
  select.value = old;
  if (id === 'ticket-repo' && !select.value) select.selectedIndex = 0;
}
function visibleTasks() {
  return state.data.tasks.filter(
    (t) =>
      (state.done || t.stage !== 'Done') &&
      (!state.repo || t.repo === state.repo) &&
      (!state.attention ||
        ['Blocked', 'Needs input', 'Human review'].includes(t.stage)) &&
      (!state.search ||
        `${t.title} ${t.repo} ${t.job?.id || ''}`
          .toLowerCase()
          .includes(state.search)),
  );
}
function card(task) {
  return `<button class="card ${task.stage === 'Blocked' ? 'blocked-card' : ''}" data-task="${esc(task.id)}"><div class="card-top"><span class="repo-label">${esc(task.repo || 'Unassigned')}</span><span class="ticket-id">${task.job ? '#' + task.job.id : 'DRAFT'}</span></div><h3>${esc(task.title)}</h3>${task.wait_reason ? `<div class="card-wait">${esc(task.wait_reason)}</div>` : ''}<div class="card-bottom"><span class="pill ${esc(kind(task))}">${esc(task.job?.state === 'paused' ? 'Paused' : task.stage)}</span><span class="card-meta">${task.job?.commit_sha ? esc(task.job.commit_sha.slice(0, 7)) : task.editable ? 'Editable' : 'In delivery'}</span></div></button>`;
}
function renderBoard() {
  const all = state.data.tasks,
    active = all.filter((t) => ['Coding', 'QA', 'Review'].includes(t.stage)),
    attention = all.filter((t) =>
      ['Human review', 'Needs input'].includes(t.stage),
    ),
    blocked = all.filter((t) => t.stage === 'Blocked');
  $('metrics').innerHTML =
    metric('In progress', active.length, 'Across coding, QA and review', '↗') +
    metric(
      'Needs your input',
      attention.length,
      'Approval or requirements',
      '◌',
      'attention',
    ) +
    metric(
      'Blocked',
      blocked.length,
      'Clear reasons and next actions',
      '!',
      'warning',
    ) +
    metric(
      'Ready to start',
      all.filter((t) => t.stage === 'Ready').length,
      'Subject to repository & stage capacity',
      '⌁',
    );
  $('attention-count').textContent = attention.length + blocked.length;
  const tasks = visibleTasks();
  $('board').classList.toggle('list', state.list);
  if (state.list) {
    $('board').innerHTML =
      tasks.map(card).join('') ||
      '<div class="empty-page">No tickets match these filters.</div>';
  } else
    $('board').innerHTML = groups
      .filter(
        (g) =>
          (state.done || g.kind !== 'done') &&
          (!state.attention || ['attention', 'blocked'].includes(g.kind)),
      )
      .map((g) => {
        const items = tasks.filter((t) => g.stages.includes(t.stage));
        return `<section class="lane ${g.kind}"><div class="lane-heading"><span class="lane-dot"></span><h2>${g.name}</h2><span class="count">${items.length}</span></div>${items.map(card).join('') || '<div class="empty-lane">Nothing here right now</div>'}</section>`;
      })
      .join('');
  $('board')
    .querySelectorAll('[data-task]')
    .forEach((el) => (el.onclick = () => openDetail(el.dataset.task)));
  $('refreshed').textContent =
    'Updated ' +
    new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function renderRepositories() {
  $('repository-cards').innerHTML = state.data.repositories
    .map((repo) => {
      const tasks = state.data.tasks.filter(
        (t) => t.repo === repo.key && t.stage !== 'Done',
      );
      return `<section class="panel repo-card"><span class="repo-label">REPOSITORY</span><h2>${esc(repo.key)}</h2><div class="repo-status"><span class="pill ${repo.enabled ? 'done' : 'blocked'}">${repo.enabled ? 'Dispatch enabled' : 'Dispatch disabled'}</span></div><div class="detail-fact"><span>Open tickets</span><strong>${tasks.length}</strong></div><div class="detail-fact"><span>Automatic merging</span><strong>${repo.auto_merge ? 'Enabled' : 'Manual'}</strong></div><p>${repo.enabled ? 'Ready work can be scheduled when its gates and capacity allow.' : 'An operator must authorize repository enablement before dispatch.'}</p></section>`;
    })
    .join('');
}
function renderSystem() {
  const data = state.data,
    capacity = data.capacity,
    limits = capacity?.stages || { coding: 1, qa: 1, review: 1 };
  $('system-content').innerHTML =
    '<div class="metrics">' +
    ['coding', 'qa', 'review']
      .map((phase) =>
        metric(
          phase[0].toUpperCase() + phase.slice(1) + ' slots',
          `${data.occupied.filter((r) => r.stage === phase).length} / ${limits[phase]}`,
          phase === 'coding'
            ? 'Different repositories can run concurrently'
            : 'Bounded stage admission',
          '◉',
        ),
      )
      .join('') +
    metric(
      'Active invocations',
      data.invocations.length,
      'Durable ownership; no implicit lease resets',
      '⌁',
    ) +
    '</div>' +
    `<div class="panel"><div class="panel-heading"><h2>Delivery coordination</h2><span class="pill ${data.delivery_lock ? 'review' : 'done'}">${data.delivery_lock ? 'Owned' : 'Available'}</span></div><p>${esc(data.delivery_lock?.operation || 'The exclusive delivery slot is available. PR updates and merges remain serialized.')}</p><p class="form-note">Human approval, exact-commit independent QA, model review and required GitHub checks apply to every delivery.</p></div><div class="panel"><div class="panel-heading"><h2>Worker capacity</h2></div>${data.occupied.length ? '<table><thead><tr><th>Task</th><th>Stage</th><th>Provider</th></tr></thead><tbody>' + data.occupied.map((r) => `<tr><td>#${r.job}</td><td>${esc(r.stage)}</td><td>${esc(r.provider)}</td></tr>`).join('') + '</tbody></table>' : '<p>No occupied worker claims.</p>'}<p class="form-note">Repository and provider configuration is operator-managed. This view does not restart services or grant worker permissions.</p></div>`;
}
async function loadState() {
  try {
    state.data = await api('/api/state');
    $('connection').textContent = 'Connected';
    $('demo-badge').hidden = !state.data.demo;
    ['repo-filter', 'stats-repo', 'ticket-repo'].forEach(repositoryOptions);
    renderBoard();
    renderRepositories();
    renderSystem();
  } catch (error) {
    $('connection').textContent = 'Disconnected';
    if (error.message !== 'Connect with the operator token.')
      notify(error.message);
  }
}
async function showView(view) {
  state.view = view;
  document
    .querySelectorAll('.nav')
    .forEach((el) => el.classList.toggle('active', el.dataset.view === view));
  Object.keys(views).forEach((key) => ($(key + '-view').hidden = key !== view));
  $('page-title').textContent = views[view][0];
  $('page-description').textContent = views[view][1];
  $('crumb').textContent = views[view][0];
  $('new-task').hidden = view !== 'board';
  if (view === 'stats') await loadStats();
}
function openEditor(task = null) {
  state.editing = task?.id || null;
  $('ticket-form').reset();
  $('form-error').textContent = '';
  $('editor-title').textContent = task ? 'Edit ticket' : 'Create a ticket';
  $('ticket-repo').disabled = Boolean(task?.job);
  if (task) {
    $('ticket-title').value = task.title;
    $('ticket-repo').value = task.repo;
    const prefix = `Repo: ${task.repo}\n\n`;
    $('ticket-description').value = task.description.startsWith(prefix)
      ? task.description.slice(prefix.length)
      : task.description;
  } else if (state.repo) $('ticket-repo').value = state.repo;
  $('editor').showModal();
  $('ticket-title').focus();
}
function closeEditor() {
  $('editor').close();
  state.editing = null;
  $('ticket-form').reset();
  $('ticket-repo').disabled = false;
}
function safeGithub(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && parsed.hostname === 'github.com'
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}
async function openDetail(id) {
  const task = state.data.tasks.find((t) => t.id === id);
  if (!task) return;
  state.detail = id;
  $('detail-label').textContent = task.job
    ? 'TICKET #' + task.job.id
    : 'BACKLOG DRAFT';
  $('detail-content').innerHTML =
    `<div class="detail-tags"><span class="repo-label">${esc(task.repo)}</span><span class="pill ${esc(kind(task))}">${esc(task.job?.state || task.stage)}</span></div><h2>${esc(task.title)}</h2>${task.wait_reason ? `<div class="notice">${esc(task.wait_reason)}</div>` : ''}<div id="detail-error" class="form-error" role="alert"></div><div class="detail-actions">${task.editable ? '<button class="button" id="edit-ticket">Edit ticket</button>' : ''}${!task.job ? '<button class="button primary" id="ready-ticket">Move to Ready</button><button class="button subtle" id="backlog-ticket">Backlog</button>' : ''}${safeGithub(task.job?.pr_url) ? `<a class="button primary" href="${esc(safeGithub(task.job.pr_url))}" target="_blank" rel="noopener noreferrer">Review PR ↗</a>` : ''}</div><h3>Goal & acceptance criteria</h3><div class="detail-description">${esc(task.description)}</div>${task.job ? `<div class="detail-fact"><span>Commit</span><code>${esc(task.job.commit_sha || 'No candidate yet')}</code></div><div class="detail-fact"><span>Coding attempts</span><strong>${task.job.attempt}</strong></div><div id="job-detail"><p>Loading evidence…</p></div>` : ''}`;
  if (!$('detail').open) $('detail').showModal();
  if ($('edit-ticket'))
    $('edit-ticket').onclick = () => {
      $('detail').close();
      openEditor(task);
    };
  for (const [button, stage] of [
    ['ready-ticket', 'Ready'],
    ['backlog-ticket', 'Backlog'],
  ])
    if ($(button))
      $(button).onclick = async () => {
        try {
          await api(`/api/tasks/${id}/stage`, { stage });
          $('detail').close();
          await loadState();
        } catch (e) {
          $('detail-error').textContent = e.message;
        }
      };
  if (task.job) {
    try {
      const detail = await api('/api/jobs/' + task.job.id);
      if (state.detail !== id) return;
      const controls = detail.control;
      $('job-detail').innerHTML = `<div class="detail-actions">${[
        ['pause', 'Pause', 'can_pause'],
        ['resume', 'Resume', 'can_resume'],
        ['prioritize', 'Prioritize', 'can_prioritize'],
      ]
        .filter((row) => controls[row[2]])
        .map(
          (row) =>
            `<button class="button" data-action="${row[0]}">${row[1]}</button>`,
        )
        .join(
          '',
        )}</div>${controls.pause_pending ? '<p class="form-note">Pause requested; waiting for a safe checkpoint.</p>' : ''}${controls.resume_blocker ? `<p class="form-note">${esc(controls.resume_blocker)}</p>` : ''}<h3>Observed time</h3>${Object.entries(
        detail.timing.seconds || {},
      )
        .map(
          ([key, value]) =>
            `<div class="detail-fact"><span>${esc(key)}</span><strong>${seconds(value)}</strong></div>`,
        )
        .join(
          '',
        )}<p class="form-note">${detail.timing.history_partial ? 'Historical timing is partial. Unknown intervals are not estimated.' : 'Recorded delivery intervals.'}</p><h3>Provider usage</h3>${
        Object.entries(detail.usage)
          .map(
            ([key, value]) =>
              `<div class="detail-fact"><span>${esc(key)}</span><strong>${num(value.total_tokens)} tokens · ${value.run_count} runs</strong></div>`,
          )
          .join('') || '<p>No model usage yet.</p>'
      }<h3>Recent evidence</h3>${
        detail.events
          .map(
            (e) =>
              `<div class="event"><strong>${esc(e.kind.replaceAll('_', ' '))}</strong><small>${esc(new Date(e.created).toLocaleString())}${e.sha ? ' · ' + esc(e.sha.slice(0, 7)) : ''}</small>${Object.entries(
                e.result,
              )
                .map(
                  ([key, value]) =>
                    `<p>${esc(key.replaceAll('_', ' '))}: ${esc(value)}</p>`,
                )
                .join('')}</div>`,
          )
          .join('') || '<p>No evidence recorded.</p>'
      }`;
      $('job-detail')
        .querySelectorAll('[data-action]')
        .forEach(
          (button) =>
            (button.onclick = async () => {
              button.disabled = true;
              try {
                await api(`/api/jobs/${task.job.id}/control`, {
                  action: button.dataset.action,
                  digest: controls.digest,
                });
                await loadState();
                await openDetail(id);
              } catch (e) {
                await openDetail(id);
                $('detail-error').textContent = e.message;
              }
            }),
        );
    } catch (e) {
      $('job-detail').textContent = e.message;
    }
  }
}
async function loadStats(more = false) {
  const sequence = ++state.statsSeq;
  if (!more) {
    state.next = null;
    $('more-stats').hidden = true;
  }
  try {
    const q = new URLSearchParams({
      days: state.days,
      offset: more ? state.next || 0 : 0,
    });
    if (state.statsRepo) q.set('repo', state.statsRepo);
    if (state.provider) q.set('provider', state.provider);
    const data = await api('/api/stats?' + q);
    if (sequence !== state.statsSeq) return;
    state.statsTasks = more ? state.statsTasks.concat(data.tasks) : data.tasks;
    state.next = data.next_offset;
    const providers = Object.entries(data.providers);
    const known =
        data.known_tokens ??
        providers.reduce(
          (sum, [, p]) => sum + (p.input_tokens || 0) + (p.output_tokens || 0),
          0,
        ),
      unknown =
        data.unknown_runs ??
        providers.filter(([, p]) => p.total_tokens == null).length;
    $('stats-metrics').innerHTML =
      metric(
        'Known tokens',
        num(known),
        unknown
          ? `${unknown} runs have incomplete token counts`
          : 'Input + output; measured usage only',
        '⌁',
      ) +
      metric(
        'Recorded runs',
        num(data.runs),
        'Includes failed and repair runs',
        '↗',
      ) +
      metric(
        'QA runs',
        num(data.qa_runs),
        'Completed recorded QA observations',
        '✓',
      ) +
      metric(
        'Tasks with activity',
        num(data.task_count),
        'Membership is filtered before pagination',
        '▦',
      );
    const days = data.daily,
      max = Math.max(
        1,
        ...days.map((day) =>
          Object.values(day.providers).reduce((sum, p) => sum + p.run_count, 0),
        ),
      ),
      w = 900,
      h = 130,
      gap = days.length ? Math.min(22, (w / days.length) * 0.3) : 0,
      bar = days.length ? (w - gap * (days.length - 1)) / days.length : 0;
    $('chart').innerHTML = days.length
      ? `<svg viewBox="0 0 940 165" role="img" aria-label="Model runs per day in UTC"><line x1="10" y1="140" x2="920" y2="140" stroke="#e3e9df"/>${days
          .map((day, i) => {
            const runs = Object.values(day.providers).reduce(
                (sum, p) => sum + p.run_count,
                0,
              ),
              height = (runs / max) * h;
            return `<rect class="chart-bar" x="${10 + i * (bar + gap)}" y="${140 - height}" width="${bar}" height="${height}" rx="4"><title>${esc(day.date)}: ${runs} runs</title></rect>${days.length < 15 || i % 5 === 0 ? `<text class="chart-label" x="${10 + i * (bar + gap) + bar / 2}" y="159" text-anchor="middle">${esc(day.date.slice(5))}</text>` : ''}`;
          })
          .join('')}</svg>`
      : '<div class="empty-page">No model runs in this period.</div>';
    $('provider-table').innerHTML =
      '<table><thead><tr><th>Provider</th><th>Runs</th><th>Input tokens</th><th>Output tokens</th><th>Runtime</th></tr></thead><tbody>' +
      providers
        .map(
          ([key, p]) =>
            `<tr><td>${esc(key)}</td><td>${num(p.run_count)}</td><td>${num(p.input_tokens)}</td><td>${num(p.output_tokens)}</td><td>${seconds(p.seconds)}</td></tr>`,
        )
        .join('') +
      '</tbody></table>';
    $('activity-table').innerHTML = state.statsTasks.length
      ? '<table><thead><tr><th>Task</th><th>Repository</th><th>Status</th><th>QA</th><th>Reviews</th></tr></thead><tbody>' +
        state.statsTasks
          .map(
            (t) =>
              `<tr><td><button class="table-link" data-activity-job="${t.id}">#${t.id} · ${esc(t.title)}</button></td><td>${esc(t.repo)}</td><td><span class="pill ${esc(t.state)}">${esc(t.state.replaceAll('_', ' '))}</span></td><td>${t.qa_runs}</td><td>${t.review_iterations}</td></tr>`,
          )
          .join('') +
        '</tbody></table>'
      : '<p>No tasks have matching activity.</p>';
    $('activity-table')
      .querySelectorAll('[data-activity-job]')
      .forEach(
        (button) =>
          (button.onclick = () => {
            const task = state.data.tasks.find(
              (t) => t.job?.id === Number(button.dataset.activityJob),
            );
            if (task) openDetail(task.id);
            else
              notify(
                'The original task is absent from this board snapshot; its activity remains in the ledger.',
              );
          }),
      );
    $('more-stats').hidden = state.next == null;
  } catch (e) {
    notify(e.message);
  }
}
$('new-task').onclick = () => openEditor();
$('close-editor').onclick = $('cancel-editor').onclick = closeEditor;
$('close-detail').onclick = () => {
  $('detail').close();
  state.detail = null;
};
$('ticket-form').onsubmit = async (event) => {
  event.preventDefault();
  $('form-error').textContent = '';
  $('save-ticket').disabled = true;
  try {
    await api(state.editing ? '/api/tasks/' + state.editing : '/api/tasks', {
      title: $('ticket-title').value,
      repo: $('ticket-repo').value,
      description: $('ticket-description').value,
    });
    closeEditor();
    await loadState();
    notify('Ticket saved.');
  } catch (e) {
    $('form-error').textContent = e.message;
  } finally {
    $('save-ticket').disabled = false;
  }
};
$('editor').addEventListener('cancel', () => {
  state.editing = null;
  $('ticket-form').reset();
});
$('login-form').onsubmit = async (event) => {
  event.preventDefault();
  state.token = $('operator-token').value;
  try {
    await api('/api/state');
    $('operator-token').value = '';
    $('login').close();
    await loadState();
  } catch (e) {
    state.token = '';
    $('login-error').textContent = e.message;
  }
};
document
  .querySelectorAll('.nav')
  .forEach((button) => (button.onclick = () => showView(button.dataset.view)));
$('refresh').onclick = async () => {
  await loadState();
  if (state.view === 'stats') await loadStats();
};
$('search').oninput = (e) => {
  state.search = e.target.value.toLowerCase();
  renderBoard();
};
$('repo-filter').onchange = (e) => {
  state.repo = e.target.value;
  renderBoard();
};
$('show-done').onchange = (e) => {
  state.done = e.target.checked;
  renderBoard();
};
$('focus-all').onclick = () => {
  state.attention = false;
  $('focus-all').classList.add('selected');
  $('focus-attention').classList.remove('selected');
  renderBoard();
};
$('focus-attention').onclick = () => {
  state.attention = true;
  $('focus-attention').classList.add('selected');
  $('focus-all').classList.remove('selected');
  renderBoard();
};
$('layout').onclick = () => {
  state.list = !state.list;
  $('layout').textContent = state.list ? 'Board view' : 'List view';
  renderBoard();
};
$('ranges')
  .querySelectorAll('button')
  .forEach(
    (button) =>
      (button.onclick = () => {
        state.days = Number(button.dataset.days);
        $('ranges')
          .querySelectorAll('button')
          .forEach((b) => b.classList.toggle('selected', b === button));
        loadStats();
      }),
  );
$('stats-repo').onchange = (e) => {
  state.statsRepo = e.target.value;
  loadStats();
};
$('stats-provider').onchange = (e) => {
  state.provider = e.target.value;
  loadStats();
};
$('more-stats').onclick = () => loadStats(true);
loadState();
setInterval(() => {
  if (!$('login').open && !$('editor').open) loadState();
}, 8000);
