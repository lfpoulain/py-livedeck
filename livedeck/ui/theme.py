"""Palette et feuille de style (même langage visuel que MixPanel)."""
from pathlib import Path

CHEVRON = (Path(__file__).parent / "assets" / "chevron.svg").as_posix()

BG = "#0d0f13"
PANEL = "#14171d"
PANEL2 = "#1a1e26"
RAISED = "#222733"
BORDER = "#272c37"
TEXT = "#e7eaf0"
MUTED = "#8b93a5"
FAINT = "#5a6273"
ACCENT = "#2dd4bf"
DANGER = "#f87171"
OK = "#34d399"
WARN = "#fbbf24"
PGM = "#ef4444"
PVW = "#22c55e"

SWATCHES = ["#000000", "#1f2937", "#330000", "#003300", "#000033", "#333300", "#330033", "#e5262b",
            "#ff8000", "#facc15", "#22c55e", "#2dd4bf", "#3b82f6", "#a855f7", "#ffffff"]
FG_SWATCHES = ["#ffffff", "#000000", "#e7eaf0", "#facc15", "#22c55e", "#e5262b"]
FB_SWATCHES = ["#e5262b", "#22c55e", "#facc15", "#ff8000", "#3b82f6", "#a855f7", "#ffffff", "#000000"]

QSS = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#Root {{ background: {BG}; }}
QFrame#Header {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QFrame#Side {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Card {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 10px; }}
QWidget#Transparent {{ background: transparent; }}
QLabel#Brand {{ font-size: 17px; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#BrandAccent {{ font-size: 17px; font-weight: 700; color: {ACCENT}; }}
QLabel#H1 {{ font-size: 18px; font-weight: 650; }}
QLabel#H2 {{ font-size: 11px; font-weight: 700; color: {MUTED}; letter-spacing: 1.2px; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#Toast {{ color: {ACCENT}; font-size: 12px; font-weight: 600; }}
QLabel#Chip {{ background: {RAISED}; border-radius: 9px; padding: 2px 9px; color: {MUTED}; font-size: 11px; font-weight: 600; }}
QLabel#Pill {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 13px; padding: 4px 12px; font-size: 12px; }}

QPushButton {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; font-weight: 600; }}
QPushButton:hover {{ background: #2a3040; border-color: #343b4a; }}
QPushButton:pressed {{ background: #1c2029; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton::menu-indicator {{ image: url("{CHEVRON}"); subcontrol-position: right center; right: 8px; width: 10px; }}
QPushButton#Primary {{ background: {ACCENT}; color: #04211d; border: none; }}
QPushButton#Primary:hover {{ background: #5eead4; }}
QPushButton#Primary:pressed {{ background: #14b8a6; }}
QPushButton#Danger {{ background: transparent; color: {DANGER}; border-color: #4a2a2e; }}
QPushButton#Danger:hover {{ background: #2a1a1d; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {MUTED}; padding: 6px 10px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; background: {PANEL2}; }}
QPushButton#Ghost:checked {{ color: {ACCENT}; }}
QPushButton#Small {{ padding: 5px 10px; font-size: 12px; }}
QPushButton#Seg {{ background: transparent; border: none; border-radius: 7px; padding: 6px 14px; color: {MUTED}; }}
QPushButton#Seg:checked {{ background: {RAISED}; color: {TEXT}; }}
QPushButton#SegLive {{ background: transparent; border: none; border-radius: 7px; padding: 6px 14px; color: {MUTED}; }}
QPushButton#SegLive:checked {{ background: #3a1518; color: #fca5a5; }}
QFrame#SegBox {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 9px; }}
QPushButton#Tile {{ text-align: left; padding: 9px 12px; border-radius: 9px; background: {PANEL2}; font-weight: 600; }}
QPushButton#Tile:hover {{ background: {RAISED}; border-color: #3a4252; }}

QComboBox, QLineEdit, QSpinBox, QPlainTextEdit#Field {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px; selection-background-color: {ACCENT}; selection-color: #04211d; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QPlainTextEdit#Field:hover {{ border-color: #384053; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QPlainTextEdit#Field:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{CHEVRON}"); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 4px; outline: none; }}
QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {PANEL2}; }}
QListWidget::item:selected {{ background: {RAISED}; color: {TEXT}; }}
QListWidget#Steps {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; }}

QPlainTextEdit#Monitor {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; color: {MUTED}; padding: 6px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {RAISED}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}
QCheckBox {{ spacing: 8px; color: {MUTED}; }}
QCheckBox::indicator {{ width: 30px; height: 16px; border-radius: 8px; background: {RAISED}; border: 1px solid {BORDER}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {RAISED}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::section {{ color: {MUTED}; padding: 8px 10px 3px; font-size: 11px; font-weight: 700; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMessageBox {{ background: {PANEL}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; color: {MUTED}; font-weight: 600; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar QToolButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {RAISED}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; margin: -6px 0; border-radius: 7px; }}
"""
