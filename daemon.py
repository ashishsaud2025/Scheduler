"""Background service: polls jobs.json and runs due commands."""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scheduler_core as core  # noqa: E402


def _poll_seconds() -> int:
    raw = os.getenv("SCHEDULER_POLL_SECONDS", "10")
    try:
        value = int(raw)
    except ValueError:
        log(f"SCHEDULER_POLL_SECONDS={raw!r} is not a number, using 10.")
        return 10
    return max(1, min(value, 3600))


def daemon_log_path() -> Path:
    return core.get_data_dir() / "daemon.log"


def log(msg: str) -> None:
    path = daemon_log_path()
    try:
        if path.exists() and path.stat().st_size > core.MAX_LOG_BYTES:
            path.replace(path.with_suffix(".log.1"))
    except OSError:
        pass
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def already_running() -> int | None:
    """Pid of a live daemon, or None. Never signals the other process."""
    status = core.daemon_status()
    if status["running"] and status["pid"] != os.getpid():
        return status["pid"]
    return None


def sleep_until_next_poll(seconds: int) -> bool:
    """Sleep in short slices. Returns False when a stop was requested."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if core.stop_requested():
            return False
        time.sleep(min(0.5, max(0.05, deadline - time.monotonic())))
    return not core.stop_requested()


def main() -> int:
    poll = _poll_seconds()
    other = already_running()
    if other is not None:
        log(f"Another daemon is already running (pid {other}). Exiting.")
        return 0

    core.clear_stop_request()
    core.write_heartbeat(os.getpid(), poll)
    log(f"Daemon started (pid {os.getpid()}, polling every {poll}s).")
    try:
        while True:
            try:
                for job in core.execute_due_jobs():
                    log(f"Ran '{job['name']}' [{job['id']}] "
                        f"exit={job.get('last_exit_code')} "
                        f"next={job.get('run_at')}")
            except core.StorageError as e:
                log(f"Storage problem, will retry: {e}")
            except Exception:  # noqa: BLE001
                log("Unexpected error in poll loop:\n" + traceback.format_exc())
            core.write_heartbeat(os.getpid(), poll)
            if not sleep_until_next_poll(poll):
                log("Stop requested.")
                break
    except KeyboardInterrupt:
        log("Interrupted.")
    finally:
        core.clear_heartbeat()
        core.clear_stop_request()
        log("Daemon stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
