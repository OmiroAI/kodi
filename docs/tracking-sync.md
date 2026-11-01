# Tracking sync verification

Use disposable state and a fictional board; no runtime service changes are needed.
Link retained jobs to tasks, including done/cancelled and human-review jobs. Sync
once to reconcile their descriptions and columns. Repeat sync twice while counting
board requests: expect one task-list read per cycle, no description PATCH requests
and no moves for unchanged tasks. Confirm ticket timestamps/activity stay unchanged.
Change a retained terminal job's error or usage and sync: expect its correction
once, then no further writes. Edit the task's authoritative requirements and
confirm progress refuses to overwrite them. Simulate a failed PATCH, retry, and
restart: the next snapshot must reconcile the actual board content. Simulate a
lost create response and verify the stable internal identity reuses the task.

For active coding/QA/review, advance the clock less than a minute within a timing
bucket: timing alone should not write. Cross a minute boundary and confirm timing
refreshes; change usage or state within the same bucket and confirm immediate
propagation. Waiting/terminal jobs display retained interval totals rather than
a running clock. Restart can legitimately report interrupted open intervals.

The mocked 199-job regression (191 terminal, eight waiting) measures two idle
cycles: two task-list reads, zero PATCH requests, zero moves and zero creates.
Previously those cycles required 796 task-list reads, 398 unconditional PATCH
requests and 398 moves for linked internal tasks: 1592 operations versus two
(99.87% fewer). The outer intake loop previously added one list read per cycle;
it now shares the snapshot. These counts exclude unrelated issue/intake APIs.
They measure local adapter calls, not live dashboard load or independent QA.

Run `python3 -m unittest discover -s tests -v`. Independent exact-candidate QA,
different-model source review, GitHub checks and human merge approval remain
separate delivery gates.
