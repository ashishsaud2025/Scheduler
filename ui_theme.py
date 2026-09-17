"""Colour tokens and the application stylesheet."""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase

INK = "#12161F"          # window base
PANEL = "#1A2130"        # panels and sidebar
RAISED = "#232C3E"       # inputs, list items, calendar body
RAISED_HOVER = "#2A344A"
LINE = "#323E57"         # borders and dividers
TEXT = "#E6EAF3"
TEXT_MUTED = "#8A97B1"
TEXT_FAINT = "#5F6B83"

AMBER = "#E9A33C"        # time, today, primary action
AMBER_DEEP = "#C4842A"
AMBER_TINT = "#3A2F1C"

GREEN = "#4FB286"
RED = "#E0574B"
BLUE = "#4F9BE0"
GREY = "#6E7B93"

STATUS_COLORS = {
    "pending": AMBER,
    "running": BLUE,
    "done": GREEN,
    "failed": RED,
    "disabled": GREY,
}

STATUS_WORDS = {
    "pending": "scheduled",
    "running": "running",
    "done": "finished",
    "failed": "failed",
    "disabled": "paused",
}


def mono_family() -> str:
    """First available monospace family, for commands and log output."""
    available = set(QFontDatabase.families())
    for name in ("Cascadia Mono", "Consolas", "JetBrains Mono", "SF Mono",
                 "DejaVu Sans Mono", "Menlo", "Courier New"):
        if name in available:
            return name
    return "monospace"


def ui_family() -> str:
    available = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text",
                 "Ubuntu", "DejaVu Sans"):
        if name in available:
            return name
    return "sans-serif"


def stylesheet() -> str:
    ui = ui_family()
    mono = mono_family()
    return f"""
QWidget {{
    background: {INK};
    color: {TEXT};
    font-family: "{ui}";
    font-size: 13px;
}}

QMainWindow, QDialog {{ background: {INK}; }}
QLabel {{ background: transparent; }}
QFrame {{ background: transparent; }}

#headerBar {{
    background: {PANEL};
    border-bottom: 1px solid {LINE};
}}
#appTitle {{ font-size: 17px; font-weight: 600; letter-spacing: 0.2px; }}
#appSubtitle {{ color: {TEXT_FAINT}; font-size: 12px; }}

#sidebar {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 14px;
}}
#contentPanel {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 14px;
}}

#dayTitle {{ font-size: 16px; font-weight: 600; }}
#dayCount {{ color: {TEXT_MUTED}; font-size: 12px; }}
#sectionLabel {{ color: {TEXT_FAINT}; font-size: 12px; }}
#hint {{ color: {TEXT_FAINT}; font-size: 12px; }}
#warn {{ color: {AMBER}; font-size: 12px; }}

/* buttons */

QPushButton {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background: {RAISED_HOVER}; border-color: #3E4C6B; }}
QPushButton:pressed {{ background: #1D2536; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: #1B2130; border-color: #262F42; }}

QPushButton#primary {{
    background: {AMBER};
    color: #201704;
    border: 1px solid {AMBER};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #F2B052; border-color: #F2B052; }}
QPushButton#primary:pressed {{ background: {AMBER_DEEP}; }}

QPushButton#danger:hover {{ background: #3A2320; border-color: {RED}; color: #FFD9D4; }}

QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {TEXT_MUTED};
    padding: 5px 9px;
}}
QPushButton#ghost:hover {{ color: {TEXT}; background: {RAISED}; }}
QPushButton#ghost:checked {{ color: {AMBER}; background: {AMBER_TINT}; }}

QPushButton#chip {{
    background: transparent;
    border: 1px solid {LINE};
    border-radius: 13px;
    padding: 5px 12px;
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QPushButton#chip:hover {{ color: {TEXT}; border-color: {AMBER}; }}

/* inputs */

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QTimeEdit, QSpinBox {{
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {AMBER_DEEP};
    selection-color: #14100A;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QTimeEdit:focus {{ border-color: {AMBER}; }}
QLineEdit:disabled {{ color: {TEXT_FAINT}; }}
QLineEdit#mono, QPlainTextEdit#mono, QTextEdit#logView {{
    font-family: "{mono}";
    font-size: 12px;
}}

QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {RAISED};
    border: 1px solid {LINE};
    selection-background-color: {AMBER_TINT};
    selection-color: {TEXT};
    outline: none;
}}

QTimeEdit {{ font-family: "{mono}"; font-size: 15px; }}
QTimeEdit::up-button, QTimeEdit::down-button {{ width: 18px; background: transparent; border: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {LINE};
    border-radius: 4px;
    background: {RAISED};
}}
QCheckBox::indicator:checked {{ background: {AMBER}; border-color: {AMBER}; }}

/* job list (legacy) */

QListWidget#jobList {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget#jobList::item {{
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: 10px;
    margin: 0px 2px 8px 2px;
}}
QListWidget#jobList::item:hover {{ background: {RAISED_HOVER}; }}
QListWidget#jobList::item:selected {{
    background: #2B3752;
    border: 1px solid {AMBER};
}}

/* job tree: chains. The branch lines stay faint so nesting reads quietly. */

QTreeWidget#jobTree {{
    background: transparent;
    border: none;
    outline: none;
}}
QTreeWidget#jobTree::item {{
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: 10px;
    margin: 0px 2px 8px 2px;
}}
QTreeWidget#jobTree::item:hover {{ background: {RAISED_HOVER}; }}
QTreeWidget#jobTree::item:selected {{
    background: #2B3752;
    border: 1px solid {AMBER};
}}
QTreeWidget#jobTree::branch {{
    background: transparent;
}}
QTreeWidget#jobTree::branch:has-children {{
    image: none;
}}

#jobName {{ font-size: 13px; font-weight: 600; }}
#jobCommand {{ font-family: "{mono}"; font-size: 12px; color: {TEXT_MUTED}; }}
#jobTime {{ font-family: "{mono}"; font-size: 15px; font-weight: 600; }}
#jobMeta {{ color: {TEXT_FAINT}; font-size: 11px; }}

/* calendar */

QCalendarWidget {{ background: transparent; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background: transparent;
    border-bottom: 1px solid {LINE};
    padding: 2px 0px 6px 0px;
}}
QCalendarWidget QToolButton {{
    background: transparent;
    border: none;
    border-radius: 7px;
    color: {TEXT};
    font-size: 14px;
    font-weight: 600;
    padding: 5px 10px;
}}
QCalendarWidget QToolButton:hover {{ background: {RAISED}; }}
QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
QCalendarWidget QMenu {{
    background: {RAISED};
    border: 1px solid {LINE};
    padding: 4px;
}}
QCalendarWidget QMenu::item:selected {{ background: {AMBER_TINT}; }}
QCalendarWidget QSpinBox {{
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: 6px;
}}
QCalendarWidget QAbstractItemView {{
    background: {RAISED};
    border: none;
    border-radius: 10px;
    outline: none;
    alternate-background-color: {RAISED};
    selection-background-color: {AMBER};
    selection-color: #201704;
    font-size: 13px;
    padding: 4px;
}}
QCalendarWidget QTableView {{ background: {RAISED}; border-radius: 10px; }}

/* misc chrome */

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #33405A; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #42527180; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; width: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #33405A; border-radius: 5px; min-width: 28px; }}

QStatusBar {{ background: {PANEL}; color: {TEXT_MUTED}; border-top: 1px solid {LINE}; }}
QStatusBar::item {{ border: none; }}

QToolTip {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 5px 8px;
}}

QMessageBox {{ background: {PANEL}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""
