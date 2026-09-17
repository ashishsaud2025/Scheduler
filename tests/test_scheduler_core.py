"""Tests for the scheduling and storage rules."""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scheduler_core as core  # noqa: E402


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def make(command="echo hi", when=None, repeat="once", **kw):
    job = core.new_job(kw.pop("name", "t"), command,
                       when or datetime.now() - timedelta(minutes=1),
                       str(Path.home()), repeat, kw.pop("enabled", True))
    job.update(kw)
    core.add_job(job)
    return job


def test_corrupt_file_raises_instead_of_wiping_jobs():
    make(name="keep me")
    core.jobs_file().write_text("{not json", encoding="utf-8")
    with pytest.raises(core.StorageError):
        core.load_jobs()
    quarantined = list(core.jobs_file().parent.glob("jobs.corrupt-*.json"))
    assert quarantined, "the unreadable file must be preserved, not discarded"
    assert core.jobs_file().read_text(encoding="utf-8") == "{not json"


def test_save_is_atomic_and_leaves_no_temp_files():
    make()
    core.save_jobs(core.load_jobs())
    leftovers = list(core.jobs_file().parent.glob("*.tmp"))
    assert leftovers == []


def test_concurrent_writers_do_not_lose_jobs():
    def worker(index: int) -> None:
        core.add_job(core.new_job(f"job{index}", "echo x",
                                  datetime.now() + timedelta(hours=1)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(core.load_jobs()) == 12


def test_entries_without_an_id_are_ignored():
    core.jobs_file().write_text('[{"name": "junk"}, 42]', encoding="utf-8")
    assert core.load_jobs() == []


def test_failed_one_off_job_does_not_run_again():
    job = make(command="exit 3")
    assert core.execute_due_jobs()[0]["status"] == core.STATUS_FAILED
    assert core.execute_due_jobs() == []


def test_finished_one_off_job_does_not_run_again():
    make(command="echo ok")
    assert core.execute_due_jobs()[0]["status"] == core.STATUS_DONE
    assert core.execute_due_jobs() == []


def test_disabled_job_is_never_due():
    job = make(enabled=False)
    assert core.is_due(job) is False


def test_running_job_is_not_started_twice():
    job = make(started_at=datetime.now().isoformat(timespec="seconds"),
               status=core.STATUS_RUNNING)
    assert core.is_due(job) is False


def test_abandoned_running_job_is_retried():
    stale = datetime.now() - timedelta(seconds=core.DEFAULT_TIMEOUT + 600)
    job = make(started_at=stale.isoformat(timespec="seconds"),
               status=core.STATUS_RUNNING)
    assert core.is_due(job) is True


def test_unparseable_run_at_is_not_due():
    job = make(run_at="not a date")
    assert core.is_due(job) is False


def test_daily_job_reschedules_into_the_future():
    make(command="echo ok", when=datetime.now() - timedelta(days=30),
         repeat="daily")
    finished = core.execute_due_jobs()[0]
    assert finished["status"] == core.STATUS_PENDING
    assert core.parse_run_at(finished["run_at"]) > datetime.now()
    assert core.get_due_jobs() == []


def test_weekly_job_keeps_its_weekday():
    start = datetime.now() - timedelta(weeks=3)
    make(command="echo ok", when=start, repeat="weekly")
    finished = core.execute_due_jobs()[0]
    assert core.parse_run_at(finished["run_at"]).weekday() == start.weekday()


def test_occurrences_on_matches_each_repeat_kind():
    start = datetime(2026, 9, 15, 9, 0)
    once = core.new_job("a", "x", start, repeat="once")
    daily = core.new_job("b", "x", start, repeat="daily")
    weekly = core.new_job("c", "x", start, repeat="weekly")
    assert core.occurrences_on(once, start.date())
    assert not core.occurrences_on(once, start.date() + timedelta(days=1))
    assert core.occurrences_on(daily, start.date() + timedelta(days=5))
    assert core.occurrences_on(weekly, start.date() + timedelta(days=7))
    assert not core.occurrences_on(weekly, start.date() + timedelta(days=3))
    assert not core.occurrences_on(daily, start.date() - timedelta(days=1))


def test_next_occurrence_skips_finished_one_off():
    job = make(status=core.STATUS_DONE)
    assert core.next_occurrence(job) is None


def test_missing_working_folder_reports_127_without_running():
    job = make(workdir=str(Path.home() / "nope"))
    assert core.run_job(job) == 127
    assert "does not exist" in core.read_log(job["id"])


def test_timeout_is_reported():
    job = make(command=f'"{sys.executable}" -c "import time; time.sleep(5)"')
    assert core.run_job(job, timeout=1) == 124
    assert "timed out" in core.read_log(job["id"])


def test_empty_command_is_rejected():
    job = make(command="   ")
    assert core.run_job(job) == 2


def test_log_rotates_instead_of_growing_forever(monkeypatch):
    monkeypatch.setattr(core, "MAX_LOG_BYTES", 200)
    job = make()
    for _ in range(20):
        core.append_log(job["id"], "x" * 50)
    assert core.log_path(job["id"]).stat().st_size < 2000
    assert core.log_path(job["id"]).with_suffix(".log.1").exists()


def test_manual_run_does_not_consume_the_schedule():
    when = datetime.now() + timedelta(hours=3)
    job = make(command="echo ok", when=when)
    core.run_job_manually(job)
    stored = core.load_jobs()[0]
    assert stored["status"] == core.STATUS_PENDING
    assert core.parse_run_at(stored["run_at"]) == when.replace(second=0, microsecond=0)
    assert stored["last_exit_code"] == 0


def test_process_alive_reports_this_process():
    assert core.process_alive(os.getpid()) is True


def test_process_alive_is_false_for_a_dead_pid():
    assert core.process_alive(999_999) is False


def test_process_alive_does_not_kill_the_target():
    """Probing liveness must not end the probed process."""
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert core.process_alive(proc.pid) is True
        assert proc.poll() is None, "probing liveness must not end the process"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_daemon_status_is_stale_when_the_heartbeat_ages(monkeypatch):
    core.write_heartbeat(os.getpid(), 10)
    assert core.daemon_status()["running"] is True
    old = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    core.heartbeat_file().write_text(
        f'{{"pid": {os.getpid()}, "poll_seconds": 10, "last_seen": "{old}"}}',
        encoding="utf-8")
    status = core.daemon_status()
    assert status["running"] is False and status["stale"] is True


def test_daemon_status_handles_a_broken_heartbeat_file():
    core.heartbeat_file().write_text("nonsense", encoding="utf-8")
    assert core.daemon_status()["running"] is False
