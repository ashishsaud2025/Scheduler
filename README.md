# Command Scheduler

Schedule any shell command for a future date and time, then close the window.
A small background service runs the due commands and keeps their output.

- **app.py**: desktop window with calendar, schedule, logs and service controls
- **daemon.py**: background service that actually runs due commands
- **scheduler_core.py**: storage, scheduling rules and execution
- **ui_theme.py**: colour tokens and the Qt stylesheet

Jobs live in `%APPDATA%\CmdScheduler\jobs.json`, output in
`%APPDATA%\CmdScheduler\logs\`, on Linux and macOS under
`~/.config/CmdScheduler/`. The same folder also holds `daemon.json`
(service heartbeat), `daemon.log`, `daemon.stop`, `jobs.lock` and
`app-errors.log`. Use **Open data folder** in the app to jump there.

## Setup

Needs Python 3.11 or newer.

```bat
pip install -r requirements.txt
run_app.bat
```

`run_app.bat` checks for Python, installs PySide6 on first run
(`requirements.txt` pins `PySide6>=6.6,<7`), then opens the window without a
console. Tests need pytest as well, which is not in requirements:
`pip install pytest`, then `python -m pytest tests -q`.

## Using it

1. Pick a day on the calendar, then click **Schedule a command**.
   Double-clicking (or pressing Enter on) a day opens the same dialog.
2. Enter the command, the folder to run it in, and a time. New dialogs
   default to tomorrow at 9 AM. The quick chips jump to In 10 minutes;
   Tonight, 8 PM; Tomorrow, 9 AM; or Monday, 9 AM. The summary line states
   exactly when it will run before you save.
3. Choose whether it runs once, every day, or every week.
4. Click **Start service**. The window can then be closed.

The header shows whether the service is up: green **Service running**, grey
**Service stopped**, red **Service not responding**. The button reads Start,
Stop or Restart to match. Nothing runs while the service is stopped.

Days with something scheduled are marked on the calendar: bold amber for a
one-off, a quiet underline for a repeating job. The **Today** and
**Every day** chips switch the list filter. The sidebar shows what runs next
plus total, paused and failed counts. Select any command to read what it
printed the last time it ran; **Clear** wipes that log.

Each row carries a status badge: scheduled, running, finished, failed or
paused (**Pause** flips to **Resume** for paused jobs).

## How it runs

The service wakes every 10 seconds, runs anything due, and writes the output
to that job's log. Set `SCHEDULER_POLL_SECONDS` to change the interval
(clamped to 1-3600). Only one daemon runs at a time; a second launch exits
after seeing the first one's heartbeat in `daemon.json`.

A time that already passed while the PC was off runs once as catch-up at the
next poll. A `once` job ends after it runs, failed or not. There is no
re-arm button, so to run one again, edit and save it (saving resets it to
scheduled) or use **Run it now**. `daily` and `weekly` jobs reschedule
themselves to the next future occurrence, skipping straight past any period
the machine was switched off for.

**Run it now** executes immediately without touching the schedule. If the job
has chained steps, those run too when it exits 0.

## Chained next-steps

Select any command and click **Add next step** to run something right after
it finishes. For example, schedule `xxxx` tomorrow, then add a push in the
same folder. A step needs only a command and a folder (pre-filled from its
parent). There is no date to pick because it rides on the chain's schedule.

- Steps run oldest-first when the parent exits 0. A non-zero exit skips the
  rest of that chain and notes why in each skipped step's log.
- Pausing a step pauses everything after it. Deleting a step keeps the chain
  connected: its steps now follow the step above. **Detach** takes a step out
  of its chain so it runs on its own time.
- The schedule shows chains as nested trees: `↳ after <name>` on a step,
  `+N next` on a parent. Repeating parents re-run their whole chain each
  time. Editing a step locks its date controls because timing belongs to the
  first step.

## Notes

- On Windows, commands run through `cmd.exe` in the job's working folder, so
  chain with `&&`, as in `git commit -am "msg" && git push`. A bare `;`
  is not a separator there. It becomes part of the argument and git reports
  a bad pathspec. For PowerShell syntax, call it explicitly:
  `powershell -NoProfile -Command "..."`.
- Timeout is one hour per command. Logs rotate at 1 MB with one backup
  (`.log.1`). Very chatty output is truncated in the log with a note.
- Times are stored in local time with no zone, so a daily job will shift by
  an hour across a daylight-saving change.
- **Start at login** (Windows only; the button is disabled elsewhere) writes
  a `CmdSchedulerDaemon` entry under `HKCU\...\Run` pointing at the current
  `pythonw.exe` plus `daemon.py`. It starts at login, not boot, and the app
  never needs to open again. `start_daemon_hidden.vbs` is a manual
  alternative: double-click it, or drop a shortcut in `shell:startup`.
  If you move or upgrade Python, switch autostart off and on again.
- Tests: `python -m pytest tests -q` covers storage, due rules, repeats,
  chains and daemon liveness.
