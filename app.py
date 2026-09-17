"""Command Scheduler desktop window."""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QSize, Qt, QThread, QTime, QTimer, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFontMetrics, QPalette,
                           QTextCharFormat)
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QCheckBox, QComboBox,
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy,
    QSplitter, QTextEdit, QTimeEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scheduler_core as core  # noqa: E402
import ui_theme as theme  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
REPEAT_WORDS = {"once": "once", "daily": "every day", "weekly": "every week"}


def start_daemon() -> str:
    status = core.daemon_status()
    if status["running"]:
        return f"Background service is already running (pid {status['pid']})."
    daemon_py = APP_DIR / "daemon.py"
    if not daemon_py.exists():
        return f"Cannot find {daemon_py}."
    core.clear_stop_request()
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        subprocess.Popen(
            [core.python_exe(windowless=True), str(daemon_py)],
            cwd=str(APP_DIR), creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
        )
    except OSError as e:
        return f"Could not start the background service: {e}"
    return "Starting the background service…"


def stop_daemon() -> str:
    status = core.daemon_status()
    pid = status["pid"]
    if pid is None:
        core.clear_heartbeat()
        return "Background service is not running."
    core.request_daemon_stop()
    for _ in range(30):
        QThread.msleep(100)
        if not core.process_alive(pid):
            core.clear_stop_request()
            return f"Background service stopped (pid {pid})."
    if core.kill_process(pid):
        core.clear_heartbeat()
        core.clear_stop_request()
        return f"Background service force-stopped (pid {pid})."
    return f"Background service (pid {pid}) did not stop. Stop it in Task Manager."


def _run_key():
    import winreg

    return winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run",
                          0, winreg.KEY_ALL_ACCESS)


def is_autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with _run_key() as k:
            winreg.QueryValueEx(k, "CmdSchedulerDaemon")
        return True
    except (ImportError, FileNotFoundError, OSError):
        return False


def set_autostart(enable: bool) -> str:
    if os.name != "nt":
        return "Starting at login is only available on Windows."
    import winreg

    cmd = f'"{core.python_exe(windowless=True)}" "{APP_DIR / "daemon.py"}"'
    try:
        with _run_key() as k:
            if enable:
                winreg.SetValueEx(k, "CmdSchedulerDaemon", 0, winreg.REG_SZ, cmd)
                return "The background service will now start when you log in."
            try:
                winreg.DeleteValue(k, "CmdSchedulerDaemon")
            except FileNotFoundError:
                pass
            return "The background service will no longer start at login."
    except OSError as e:
        return f"Could not change the login setting: {e}"


class Dot(QLabel):
    def __init__(self, color: str, size: int = 9, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(
            f"background: {color}; border-radius: {self.width() // 2}px;")


class Badge(QLabel):
    def __init__(self, text: str = "", color: str = theme.TEXT_MUTED, parent=None):
        super().__init__(text, parent)
        self.set_tone(color)

    def set_tone(self, color: str) -> None:
        self.setStyleSheet(
            f"color: {color}; border: 1px solid {color}55; border-radius: 9px;"
            f"padding: 1px 8px; font-size: 11px; background: {color}18;")


class JobCard(QWidget):
    """One row in the schedule tree. Chains show where the step sits."""

    def __init__(self, job: dict, when: datetime | None, parent=None,
                 parent_name: str | None = None, child_count: int = 0):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        status = job.get("status", "pending")
        if not job.get("enabled", True):
            status = "disabled"
        color = theme.STATUS_COLORS.get(status, theme.GREY)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 11, 14, 11)
        root.setSpacing(12)

        dot = Dot(color)
        dot_wrap = QVBoxLayout()
        dot_wrap.setContentsMargins(0, 4, 0, 0)
        dot_wrap.addWidget(dot)
        dot_wrap.addStretch(1)
        root.addLayout(dot_wrap)

        middle = QVBoxLayout()
        middle.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        name = QLabel(job.get("name", "(unnamed)"))
        name.setObjectName("jobName")
        title_row.addWidget(name)
        title_row.addWidget(Badge(theme.STATUS_WORDS.get(status, status), color))
        if job.get("repeat", "once") != "once":
            title_row.addWidget(Badge(REPEAT_WORDS[job["repeat"]], theme.TEXT_MUTED))
        if parent_name:
            short = parent_name if len(parent_name) <= 26 else parent_name[:25] + "…"
            title_row.addWidget(Badge(f"↳ after {short}", theme.TEXT_MUTED))
        if child_count:
            title_row.addWidget(Badge(f"+{child_count} next", theme.AMBER))
        title_row.addStretch(1)
        middle.addLayout(title_row)

        self.command = QLabel()
        self.command.setObjectName("jobCommand")
        self._command_text = job.get("command", "")
        self.command.setToolTip(self._command_text)
        middle.addWidget(self.command)
        root.addLayout(middle, 1)

        right = QVBoxLayout()
        right.setSpacing(3)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        time_label = QLabel(when.strftime("%H:%M") if when else "--:--")
        time_label.setObjectName("jobTime")
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(time_label)
        right.addWidget(self._meta_label(job, when))
        root.addLayout(right)

    def _meta_label(self, job: dict, when: datetime | None) -> QLabel:
        bits: list[str] = []
        if when and job.get("enabled", True):
            bits.append(core.humanize_delta(when))
        code = job.get("last_exit_code")
        if code is not None:
            bits.append("last run ok" if code == 0 else f"last exit {code}")
        label = QLabel("  ·  ".join(bits) if bits else "not run yet")
        label.setObjectName("jobMeta")
        label.setAlignment(Qt.AlignmentFlag.AlignRight)
        return label

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        metrics = QFontMetrics(self.command.font())
        self.command.setText(metrics.elidedText(
            self._command_text, Qt.TextElideMode.ElideRight,
            max(80, self.command.width())))

    def sizeHint(self) -> QSize:
        return QSize(400, 66)


class JobDialog(QDialog):
    def __init__(self, parent=None, job: dict | None = None,
                 initial_date: date | None = None,
                 chain_info: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit scheduled command" if job else "Schedule a command")
        self.setMinimumWidth(680)
        self.job = job
        self.chain_info = chain_info

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        heading = QLabel("What should run?")
        heading.setObjectName("dayTitle")
        root.addWidget(heading)

        self.cmd_edit = QLineEdit(job["command"] if job else "")
        self.cmd_edit.setObjectName("mono")
        self.cmd_edit.setPlaceholderText('git commit -am "nightly" && git push')
        self.cmd_edit.setClearButtonEnabled(True)
        root.addWidget(self.cmd_edit)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.dir_edit = QLineEdit(
            job.get("workdir", "") if job else str(Path.home()))
        self.dir_edit.setObjectName("mono")
        self.dir_edit.setPlaceholderText("Folder to run it in")
        browse = QPushButton("Choose folder")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.dir_edit, 1)
        folder_row.addWidget(browse)
        root.addLayout(folder_row)

        when_heading = QLabel("When?")
        when_heading.setObjectName("dayTitle")
        root.addWidget(when_heading)

        when_row = QHBoxLayout()
        when_row.setSpacing(18)

        self.calendar = QCalendarWidget()
        _style_calendar(self.calendar)
        self.calendar.setMinimumWidth(320)
        self.calendar.selectionChanged.connect(self._update_summary)
        self.calendar.currentPageChanged.connect(
            lambda *_: paint_calendar(self.calendar))
        when_row.addWidget(self.calendar)

        side = QVBoxLayout()
        side.setSpacing(8)
        self.time_edit = QTimeEdit()
        self.time_edit.setMaximumWidth(240)
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.timeChanged.connect(self._update_summary)
        side.addWidget(self.time_edit)

        chips = QVBoxLayout()
        chips.setSpacing(6)
        for label, factory in (
            ("In 10 minutes", lambda: datetime.now() + timedelta(minutes=10)),
            ("Tonight, 8 PM", lambda: core.tonight_at(20)),
            ("Tomorrow, 9 AM", lambda: core.tomorrow_at(9)),
            ("Monday, 9 AM", lambda: core.next_weekday_at(0, 9)),
        ):
            chip = QPushButton(label)
            chip.setObjectName("chip")
            chip.setMaximumWidth(240)
            chip.clicked.connect(lambda _=False, f=factory: self.set_when(f()))
            chips.addWidget(chip)
        side.addLayout(chips)

        self.repeat_combo = QComboBox()
        for value, label in (("once", "Runs once"),
                             ("daily", "Repeats every day"),
                             ("weekly", "Repeats every week")):
            self.repeat_combo.addItem(label, value)
        self.repeat_combo.setMaximumWidth(240)
        self.repeat_combo.currentIndexChanged.connect(self._update_summary)
        side.addWidget(self.repeat_combo)
        side.addStretch(1)
        when_row.addLayout(side, 1)
        root.addLayout(when_row)

        self.summary = QLabel()
        self.summary.setObjectName("hint")
        root.addWidget(self.summary)

        if chain_info:
            self.calendar.setEnabled(False)
            self.time_edit.setEnabled(False)
            self.repeat_combo.setEnabled(False)
            self.summary.setText(chain_info)
            self.summary.setObjectName("hint")

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.name_edit = QLineEdit(job["name"] if job else "")
        self.name_edit.setPlaceholderText("Name it (optional)")
        self.name_edit.setMaximumWidth(260)
        self.enabled_check = QCheckBox("Active")
        self.enabled_check.setChecked(job.get("enabled", True) if job else True)
        bottom.addWidget(self.name_edit)
        bottom.addWidget(self.enabled_check)
        bottom.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save schedule" if job else "Schedule it")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._try_accept)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)

        if job:
            self.repeat_combo.setCurrentIndex(
                max(0, self.repeat_combo.findData(job.get("repeat", "once"))))
            self.set_when(core.parse_run_at(job.get("run_at")) or core.tomorrow_at(9))
        else:
            start = core.tomorrow_at(9)
            if initial_date and initial_date >= date.today():
                start = datetime.combine(initial_date, start.time())
            self.set_when(start)
        self.cmd_edit.setFocus()

    def set_when(self, when: datetime) -> None:
        self.calendar.setSelectedDate(QDate(when.year, when.month, when.day))
        self.time_edit.setTime(QTime(when.hour, when.minute))
        self._update_summary()

    def when(self) -> datetime:
        d = self.calendar.selectedDate()
        t = self.time_edit.time()
        return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())

    def _update_summary(self) -> None:
        if self.chain_info:
            return
        when = self.when()
        repeat = self.repeat_combo.currentData()
        text = when.strftime("Runs %A %d %B at %H:%M")
        if repeat != "once":
            text += f", then {REPEAT_WORDS[repeat]}"
        if when <= datetime.now():
            self.summary.setObjectName("warn")
            text += ". That moment has passed, so it runs at the next check."
        else:
            self.summary.setObjectName("hint")
            text += f" ({core.humanize_delta(when)})"
        self.summary.setText(text)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Folder to run the command in",
            self.dir_edit.text() or str(Path.home()))
        if chosen:
            self.dir_edit.setText(chosen)

    def _try_accept(self) -> None:
        if not self.cmd_edit.text().strip():
            self.summary.setText("Enter a command first.")
            self.cmd_edit.setFocus()
            return
        folder = self.dir_edit.text().strip()
        if folder and not Path(folder).is_dir():
            self.summary.setText(f"That folder does not exist: {folder}")
            self.dir_edit.setFocus()
            return
        self.accept()

    def values(self) -> dict:
        cmd = self.cmd_edit.text().strip()
        enabled = self.enabled_check.isChecked()
        return {
            "name": self.name_edit.text().strip() or cmd[:40],
            "command": cmd,
            "workdir": self.dir_edit.text().strip() or str(Path.home()),
            "run_at": self.when().isoformat(timespec="seconds"),
            "repeat": self.repeat_combo.currentData(),
            "enabled": enabled,
            "status": core.STATUS_PENDING if enabled else core.STATUS_DISABLED,
            "started_at": None,
        }


class NextStepDialog(QDialog):
    """Command only, no date: it runs right after the selected step."""

    def __init__(self, parent, parent_job: dict):
        super().__init__(parent)
        self.setWindowTitle(f"Run after '{parent_job.get('name', '')}'")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        info = QLabel(
            f"Runs immediately after '{parent_job.get('name', '')}' finishes "
            f"cleanly, in the same folder unless you change it. No date "
            f"is needed because it rides on that step's schedule.")
        info.setObjectName("hint")
        info.setWordWrap(True)
        root.addWidget(info)

        self.cmd_edit = QLineEdit()
        self.cmd_edit.setObjectName("mono")
        self.cmd_edit.setPlaceholderText("git push")
        self.cmd_edit.setClearButtonEnabled(True)
        root.addWidget(self.cmd_edit)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.dir_edit = QLineEdit(parent_job.get("workdir", "") or str(Path.home()))
        self.dir_edit.setObjectName("mono")
        browse = QPushButton("Choose folder")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.dir_edit, 1)
        folder_row.addWidget(browse)
        root.addLayout(folder_row)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name it (optional)")
        root.addWidget(self.name_edit)

        self.error = QLabel()
        self.error.setObjectName("warn")
        root.addWidget(self.error)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Add as next step")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._try_accept)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)
        self.cmd_edit.setFocus()

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Folder to run the command in",
            self.dir_edit.text() or str(Path.home()))
        if chosen:
            self.dir_edit.setText(chosen)

    def _try_accept(self) -> None:
        if not self.cmd_edit.text().strip():
            self.error.setText("Enter a command first.")
            return
        folder = self.dir_edit.text().strip()
        if folder and not Path(folder).is_dir():
            self.error.setText(f"That folder does not exist: {folder}")
            return
        self.accept()

    def values(self) -> dict:
        cmd = self.cmd_edit.text().strip()
        return {
            "name": self.name_edit.text().strip() or cmd[:40],
            "command": cmd,
            "workdir": self.dir_edit.text().strip() or str(Path.home()),
        }


def paint_calendar(cal: QCalendarWidget, jobs: list[dict] | None = None) -> None:
    """Mark scheduled days on the visible six-week grid."""
    jobs = jobs or []
    blank = QTextCharFormat()
    for stale in getattr(cal, "_painted", []):
        cal.setDateTextFormat(stale, blank)
    painted: list[QDate] = []

    year, month = cal.yearShown(), cal.monthShown()
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())
    for offset in range(42):
        day = start + timedelta(days=offset)
        in_month = day.month == month and day.year == year
        fmt = QTextCharFormat()
        if not in_month:
            fmt.setForeground(QColor(theme.TEXT_FAINT))
        elif day.weekday() >= 5:
            fmt.setForeground(QColor(theme.TEXT_MUTED))
        else:
            fmt.setForeground(QColor(theme.TEXT))

        day_jobs = [j for j in jobs if core.occurrences_on(j, day)]
        active = [j for j in day_jobs if j.get("enabled", True)]
        if active:
            if any(j.get("repeat", "once") == "once" for j in active):
                fmt.setForeground(QColor(theme.AMBER))
                fmt.setFontWeight(700)
            else:
                fmt.setFontUnderline(True)
                fmt.setUnderlineColor(QColor(theme.AMBER))
        if day_jobs:
            names = ", ".join(j.get("name", "") for j in day_jobs[:6])
            fmt.setToolTip(f"{len(day_jobs)} scheduled: {names}")

        qd = QDate(day.year, day.month, day.day)
        cal.setDateTextFormat(qd, fmt)
        painted.append(qd)
    cal._painted = painted


def _style_calendar(cal: QCalendarWidget) -> None:
    cal.setGridVisible(False)
    palette = cal.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.RAISED))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.RAISED))
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.PANEL))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.AMBER))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#201704"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                     QColor(theme.TEXT_FAINT))
    cal.setPalette(palette)
    cal.setNavigationBarVisible(True)
    cal.setHorizontalHeaderFormat(
        QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames)
    cal.setVerticalHeaderFormat(
        QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    header = QTextCharFormat()
    header.setForeground(QColor(theme.TEXT_FAINT))
    cal.setHeaderTextFormat(header)
    for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(theme.TEXT_MUTED))
        cal.setWeekdayTextFormat(day, fmt)
    paint_calendar(cal)


class RunWorker(QThread):
    done = Signal(str, int, int)
    failed = Signal(str, str)

    def __init__(self, job: dict, parent=None):
        super().__init__(parent)
        self._job = dict(job)

    def run(self) -> None:
        try:
            updated, chained = core.run_manual_chain(self._job)
            code = (updated or {}).get("last_exit_code", 1)
            self.done.emit(self._job["id"], int(code), len(chained))
        except core.StorageError as e:
            self.failed.emit(self._job["id"], str(e))
        except Exception as e:  # noqa: BLE001 - never kill the worker silently
            self.failed.emit(self._job["id"], f"{type(e).__name__}: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Command Scheduler")
        self.resize(1120, 720)
        self.setMinimumSize(900, 600)

        self._jobs: list[dict] = []
        self._signature = ""
        self._marked: list[QDate] = []
        self._worker: RunWorker | None = None
        self._filter_date: date | None = date.today()

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(14, 14, 14, 10)
        body_layout.setSpacing(14)
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_content(), 1)
        outer.addWidget(body, 1)

        self.statusBar().showMessage(f"Jobs live in {core.jobs_file()}")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(4000)
        self.refresh(force=True)

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("headerBar")
        bar.setFixedHeight(66)
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 10, 16, 10)
        row.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("Command Scheduler")
        title.setObjectName("appTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("appSubtitle")
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        row.addLayout(titles)
        row.addStretch(1)

        self.service_dot = Dot(theme.GREY, 8)
        self.service_label = QLabel()
        row.addWidget(self.service_dot)
        row.addWidget(self.service_label)

        self.btn_service = QPushButton()
        self.btn_service.clicked.connect(self._toggle_service)
        self.btn_autostart = QPushButton()
        self.btn_autostart.setObjectName("ghost")
        self.btn_autostart.clicked.connect(self._toggle_autostart)
        row.addWidget(self.btn_service)
        row.addWidget(self.btn_autostart)
        return bar

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(348)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.calendar = QCalendarWidget()
        _style_calendar(self.calendar)
        self.calendar.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Fixed)
        self.calendar.setMinimumHeight(268)
        self.calendar.selectionChanged.connect(self._on_date_picked)
        self.calendar.currentPageChanged.connect(lambda *_: self._mark_calendar())
        self.calendar.activated.connect(lambda *_: self._add())
        layout.addWidget(self.calendar)

        picker_row = QHBoxLayout()
        picker_row.setSpacing(8)
        today_btn = QPushButton("Today")
        today_btn.setObjectName("chip")
        today_btn.clicked.connect(self._go_today)
        self.btn_all_dates = QPushButton("Every day")
        self.btn_all_dates.setObjectName("chip")
        self.btn_all_dates.setCheckable(True)
        self.btn_all_dates.toggled.connect(self._on_all_dates)
        picker_row.addWidget(today_btn)
        picker_row.addWidget(self.btn_all_dates)
        picker_row.addStretch(1)
        layout.addLayout(picker_row)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.LINE};")
        layout.addWidget(line)

        up_next = QLabel("Next to run")
        up_next.setObjectName("sectionLabel")
        layout.addWidget(up_next)
        self.next_summary = QLabel()
        self.next_summary.setWordWrap(True)
        layout.addWidget(self.next_summary)
        layout.addStretch(1)

        self.counts = QLabel()
        self.counts.setObjectName("hint")
        self.counts.setWordWrap(True)
        layout.addWidget(self.counts)
        return side

    def _build_content(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("contentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.day_title = QLabel()
        self.day_title.setObjectName("dayTitle")
        self.day_count = QLabel()
        self.day_count.setObjectName("dayCount")
        titles.addWidget(self.day_title)
        titles.addWidget(self.day_count)
        head.addLayout(titles)
        head.addStretch(1)
        add = QPushButton("Schedule a command")
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        head.addWidget(add)
        layout.addLayout(head)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)

        list_wrap = QWidget()
        list_layout = QVBoxLayout(list_wrap)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(8)

        self.job_list = QTreeWidget()
        self.job_list.setObjectName("jobTree")
        self.job_list.setHeaderHidden(True)
        self.job_list.setColumnCount(1)
        self.job_list.setIndentation(22)
        self.job_list.setAnimated(True)
        self.job_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.job_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.job_list.itemSelectionChanged.connect(self._on_selection)
        self.job_list.itemDoubleClicked.connect(lambda _: self._edit())
        list_layout.addWidget(self.job_list, 1)

        self.empty_label = QLabel()
        self.empty_label.setObjectName("hint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        list_layout.addWidget(self.empty_label, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_edit = QPushButton("Edit")
        self.btn_next = QPushButton("Add next step")
        self.btn_run = QPushButton("Run it now")
        self.btn_pause = QPushButton("Pause")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setObjectName("danger")
        self.btn_detach = QPushButton("Detach")
        self.btn_detach.setObjectName("ghost")
        self.btn_detach.setToolTip(
            "Take this step out of its chain so it runs on its own time.")
        self.btn_edit.clicked.connect(self._edit)
        self.btn_next.clicked.connect(self._add_next)
        self.btn_run.clicked.connect(self._run_now)
        self.btn_pause.clicked.connect(self._toggle_enabled)
        self.btn_delete.clicked.connect(self._delete)
        self.btn_detach.clicked.connect(self._detach)
        for b in (self.btn_edit, self.btn_next, self.btn_run,
                  self.btn_pause, self.btn_delete):
            actions.addWidget(b)
        actions.addWidget(self.btn_detach)
        actions.addStretch(1)
        self.btn_folder = QPushButton("Open data folder")
        self.btn_folder.setObjectName("ghost")
        self.btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(core.get_data_dir()))))
        actions.addWidget(self.btn_folder)
        list_layout.addLayout(actions)
        split.addWidget(list_wrap)

        log_wrap = QWidget()
        log_layout = QVBoxLayout(log_wrap)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(6)
        log_head = QHBoxLayout()
        self.log_title = QLabel("Output")
        self.log_title.setObjectName("sectionLabel")
        log_head.addWidget(self.log_title)
        log_head.addStretch(1)
        self.btn_clear_log = QPushButton("Clear")
        self.btn_clear_log.setObjectName("ghost")
        self.btn_clear_log.clicked.connect(self._clear_log)
        log_head.addWidget(self.btn_clear_log)
        log_layout.addLayout(log_head)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText(
            "Pick a command to see what it printed the last time it ran.")
        log_layout.addWidget(self.log_view)
        split.addWidget(log_wrap)
        split.setSizes([420, 210])
        layout.addWidget(split, 1)
        return panel

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except core.StorageError as e:
            QMessageBox.warning(self, "Cannot save", str(e))
            return None

    def _visible_jobs(self) -> list[dict]:
        if self._filter_date is None:
            jobs = list(self._jobs)
        else:
            jobs = [j for j in self._jobs
                    if core.occurrences_on(j, self._filter_date,
                                           jobs=self._jobs)]
        by_id = {j["id"]: j for j in self._jobs}
        return sorted(
            jobs,
            key=lambda j: (core.effective_run_at(j, by_id) or datetime.max))

    def _selected_job(self) -> dict | None:
        item = self.job_list.currentItem()
        if item is None:
            return None
        job_id = item.data(0, Qt.ItemDataRole.UserRole)
        return next((j for j in self._jobs if j["id"] == job_id), None)

    def _find_item(self, job_id: str) -> QTreeWidgetItem | None:
        def visit(parent: QTreeWidgetItem | None):
            count = (parent.childCount() if parent is not None
                     else self.job_list.topLevelItemCount())
            for i in range(count):
                item = (parent.child(i) if parent is not None
                        else self.job_list.topLevelItem(i))
                if item.data(0, Qt.ItemDataRole.UserRole) == job_id:
                    return item
                found = visit(item)
                if found is not None:
                    return found
            return None

        return visit(None)

    def _select_job(self, job_id: str | None) -> None:
        if job_id is None:
            return
        item = self._find_item(job_id)
        if item is not None:
            self.job_list.setCurrentItem(item)
            self.job_list.scrollToItem(item)

    def refresh(self, force: bool = False) -> None:
        try:
            self._jobs = core.load_jobs()
        except core.StorageError as e:
            self.statusBar().showMessage(str(e), 15000)
            return

        signature = repr([(j["id"], j.get("run_at"), j.get("status"),
                           j.get("name"), j.get("command"), j.get("repeat"),
                           j.get("enabled"), j.get("last_exit_code"),
                           j.get("parent_id"))
                          for j in self._jobs]) + str(self._filter_date)
        if force or signature != self._signature:
            self._signature = signature
            self._rebuild_list()
            self._mark_calendar()
        self._update_header()
        self._update_sidebar()
        self._update_actions()

    def _rebuild_list(self) -> None:
        keep = self._selected_job()
        keep_id = keep["id"] if keep else None
        scroll = self.job_list.verticalScrollBar().value()

        self.job_list.blockSignals(True)
        self.job_list.clear()
        by_id = {j["id"]: j for j in self._jobs}
        visible = self._visible_jobs()
        visible_ids = {j["id"] for j in visible}
        for job in visible:
            missing = job.get("parent_id")
            while missing and missing not in visible_ids and missing in by_id:
                visible_ids.add(missing)
                missing = by_id[missing].get("parent_id")
        nodes = [by_id[i] for i in visible_ids if i in by_id]
        roots = sorted(
            (j for j in nodes
             if not j.get("parent_id") or j.get("parent_id") not in visible_ids),
            key=lambda j: (core.effective_run_at(j, by_id) or datetime.max))

        def add_children(tree_parent: QTreeWidgetItem | None, parent_id: str):
            for child in core.children_of(nodes, parent_id):
                item = QTreeWidgetItem()
                item.setData(0, Qt.ItemDataRole.UserRole, child["id"])
                parent_job = by_id.get(child.get("parent_id") or "")
                card = JobCard(
                    child, self._display_time(child),
                    parent_name=(parent_job or {}).get("name"),
                    child_count=len(core.children_of(self._jobs, child["id"])))
                item.setSizeHint(0, card.sizeHint())
                if tree_parent is None:
                    self.job_list.addTopLevelItem(item)
                else:
                    tree_parent.addChild(item)
                self.job_list.setItemWidget(item, 0, card)
                add_children(item, child["id"])

        for root in roots:
            item = QTreeWidgetItem()
            item.setData(0, Qt.ItemDataRole.UserRole, root["id"])
            card = JobCard(root, self._display_time(root),
                           child_count=len(core.children_of(self._jobs,
                                                            root["id"])))
            item.setSizeHint(0, card.sizeHint())
            self.job_list.addTopLevelItem(item)
            self.job_list.setItemWidget(item, 0, card)
            add_children(item, root["id"])
        self.job_list.blockSignals(False)

        has_any = bool(roots)
        self.job_list.setVisible(has_any)
        self.empty_label.setVisible(not has_any)
        if not has_any:
            self.empty_label.setText(self._empty_text())
        self.job_list.expandAll()
        self.job_list.verticalScrollBar().setValue(scroll)
        self._select_job(keep_id)
        if self.job_list.currentItem() is None and has_any:
            self.job_list.setCurrentItem(self.job_list.topLevelItem(0))
        self._on_selection()

    def _display_time(self, job: dict) -> datetime | None:
        by_id = {j["id"]: j for j in self._jobs}
        eff = core.effective_run_at(job, by_id)
        if eff is None:
            return None
        if self._filter_date is None:
            return core.next_occurrence(job, jobs=self._jobs) or eff
        return datetime.combine(self._filter_date, eff.time())

    def _empty_text(self) -> str:
        if self._filter_date is None:
            return "Nothing scheduled yet. Schedule a command to fill this up."
        day = self._filter_date.strftime("%A %d %B")
        return f"Nothing scheduled for {day}. Pick another day, or add something."

    def _mark_calendar(self) -> None:
        paint_calendar(self.calendar, self._jobs)

    def _update_header(self) -> None:
        status = core.daemon_status()
        if status["running"]:
            self.service_dot.set_color(theme.GREEN)
            self.service_label.setText("Service running")
            self.service_label.setToolTip(
                f"pid {status['pid']}, last check {status['last_seen']:%H:%M:%S}"
                if status["last_seen"] else "")
            self.btn_service.setText("Stop service")
        elif status["stale"]:
            self.service_dot.set_color(theme.RED)
            self.service_label.setText("Service not responding")
            self.btn_service.setText("Restart service")
        else:
            self.service_dot.set_color(theme.GREY)
            self.service_label.setText("Service stopped")
            self.btn_service.setText("Start service")
        self.btn_autostart.setText(
            "Runs at login" if is_autostart_enabled() else "Start at login")
        self.btn_autostart.setEnabled(os.name == "nt")

        pending = sum(1 for j in self._jobs
                      if j.get("enabled", True) and j.get("status") != core.STATUS_DONE)
        if status["running"]:
            self.subtitle.setText(
                f"{pending} command{'' if pending == 1 else 's'} waiting. "
                "You can close this window.")
        else:
            self.subtitle.setText(
                "Nothing will run while the service is stopped.")

    def _update_sidebar(self) -> None:
        upcoming = []
        for job in self._jobs:
            if job.get("parent_id"):
                continue  # steps ride on their chain; the root speaks for them
            when = core.next_occurrence(job, jobs=self._jobs)
            if when is not None:
                upcoming.append((when, job))
        upcoming.sort(key=lambda pair: pair[0])
        if upcoming:
            when, job = upcoming[0]
            self.next_summary.setText(
                f"<b>{job.get('name', '')}</b><br>"
                f"<span style='color:{theme.AMBER}'>{when:%a %d %b · %H:%M}</span> "
                f"<span style='color:{theme.TEXT_FAINT}'>({core.humanize_delta(when)})</span>")
        else:
            self.next_summary.setText(
                f"<span style='color:{theme.TEXT_FAINT}'>Nothing is waiting to run.</span>")

        total = len(self._jobs)
        paused = sum(1 for j in self._jobs if not j.get("enabled", True))
        failed = sum(1 for j in self._jobs if j.get("status") == core.STATUS_FAILED)
        bits = [f"{total} total"]
        if paused:
            bits.append(f"{paused} paused")
        if failed:
            bits.append(f"{failed} failed")
        self.counts.setText(" · ".join(bits))

        if self._filter_date is None:
            self.day_title.setText("Every scheduled command")
        elif self._filter_date == date.today():
            self.day_title.setText(self._filter_date.strftime("Today, %d %B"))
        else:
            self.day_title.setText(self._filter_date.strftime("%A %d %B"))
        count = len(self._visible_jobs())
        self.day_count.setText(
            "nothing scheduled" if count == 0
            else f"{count} command{'' if count == 1 else 's'}")

    def _update_actions(self) -> None:
        job = self._selected_job()
        busy = self._worker is not None and self._worker.isRunning()
        for b in (self.btn_edit, self.btn_next, self.btn_delete,
                  self.btn_pause):
            b.setEnabled(job is not None and not busy)
        self.btn_detach.setEnabled(
            job is not None and not busy and bool(job.get("parent_id")))
        self.btn_run.setEnabled(job is not None and not busy)
        self.btn_clear_log.setEnabled(job is not None)
        if job is not None:
            self.btn_pause.setText("Resume" if not job.get("enabled", True) else "Pause")
        self.btn_run.setText("Running…" if busy else "Run it now")

    def _on_date_picked(self) -> None:
        picked = self.calendar.selectedDate().toPython()
        if self.btn_all_dates.isChecked():
            self.btn_all_dates.setChecked(False)  # triggers a refresh
        self._filter_date = picked
        self.refresh(force=True)

    def _on_all_dates(self, checked: bool) -> None:
        self._filter_date = None if checked else self.calendar.selectedDate().toPython()
        self.refresh(force=True)

    def _go_today(self) -> None:
        today = QDate.currentDate()
        self.calendar.setSelectedDate(today)
        self.calendar.setCurrentPage(today.year(), today.month())

    def _on_selection(self) -> None:
        job = self._selected_job()
        self._update_actions()
        if job is None:
            self.log_view.clear()
            self.log_title.setText("Output")
            return
        self.log_title.setText(f"Output of {job.get('name', '')}")
        text = core.read_log(job["id"])
        self.log_view.setPlainText(
            text or "This command has not produced any output yet.")
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    def _note(self, message: str | None) -> None:
        if message:
            self.statusBar().showMessage(message, 8000)
        self.refresh(force=True)

    def _add(self) -> None:
        dialog = JobDialog(self, initial_date=self._filter_date)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        v = dialog.values()
        job = core.new_job(v["name"], v["command"],
                           datetime.fromisoformat(v["run_at"]),
                           v["workdir"], v["repeat"], v["enabled"])
        if self._guard(core.add_job, job) is None:
            return
        if not core.daemon_status()["running"]:
            self._note("Scheduled. Start the service so it actually runs.")
        else:
            self._note(f"Scheduled for {datetime.fromisoformat(v['run_at']):%a %d %b at %H:%M}.")
        self._select_job(job["id"])

    def _edit(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        chain_info = None
        if job.get("parent_id"):
            parent = next((j for j in self._jobs
                           if j["id"] == job.get("parent_id")), None)
            chain_info = (
                f"Runs right after '{(parent or {}).get('name', 'its chain')}'. "
                f"Timing comes from the first step. Edit that one to move "
                f"the whole chain.")
        dialog = JobDialog(self, job=job, chain_info=chain_info)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if job.get("parent_id"):
            values.pop("run_at", None)
            values.pop("repeat", None)
        if self._guard(core.update_job, job["id"], values) is not None:
            self._note("Schedule updated.")
            self._select_job(job["id"])

    def _add_next(self) -> None:
        parent = self._selected_job()
        if parent is None:
            return
        dialog = NextStepDialog(self, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        v = dialog.values()
        try:
            job = core.add_next_step(parent["id"], v["name"], v["command"],
                                     v["workdir"])
        except (ValueError, core.StorageError) as e:
            QMessageBox.warning(self, "Could not add it", str(e))
            return
        self._note(f"'{v['name']}' will run right after '{parent['name']}'.")
        self.refresh(force=True)
        self._select_job(job["id"])

    def _detach(self) -> None:
        job = self._selected_job()
        if job is None or not job.get("parent_id"):
            return
        if self._guard(core.set_parent, job["id"], None) is not None:
            self._note("Detached. It now runs on its own time.")
            self._select_job(job["id"])

    def _delete(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        confirm = QMessageBox.question(
            self, "Delete this command?",
            f"'{job.get('name', '')}' will be removed from the schedule.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        kids = len(core.children_of(self._jobs, job["id"]))
        if self._guard(core.delete_job, job["id"]) is not None:
            core.clear_log(job["id"])
            self.log_view.clear()
            self._note("Deleted. Its next steps now follow the step above."
                       if kids else "Deleted.")

    def _toggle_enabled(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        if self._guard(core.toggle_job, job["id"]) is not None:
            self._note("Paused." if job.get("enabled", True) else "Resumed.")
            self._select_job(job["id"])

    def _run_now(self) -> None:
        job = self._selected_job()
        if job is None or (self._worker and self._worker.isRunning()):
            return
        self.statusBar().showMessage(f"Running '{job.get('name', '')}'…")
        self._worker = RunWorker(job, self)
        self._worker.done.connect(self._on_run_done)
        self._worker.failed.connect(self._on_run_failed)
        self._worker.finished.connect(self._update_actions)
        self._worker.start()
        self._update_actions()

    def _on_run_done(self, job_id: str, code: int, chained: int = 0) -> None:
        if code == 0:
            extra = f" (+{chained} next step{'s' if chained != 1 else ''})" if chained else ""
            self._note(f"Finished cleanly{extra}.")
        else:
            self._note(f"Finished with exit code {code}. "
                       f"Chained steps were skipped." if chained else
                       f"Finished with exit code {code}.")
        self._select_job(job_id)
        self._on_selection()

    def _on_run_failed(self, job_id: str, message: str) -> None:
        QMessageBox.warning(self, "Could not run it", message)
        self._note(None)

    def _clear_log(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        core.clear_log(job["id"])
        self.log_view.clear()

    def _toggle_service(self) -> None:
        if core.daemon_status()["running"]:
            self._note(stop_daemon())
            return
        message = start_daemon()
        self.statusBar().showMessage(message, 4000)
        QTimer.singleShot(1500, self._confirm_service_started)

    def _confirm_service_started(self) -> None:
        if core.daemon_status()["running"]:
            self._note("Background service is running. You can close this window.")
        else:
            self._note("The service did not come up. Check daemon.log in the data folder.")

    def _toggle_autostart(self) -> None:
        self._note(set_autostart(not is_autostart_enabled()))

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        self._timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        super().closeEvent(event)


def _install_excepthook() -> None:
    def hook(kind, value, tb) -> None:
        text = "".join(traceback.format_exception(kind, value, tb))
        try:
            with (core.get_data_dir() / "app-errors.log").open("a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}")
        except OSError:
            pass
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "Something went wrong", text[-1500:])

    sys.excepthook = hook


def main() -> int:
    _install_excepthook()
    app = QApplication(sys.argv)
    app.setApplicationName("Command Scheduler")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.stylesheet())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
