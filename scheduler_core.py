"""Storage, scheduling rules, and execution for the app and the daemon."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator

APP_NAME = "CmdScheduler"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"

REPEATS = ("once", "daily", "weekly")

DEFAULT_TIMEOUT = 3600
MAX_LOG_BYTES = 1_000_000
MAX_CAPTURED_CHARS = 200_000
LOCK_TIMEOUT = 10.0
STALE_RUN_GRACE = 300


class StorageError(RuntimeError):
    """Raised when jobs.json cannot be read or written safely."""


def get_data_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        p = Path(appdata) / APP_NAME
    else:
        p = Path.home() / ".config" / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    (p / "logs").mkdir(parents=True, exist_ok=True)
    return p


def jobs_file() -> Path:
    return get_data_dir() / "jobs.json"


def logs_dir() -> Path:
    return get_data_dir() / "logs"


def lock_file() -> Path:
    return get_data_dir() / "jobs.lock"


@contextmanager
def _locked() -> Iterator[None]:
    """Run the block while holding the cross-process jobs lock."""
    path = lock_file()
    deadline = time.monotonic() + LOCK_TIMEOUT
    handle = open(path, "a+b")
    try:
        while True:
            try:
                _acquire(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StorageError(
                        f"Timed out waiting for the jobs lock at {path}. "
                        "Another Command Scheduler process may be stuck."
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            _release(handle)
    finally:
        handle.close()


def _acquire(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _read_unlocked() -> list[dict]:
    f = jobs_file()
    if not f.exists():
        return []
    try:
        raw = f.read_text(encoding="utf-8")
    except OSError as e:
        raise StorageError(f"Cannot read {f}: {e}") from e
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        # Quarantine instead of returning []: a later save would wipe all jobs.
        quarantine = f.with_name(f"jobs.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
        try:
            shutil.copy2(f, quarantine)
        except OSError:
            quarantine = f
        raise StorageError(
            f"{f} is not valid JSON ({e}). A copy was kept at {quarantine}. "
            "Fix or delete the file, then reopen the app."
        ) from e
    if not isinstance(data, list):
        raise StorageError(f"{f} should contain a list of jobs.")
    return [j for j in data if isinstance(j, dict) and j.get("id")]


def _write_unlocked(jobs: list[dict]) -> None:
    f = jobs_file()
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        os.replace(tmp, f)  # atomic on Windows and POSIX
    except OSError as e:
        raise StorageError(f"Cannot write {f}: {e}") from e
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def load_jobs() -> list[dict]:
    with _locked():
        return _read_unlocked()


def save_jobs(jobs: list[dict]) -> None:
    with _locked():
        _write_unlocked(jobs)


def mutate(change: Callable[[list[dict]], None]) -> list[dict]:
    """Apply `change` to the job list under one lock, then save atomically."""
    with _locked():
        jobs = _read_unlocked()
        change(jobs)
        _write_unlocked(jobs)
        return jobs


def new_job(name: str, command: str, run_at: datetime,
            workdir: str = "", repeat: str = "once", enabled: bool = True,
            timeout: int = DEFAULT_TIMEOUT,
            parent_id: str | None = None) -> dict:
    if repeat not in REPEATS:
        raise ValueError(f"repeat must be one of {REPEATS}")
    if parent_id is not None:
        # Chained steps run with their parent, so they are always one-shot.
        repeat = "once"
    return {
        "id": uuid.uuid4().hex[:8],
        "name": name.strip() or command.strip()[:40],
        "command": command,
        "workdir": workdir or os.path.expanduser("~"),
        "run_at": run_at.replace(second=0, microsecond=0).isoformat(timespec="seconds"),
        "repeat": repeat,
        "enabled": enabled,
        "status": STATUS_PENDING if enabled else STATUS_DISABLED,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": None,
        "last_run": None,
        "last_exit_code": None,
        "timeout": int(timeout),
        "parent_id": parent_id,
    }


def add_next_step(parent_id: str, name: str, command: str,
                  workdir: str | None = None,
                  timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Add a command that runs right after `parent_id` exits 0."""
    if not command.strip():
        raise ValueError("command must not be empty")
    parents = load_jobs()
    parent = next((j for j in parents if j["id"] == parent_id), None)
    if parent is None:
        raise ValueError(f"no job with id {parent_id!r}")
    run_at = parse_run_at(parent.get("run_at")) or datetime.now()
    job = new_job(name, command, run_at,
                  workdir if workdir is not None else parent.get("workdir", ""),
                  enabled=True, timeout=timeout, parent_id=parent_id)
    add_job(job)
    return job


def set_parent(job_id: str, parent_id: str | None) -> list[dict]:
    """Attach a job after another one, or detach it back to standalone."""
    if parent_id == job_id:
        raise ValueError("a job cannot run after itself")

    def change(jobs: list[dict]) -> None:
        by_id = {j["id"]: j for j in jobs}
        job = by_id.get(job_id)
        if job is None:
            raise ValueError(f"no job with id {job_id!r}")
        if parent_id is not None:
            parent = by_id.get(parent_id)
            if parent is None:
                raise ValueError(f"no job with id {parent_id!r}")
            if _is_ancestor(jobs, parent_id, job_id):
                raise ValueError("that would create a loop in the chain")
            job["parent_id"] = parent_id
            job["repeat"] = "once"
            if parse_run_at(parent.get("run_at")) is not None:
                job["run_at"] = parent["run_at"]
        else:
            job["parent_id"] = None

    return mutate(change)


def _is_ancestor(jobs: list[dict], node_id: str, maybe_ancestor_id: str) -> bool:
    """True when `maybe_ancestor_id` appears above `node_id` in the chain."""
    by_id = {j["id"]: j for j in jobs}
    seen: set[str] = set()
    current = by_id.get(node_id)
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        parent = current.get("parent_id")
        if parent == maybe_ancestor_id:
            return True
        current = by_id.get(parent) if parent else None
    return False


def add_job(job: dict) -> list[dict]:
    return mutate(lambda jobs: jobs.append(job))


def update_job(job_id: str, updates: dict) -> list[dict]:
    def change(jobs: list[dict]) -> None:
        for j in jobs:
            if j["id"] == job_id:
                j.update(updates)
                return

    return mutate(change)


def delete_job(job_id: str) -> list[dict]:
    def change(jobs: list[dict]) -> None:
        doomed = next((j for j in jobs if j["id"] == job_id), None)
        grandparent = doomed.get("parent_id") if doomed else None
        # Steps of the deleted job move up to its own parent.
        for j in jobs:
            if j.get("parent_id") == job_id:
                j["parent_id"] = grandparent
        jobs[:] = [j for j in jobs if j["id"] != job_id]

    return mutate(change)


def toggle_job(job_id: str) -> list[dict]:
    def change(jobs: list[dict]) -> None:
        for j in jobs:
            if j["id"] != job_id:
                continue
            enabled = not j.get("enabled", True)
            j["enabled"] = enabled
            if not enabled:
                j["status"] = STATUS_DISABLED
            elif j.get("status") in (STATUS_DISABLED, STATUS_FAILED):
                j["status"] = STATUS_PENDING
            return

    return mutate(change)


def reset_job(job_id: str) -> list[dict]:
    """Return a finished or failed job to pending so it can run again."""
    return update_job(job_id, {"status": STATUS_PENDING, "started_at": None})


def is_chained(job: dict) -> bool:
    return bool(job.get("parent_id"))


def children_of(jobs: list[dict], parent_id: str) -> list[dict]:
    """Direct next-steps of a job, oldest first (the order they run in)."""
    return sorted(
        (j for j in jobs if j.get("parent_id") == parent_id),
        key=lambda j: (j.get("created_at", ""), j["id"]))


def root_of(job: dict, by_id: dict[str, dict]) -> dict:
    """First step of the chain, following parent links (cycle-safe)."""
    seen: set[str] = set()
    current = job
    while True:
        parent_id = current.get("parent_id")
        parent = by_id.get(parent_id) if parent_id else None
        if parent is None or current["id"] in seen:
            return current
        seen.add(current["id"])
        current = parent


def effective_run_at(job: dict, by_id: dict[str, dict] | None = None,
                     jobs: list[dict] | None = None) -> datetime | None:
    """When the chain this job belongs to is scheduled (the root's time)."""
    if by_id is None:
        jobs = jobs if jobs is not None else load_jobs()
        by_id = {j["id"]: j for j in jobs}
    return parse_run_at(root_of(job, by_id).get("run_at"))


def parse_run_at(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def is_due(job: dict, now: datetime | None = None) -> bool:
    if not job.get("enabled", True):
        return False
    if is_chained(job):
        return False
    now = now or datetime.now()
    status = job.get("status")
    repeat = job.get("repeat", "once")

    if repeat == "once" and status in (STATUS_DONE, STATUS_FAILED):
        return False

    if status == STATUS_RUNNING and not _run_looks_abandoned(job, now):
        return False

    run_at = parse_run_at(job.get("run_at"))
    if run_at is None:
        return False
    return run_at <= now


def _run_looks_abandoned(job: dict, now: datetime) -> bool:
    """True when a job is marked running but its process is long gone."""
    started = parse_run_at(job.get("started_at"))
    if started is None:
        return True
    limit = int(job.get("timeout", DEFAULT_TIMEOUT)) + STALE_RUN_GRACE
    return (now - started).total_seconds() > limit


def get_due_jobs(now: datetime | None = None) -> list[dict]:
    return [j for j in load_jobs() if is_due(j, now)]


def next_run_for_repeat(job: dict, now: datetime | None = None) -> datetime | None:
    repeat = job.get("repeat", "once")
    run_at = parse_run_at(job.get("run_at"))
    if run_at is None or repeat not in ("daily", "weekly"):
        return None
    now = now or datetime.now()
    step = timedelta(days=1) if repeat == "daily" else timedelta(weeks=1)
    behind = (now - run_at).total_seconds()
    periods = max(1, int(behind // step.total_seconds()) + 1)
    nxt = run_at + step * periods
    while nxt <= now:
        nxt += step
    return nxt


def next_occurrence(job: dict, now: datetime | None = None,
                      jobs: list[dict] | None = None) -> datetime | None:
    """When this job is expected to run next, or None if it never will."""
    if not job.get("enabled", True):
        return None
    if is_chained(job):
        jobs = jobs if jobs is not None else load_jobs()
        by_id = {j["id"]: j for j in jobs}
        root = root_of(job, by_id)
        if root["id"] == job["id"]:
            return None
        return next_occurrence(root, now, jobs)
    run_at = parse_run_at(job.get("run_at"))
    if run_at is None:
        return None
    now = now or datetime.now()
    if run_at > now:
        return run_at
    if job.get("repeat", "once") == "once":
        return None if job.get("status") in (STATUS_DONE, STATUS_FAILED) else run_at
    return next_run_for_repeat(job, now)


def occurrences_on(job: dict, day: date, horizon_days: int = 400,
                     jobs: list[dict] | None = None) -> bool:
    """True when this job is expected to run on `day`."""
    if is_chained(job):
        jobs = jobs if jobs is not None else load_jobs()
        by_id = {j["id"]: j for j in jobs}
        root = root_of(job, by_id)
        if root["id"] == job["id"]:
            return False
        return occurrences_on(root, day, horizon_days, jobs)
    run_at = parse_run_at(job.get("run_at"))
    if run_at is None:
        return False
    start = run_at.date()
    if day < start:
        return False
    repeat = job.get("repeat", "once")
    if repeat == "once":
        return day == start
    delta = (day - start).days
    if delta > horizon_days:
        return False
    if repeat == "daily":
        return True
    if repeat == "weekly":
        return delta % 7 == 0
    return False


def log_path(job_id: str) -> Path:
    return logs_dir() / f"{job_id}.log"


def append_log(job_id: str, text: str) -> Path:
    log = log_path(job_id)
    try:
        if log.exists() and log.stat().st_size > MAX_LOG_BYTES:
            log.replace(log.with_suffix(".log.1"))
    except OSError:
        pass
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] {text}\n")
    except OSError:
        pass
    return log


def clear_log(job_id: str) -> None:
    for p in (log_path(job_id), log_path(job_id).with_suffix(".log.1")):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def read_log(job_id: str, limit: int = 40_000) -> str:
    p = log_path(job_id)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError as e:
        return f"(cannot read log: {e})"


def run_job(job: dict, timeout: int | None = None) -> int:
    """Run one job via the shell and log its output. Returns the exit code."""
    cmd = job.get("command", "").strip()
    if not cmd:
        append_log(job["id"], "ERROR: job has no command")
        return 2
    workdir = job.get("workdir") or os.path.expanduser("~")
    if not Path(workdir).is_dir():
        append_log(job["id"], f"ERROR: working folder does not exist: {workdir}")
        return 127
    limit = int(timeout or job.get("timeout") or DEFAULT_TIMEOUT)
    append_log(job["id"], f"$ {cmd}\n  (cwd={workdir}, timeout={limit}s)")
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=workdir,
            capture_output=True, text=True, errors="replace", timeout=limit,
        )
    except subprocess.TimeoutExpired:
        append_log(job["id"], f"ERROR: timed out after {limit}s")
        return 124
    except OSError as e:
        append_log(job["id"], f"ERROR: could not start command: {e}")
        return 126
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_CAPTURED_CHARS:
        out = out[:MAX_CAPTURED_CHARS] + f"\n... truncated at {MAX_CAPTURED_CHARS} characters"
    append_log(job["id"], out.strip() or "(no output)")
    append_log(job["id"], f"exit code: {proc.returncode}")
    return proc.returncode


def _claim_due_jobs(now: datetime) -> list[dict]:
    """Mark every due job as running in one locked pass and return copies."""
    claimed: list[dict] = []

    def change(jobs: list[dict]) -> None:
        for j in jobs:
            if is_due(j, now):
                j["status"] = STATUS_RUNNING
                j["started_at"] = now.isoformat(timespec="seconds")
                j["last_run"] = now.isoformat(timespec="seconds")
                claimed.append(dict(j))

    mutate(change)
    return claimed


def finish_job(job_id: str, code: int, now: datetime | None = None) -> dict | None:
    """Record the outcome of a run and reschedule a repeating job."""
    now = now or datetime.now()
    result: dict | None = None

    def change(jobs: list[dict]) -> None:
        nonlocal result
        for j in jobs:
            if j["id"] != job_id:
                continue
            j["last_exit_code"] = code
            j["started_at"] = None
            nxt = None if is_chained(j) else next_run_for_repeat(j, now)
            if nxt is not None:
                j["run_at"] = nxt.isoformat(timespec="seconds")
                j["status"] = STATUS_PENDING
            else:
                j["status"] = STATUS_DONE if code == 0 else STATUS_FAILED
            result = dict(j)
            return

    mutate(change)
    return result


MAX_CHAIN_DEPTH = 50


def _claim_one(job_id: str, now: datetime) -> dict | None:
    """Atomically mark one chained step as running. Returns its snapshot."""
    claimed: dict | None = None

    def change(jobs: list[dict]) -> None:
        nonlocal claimed
        for j in jobs:
            if j["id"] != job_id or not j.get("enabled", True):
                continue
            if j.get("status") == STATUS_RUNNING and not _run_looks_abandoned(
                    j, now):
                return
            j["status"] = STATUS_RUNNING
            j["started_at"] = now.isoformat(timespec="seconds")
            j["last_run"] = now.isoformat(timespec="seconds")
            claimed = dict(j)
            return

    mutate(change)
    return claimed


def _run_descendants(parent_id: str, parent_code: int, parent_name: str,
                     _depth: int = 0) -> list[dict]:
    """Run every next-step below `parent_id`, oldest first, depth-first."""
    now = datetime.now()
    finished: list[dict] = []
    if _depth > MAX_CHAIN_DEPTH:
        append_log(parent_id, "ERROR: chain is too deep, stopping here")
        return finished
    children = children_of(load_jobs(), parent_id)
    if parent_code != 0:
        for child in children:
            append_log(child["id"],
                       f"Skipped: '{parent_name}' exited with code "
                       f"{parent_code}, so chained steps do not run.")
        return finished
    for child in children:
        if not child.get("enabled", True):
            append_log(child["id"],
                       f"Skipped: this step is paused, "
                       f"and so is everything after it.")
            continue
        snapshot = _claim_one(child["id"], now)
        if snapshot is None:
            continue
        code = run_job(snapshot)
        updated = finish_job(child["id"], code)
        finished.append(updated or snapshot)
        finished.extend(_run_descendants(child["id"], code,
                                         snapshot.get("name", ""), _depth + 1))
    return finished


def execute_due_jobs(now: datetime | None = None) -> list[dict]:
    """Run every due job and its chained next-steps. Returns all finished."""
    now = now or datetime.now()
    finished: list[dict] = []
    for job in _claim_due_jobs(now):
        code = run_job(job)
        updated = finish_job(job["id"], code)
        finished.append(updated or job)
        finished.extend(_run_descendants(job["id"], code, job.get("name", "")))
    return finished


def run_job_manually(job: dict) -> dict | None:
    """Run a job now without disturbing its schedule, then record the result."""
    code = run_job(job)
    result: dict | None = None

    def change(jobs: list[dict]) -> None:
        nonlocal result
        for j in jobs:
            if j["id"] != job["id"]:
                continue
            j["last_exit_code"] = code
            j["last_run"] = datetime.now().isoformat(timespec="seconds")
            result = dict(j)
            return

    mutate(change)
    return result


def run_manual_chain(job: dict) -> tuple[dict | None, list[dict]]:
    """Run a job now, then its chained next-steps if it succeeded."""
    updated = run_job_manually(job)
    code = (updated or {}).get("last_exit_code", 1)
    chained = _run_descendants(job["id"], int(code), job.get("name", ""))
    return updated, chained


def tomorrow_at(hour: int = 9, minute: int = 0) -> datetime:
    t = datetime.now() + timedelta(days=1)
    return t.replace(hour=hour, minute=minute, second=0, microsecond=0)


def tonight_at(hour: int = 20, minute: int = 0) -> datetime:
    now = datetime.now()
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def next_weekday_at(weekday: int, hour: int = 9, minute: int = 0) -> datetime:
    """weekday: Monday=0 to Sunday=6. Returns the next strictly future match."""
    now = datetime.now()
    days_ahead = (weekday - now.weekday()) % 7
    target = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=7)
    return target


def humanize_delta(when: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    secs = (when - now).total_seconds()
    past = secs < 0
    secs = abs(secs)
    if secs < 90:
        text = "less than a minute"
    elif secs < 3600:
        text = f"{int(secs // 60)} min"
    elif secs < 86400:
        hours = secs / 3600
        text = f"{hours:.0f} hr" if hours >= 2 else "1 hr"
    else:
        days = int(secs // 86400)
        text = f"{days} day" if days == 1 else f"{days} days"
    return f"{text} ago" if past else f"in {text}"


def python_exe(windowless: bool = False) -> str:
    """Path to the interpreter to launch child processes with."""
    exe = Path(sys.executable)
    if windowless and os.name == "nt":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


HEARTBEAT_STALE_FACTOR = 3
HEARTBEAT_GRACE = 15


def heartbeat_file() -> Path:
    return get_data_dir() / "daemon.json"


def stop_flag_file() -> Path:
    return get_data_dir() / "daemon.stop"


def process_alive(pid: int) -> bool:
    """True when a process with this id exists (never signals it)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write_heartbeat(pid: int, poll_seconds: int) -> None:
    payload = {
        "pid": pid,
        "poll_seconds": poll_seconds,
        "last_seen": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        heartbeat_file().write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def clear_heartbeat() -> None:
    try:
        heartbeat_file().unlink(missing_ok=True)
    except OSError:
        pass


def daemon_status() -> dict:
    """Report whether the background service is running and how fresh it is."""
    unknown = {"running": False, "pid": None, "last_seen": None, "stale": False}
    f = heartbeat_file()
    if not f.exists():
        return unknown
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        pid = int(data["pid"])
        poll = int(data.get("poll_seconds", 10))
        last_seen = parse_run_at(data.get("last_seen"))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return unknown
    alive = process_alive(pid)
    age = (datetime.now() - last_seen).total_seconds() if last_seen else None
    stale = age is not None and age > poll * HEARTBEAT_STALE_FACTOR + HEARTBEAT_GRACE
    return {
        "running": alive and not stale,
        "pid": pid if alive else None,
        "last_seen": last_seen,
        "stale": alive and stale,
    }


def request_daemon_stop() -> None:
    try:
        stop_flag_file().write_text(datetime.now().isoformat(), encoding="utf-8")
    except OSError:
        pass


def clear_stop_request() -> None:
    try:
        stop_flag_file().unlink(missing_ok=True)
    except OSError:
        pass


def stop_requested() -> bool:
    return stop_flag_file().exists()


def kill_process(pid: int) -> bool:
    """Force-terminate a process. Used only when a graceful stop times out."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=10)
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        return False
    return not process_alive(pid)
