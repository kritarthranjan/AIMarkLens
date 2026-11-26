"""Dark theme: colour tokens, Fusion palette and the application style sheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

C = {
    "bg": "#0E1116", "rail": "#0A0C10", "surface": "#151A21", "surface_alt": "#1A2029", "raised": "#212836",
    "input": "#10141A", "border": "#262E3A", "border_strong": "#364050",
    "text": "#E7EBF2", "muted": "#98A2B3", "faint": "#6B7585",
    "accent": "#2563EB", "accent_hover": "#3B82F6", "accent_pressed": "#1D4ED8", "focus": "#93C5FD",
    "selection": "#1E3A5F",
    "ok": "#3CC48A", "warning": "#E8A93B", "error": "#EF6B6B", "signal": "#B48CFF", "info": "#5BB4F0",
    "neutral": "#98A2B3",
}

TONE = {"ok": C["ok"], "warning": C["warning"], "error": C["error"], "signal": C["signal"],
        "info": C["info"], "neutral": C["neutral"]}

TONE_LABEL = {"ok": "OK", "warning": "Warning", "error": "Error", "signal": "Signal", "info": "Info",
              "neutral": "Note"}

UI_FONTS = ["Segoe UI Variable Text", "Segoe UI", "Arial"]
MONO_FONTS = ["Cascadia Mono", "Consolas", "Courier New"]


def tone_colour(tone: str, alpha: int = 255) -> QColor:
    colour = QColor(TONE.get(tone, C["neutral"]))
    colour.setAlpha(alpha)
    return colour


def mono_font(point_size: float = 9.5) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FONTS)
    font.setPointSizeF(point_size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _qss(check: str = "") -> str:
    c = C
    return f"""
QWidget {{ color: {c['text']}; }}
QMainWindow, QDialog, #Content {{ background: {c['bg']}; }}
QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border_strong']};
           padding: 6px 8px; border-radius: 6px; }}
#Rail {{ background: {c['rail']}; border-right: 1px solid {c['border']}; }}
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#Inset {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: 17pt; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 12.5pt; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 10.5pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="caption"] {{ color: {c['faint']}; font-size: 8.8pt; }}
QLabel[role="brand"] {{ font-size: 13pt; font-weight: 700; }}

QPushButton {{ background: {c['surface_alt']}; border: 1px solid {c['border_strong']}; border-radius: 8px;
              padding: 7px 14px; min-height: 20px; outline: none; }}
QPushButton:hover {{ background: {c['raised']}; border-color: #4A5566; }}
QPushButton:pressed {{ background: #161B23; }}
QPushButton:focus {{ border: 2px solid {c['focus']}; padding: 6px 13px; }}
QPushButton:disabled {{ color: {c['faint']}; background: #13171D; border-color: {c['border']}; }}
QPushButton[variant="primary"] {{ background: {c['accent']}; border: 1px solid {c['accent']}; color: #FFFFFF; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[variant="primary"]:pressed {{ background: {c['accent_pressed']}; }}
QPushButton[variant="primary"]:focus {{ border: 2px solid {c['focus']}; }}
QPushButton[variant="primary"]:disabled {{ background: #1F2D4A; border-color: #1F2D4A; color: #8391AD; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {c['surface_alt']}; border-color: {c['border']}; }}
QPushButton[variant="ghost"]:focus {{ border: 2px solid {c['focus']}; }}
QPushButton[variant="link"] {{ background: transparent; border: 1px solid transparent; color: {c['accent_hover']};
                               padding: 4px 6px; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}
QPushButton[variant="link"]:focus {{ border: 2px solid {c['focus']}; padding: 3px 5px; }}

QToolButton#NavButton {{ background: transparent; border: 2px solid transparent; border-radius: 8px;
                         padding: 8px 10px; color: {c['muted']}; font-size: 10.5pt; text-align: left; }}
QToolButton#NavButton:hover {{ background: {c['surface']}; color: {c['text']}; }}
QToolButton#NavButton:checked {{ background: #18202E; color: {c['text']}; }}
QToolButton#NavButton:focus {{ border: 2px solid {c['focus']}; }}

QPushButton#Segment {{ background: transparent; border: 1px solid transparent; border-radius: 7px;
                       padding: 5px 14px; color: {c['muted']}; min-height: 18px; }}
QPushButton#Segment:hover {{ color: {c['text']}; background: {c['surface_alt']}; }}
QPushButton#Segment:checked {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border_strong']}; }}
QPushButton#Segment:focus {{ border: 2px solid {c['focus']}; padding: 4px 13px; }}
QFrame#SegmentedControl {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 9px; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {c['input']}; border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {c['accent']}; selection-color: #FFFFFF; }}
QPlainTextEdit, QTextEdit {{ padding: 4px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {c['focus']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {c['faint']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['border_strong']};
                               selection-background-color: {c['selection']}; }}

QCheckBox {{ spacing: 9px; padding: 3px 4px; border-radius: 6px; border: 2px solid transparent; }}
QCheckBox::indicator, QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid #56627A;
    border-radius: 4px; background: {c['input']}; }}
QCheckBox::indicator:hover, QListWidget::indicator:hover {{ border-color: {c['accent_hover']}; }}
QCheckBox::indicator:checked, QListWidget::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
    image: url({check}); }}
QCheckBox::indicator:disabled {{ background: #13171D; border-color: {c['border']}; }}
QCheckBox:focus {{ border: 2px solid {c['focus']}; }}
QCheckBox:disabled {{ color: {c['faint']}; }}

QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 8px 14px; border: none;
               border-bottom: 2px solid transparent; margin-right: 2px; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar:focus {{ outline: none; }}
QTabBar::tab:focus {{ border: 2px solid {c['focus']}; border-radius: 6px; }}
QTabBar QToolButton {{ background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: 4px; }}

QTableView, QListWidget, QTreeView {{ background: #11151B; alternate-background-color: #141921;
    border: 1px solid {c['border']}; border-radius: 8px; gridline-color: transparent;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; }}
QTableView:focus, QListWidget:focus {{ border: 1px solid {c['focus']}; }}
QListWidget::item {{ padding: 4px 6px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {c['selection']}; }}
QHeaderView::section {{ background: #161B23; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']};
                        padding: 6px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: #161B23; border: none; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2F3846; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #3E4959; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2F3846; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: #3E4959; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 4px;
               max-height: 8px; min-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {c['border']}; }}
QMenu {{ background: {c['raised']}; border: 1px solid {c['border_strong']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['selection']}; }}
QStatusBar {{ background: {c['rail']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(UI_FONTS)
    font.setPointSizeF(10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: C["bg"], QPalette.ColorRole.WindowText: C["text"],
        QPalette.ColorRole.Base: C["input"], QPalette.ColorRole.AlternateBase: "#141921",
        QPalette.ColorRole.Text: C["text"], QPalette.ColorRole.Button: C["surface_alt"],
        QPalette.ColorRole.ButtonText: C["text"], QPalette.ColorRole.Highlight: C["accent"],
        QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.ToolTipBase: C["raised"],
        QPalette.ColorRole.ToolTipText: C["text"], QPalette.ColorRole.PlaceholderText: C["faint"],
        QPalette.ColorRole.Link: C["accent_hover"], QPalette.ColorRole.BrightText: "#FFFFFF",
        QPalette.ColorRole.Light: "#2A3240", QPalette.ColorRole.Midlight: "#222936",
        QPalette.ColorRole.Mid: "#1A2029", QPalette.ColorRole.Dark: "#0B0E12", QPalette.ColorRole.Shadow: "#000000",
    }
    for role, value in roles.items():
        p.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(C["faint"]))
    app.setPalette(p)
    app.setStyleSheet(_qss(_check_image()))


def _check_image() -> str:
    """Render the checkbox tick once to a cache file, since style sheets need an image URL."""
    import os
    import tempfile

    from PySide6.QtCore import QStandardPaths

    from AIMarkLens.ui import icons

    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or tempfile.gettempdir()
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "check-v1.png")
        if not os.path.exists(path):
            icons.pixmap("check", "#FFFFFF", 16, 2.0).save(path, "PNG")
        return path.replace("\\", "/")
    except OSError:
        return ""
