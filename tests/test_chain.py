"""Tests for chained next-steps: ordering, failure handling, tree upkeep."""
from __future__ import annotations

import sys
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


def make_root(command="echo root", when=None, **kw):
    job = core.new_job(kw.pop("name", "root"), command,
                       when or (datetime.now() - timedelta(minutes=1)),
                       str(Path.home()), kw.pop("repeat", "once"),
                       kw.pop("enabled", True))
    job.update(kw)
    core.add_job(job)
    return job


def test_chained_step_is_never_time_due_on_its_own():
    root = make_root()
    child = core.add_next_step(root["id"], "second", "echo hi")
    assert core.is_due(child) is False
    assert core.get_due_jobs()[0]["id"] == root["id"]


def test_chain_runs_in_order_after_parent_succeeds():
    root = make_root(command="echo first")
    second = core.add_next_step(root["id"], "second", "echo second")
    third = core.add_next_step(second["id"], "third", "echo third")
    finished = core.execute_due_jobs()
    assert [j["id"] for j in finished] == [root["id"], second["id"], third["id"]]
    assert all(j["status"] == core.STATUS_DONE for j in finished)
    assert "second" in core.read_log(second["id"])


def test_failed_parent_skips_its_chain():
    root = make_root(command="exit 3")
    child = core.add_next_step(root["id"], "second", "echo should-not-run")
    finished = core.execute_due_jobs()
    assert [j["id"] for j in finished] == [root["id"]]
    assert finished[0]["status"] == core.STATUS_FAILED
    stored = {j["id"]: j for j in core.load_jobs()}
    assert stored[child["id"]]["status"] == core.STATUS_PENDING
    assert "Skipped" in core.read_log(child["id"])


def test_disabled_step_blocks_everything_after_it():
    root = make_root()
    mid = core.add_next_step(root["id"], "mid", "echo mid")
    tail = core.add_next_step(mid["id"], "tail", "echo tail")
    core.toggle_job(mid["id"])  # pause the middle step
    finished = core.execute_due_jobs()
    assert [j["id"] for j in finished] == [root["id"]]
    stored = {j["id"]: j for j in core.load_jobs()}
    assert stored[tail["id"]]["status"] == core.STATUS_PENDING


def test_chain_repeats_with_a_daily_parent():
    root = make_root(command="echo ok", repeat="daily",
                     when=datetime.now() - timedelta(days=2))
    child = core.add_next_step(root["id"], "second", "echo ok")
    finished = core.execute_due_jobs()
    assert finished[0]["status"] == core.STATUS_PENDING  # parent rescheduled
    assert finished[1]["status"] == core.STATUS_DONE  # step finished once
    assert core.get_due_jobs() == []


def test_delete_middle_step_reparents_its_children():
    root = make_root()
    mid = core.add_next_step(root["id"], "mid", "echo mid")
    tail = core.add_next_step(mid["id"], "tail", "echo tail")
    core.delete_job(mid["id"])
    stored = {j["id"]: j for j in core.load_jobs()}
    assert tail["id"] in stored
    assert stored[tail["id"]]["parent_id"] == root["id"]


def test_delete_root_promotes_children_to_standalone():
    root = make_root()
    child = core.add_next_step(root["id"], "second", "echo hi")
    core.delete_job(root["id"])
    stored = {j["id"]: j for j in core.load_jobs()}
    assert stored[child["id"]]["parent_id"] is None
    assert core.is_due(stored[child["id"]]) is True


def test_detach_makes_step_standalone_again():
    root = make_root()
    child = core.add_next_step(root["id"], "second", "echo hi")
    core.set_parent(child["id"], None)
    stored = {j["id"]: j for j in core.load_jobs()}
    assert stored[child["id"]]["parent_id"] is None
    assert core.is_due(stored[child["id"]]) is True


def test_loop_is_rejected():
    root = make_root()
    child = core.add_next_step(root["id"], "second", "echo hi")
    with pytest.raises(ValueError):
        core.set_parent(root["id"], child["id"])
    with pytest.raises(ValueError):
        core.set_parent(root["id"], root["id"])


def test_next_step_inherits_folder_but_allows_override(tmp_path):
    root = make_root()
    defaulted = core.add_next_step(root["id"], "a", "echo a")
    assert defaulted["workdir"] == root["workdir"]
    other = core.add_next_step(root["id"], "b", "echo b", workdir=str(tmp_path))
    assert other["workdir"] == str(tmp_path)


def test_calendar_and_next_run_follow_the_chain():
    when = datetime.now() + timedelta(days=2)
    root = make_root(when=when)
    child = core.add_next_step(root["id"], "second", "echo hi")
    jobs = core.load_jobs()
    by_id = {j["id"]: j for j in jobs}
    child = by_id[child["id"]]
    assert core.occurrences_on(child, when.date(), jobs=jobs) is True
    assert core.occurrences_on(
        child, when.date() + timedelta(days=1), jobs=jobs) is False
    assert core.next_occurrence(child, jobs=jobs) == when.replace(
        second=0, microsecond=0)


def test_manual_run_triggers_the_chain():
    when = datetime.now() + timedelta(hours=5)
    root = make_root(command="echo ok", when=when)
    child = core.add_next_step(root["id"], "second", "echo ok")
    updated, chained = core.run_manual_chain(root)
    assert updated["last_exit_code"] == 0
    assert [j["id"] for j in chained] == [child["id"]]
    stored = {j["id"]: j for j in core.load_jobs()}
    assert stored[root["id"]]["status"] == core.STATUS_PENDING
