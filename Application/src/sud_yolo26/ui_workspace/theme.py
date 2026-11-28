"""Workspace-only themes. Persist appearance separately from scientific settings."""
from string import Template
from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

PALETTES = {
    'light': dict(bg='#f1f5f7', panel='#ffffff', raised='#eaf1f5', text='#253846',
        muted='#607783', line='#d5e2e9', accent='#177f85', on_accent='#ffffff',
        hover='#e6f3f2', selected='#d8eeed', disabled='#91a0a7', error='#9c2945',
        error_bg='#fff0f3', scene='#eef2f6', scene_text='#263849'),
    'dark': dict(bg='#060f1e', panel='#0b1b2e', raised='#10273e', text='#e5eef7',
        muted='#a0b5cb', line='#24465f', accent='#36dce8', on_accent='#04212b',
        hover='#12374b', selected='#104455', disabled='#61798f', error='#ff9ab0',
        error_bg='#361e31', scene='#081728', scene_text='#d5e6f5'),
}

_STYLE = Template('''
QMainWindow#Workspace, QWidget#WorkspaceRoot, QDialog {background:$bg;}
QWidget {color:$text;font-size:10pt;}
QWidget#Sidebar, QFrame#Panel {background:$panel;border:1px solid $line;border-radius:10px;}
QLabel#Brand {font-size:17pt;font-weight:600;color:$accent;}
QLabel#Heading {font-size:18pt;font-weight:600;}
QLabel#Muted {color:$muted;}
QLabel#Metric {font-size:19pt;font-weight:600;color:$accent;}
QLabel#DimensionsMetric {font-size:14pt;font-weight:600;color:$accent;}
QLabel#Section {font-size:12pt;font-weight:600;}
QPushButton, QToolButton {background:$panel;border:1px solid $line;border-radius:6px;padding:8px 12px;}
QPushButton:hover, QToolButton:hover {background:$hover;border-color:$accent;}
QPushButton:disabled, QToolButton:disabled {color:$disabled;background:$raised;border-color:$line;}
QPushButton#Primary {background:$accent;color:$on_accent;border-color:$accent;font-weight:600;}
QPushButton:checked, QToolButton:checked {background:$selected;color:$accent;border-color:$accent;}
QPushButton:focus, QComboBox:focus, QLineEdit:focus {border:1px solid $accent;}
QListWidget {background:$panel;border:1px solid $line;border-radius:6px;}
QListWidget::item {padding:9px;}
QListWidget::item:selected {background:$selected;color:$text;}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {background:$panel;border:1px solid $line;border-radius:5px;padding:7px;}
QComboBox QAbstractItemView {background:$panel;color:$text;selection-background-color:$selected;selection-color:$text;}
QTableWidget {background:$panel;alternate-background-color:$raised;gridline-color:$line;border:0;}
QHeaderView::section, QTableCornerButton::section {background:$raised;color:$text;padding:7px;border:0;font-weight:600;}
QTableWidget::item:selected {background:$selected;color:$text;}
QProgressBar {border:0;background:$raised;border-radius:4px;min-height:8px;}
QProgressBar::chunk {background:$accent;border-radius:4px;}
QTextEdit {background:$error_bg;color:$error;border:1px solid $line;}
QTabWidget::pane {border:0;}
QTabBar::tab {padding:12px 20px;background:$raised;color:$muted;border-bottom:3px solid transparent;}
QTabBar::tab:selected {background:$panel;color:$accent;border-bottom:3px solid $accent;}
QToolBar {background:$bg;border:0;spacing:3px;}
QToolButton {padding:4px;}
QSlider::groove:horizontal {height:5px;background:$line;border-radius:2px;}
QSlider::sub-page:horizontal {background:$accent;border-radius:2px;}
QSlider::handle:horizontal {width:14px;margin:-5px 0;background:$accent;border-radius:7px;}
QToolTip {background:$raised;color:$text;border:1px solid $line;padding:6px;}
QSplitter::handle {background:$line;}
''')


def style(name):
    result = _STYLE.substitute(PALETTES[name])
    if name == 'dark':
        result += '''
QWidget#Sidebar {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #0b1b2e,stop:1 #070f20);}
QPushButton#Primary {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #42e8ed,stop:1 #19bcd5);}
'''
    return result


STYLE = style('light')  # Compatibility for callers importing the former constant.


class ThemeController(QObject):
    changed = pyqtSignal(str)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings if settings is not None else QSettings('SUD', 'YOLO26')
        saved = self.settings.value('workspace/theme', 'dark')
        self.name = saved if saved in PALETTES else 'dark'

    def set_theme(self, name):
        if name not in PALETTES:
            raise ValueError('Unknown workspace theme')
        self.settings.setValue('workspace/theme', name)
        if self.name != name:
            self.name = name
            self.changed.emit(name)

    def toggle(self):
        self.set_theme('light' if self.name == 'dark' else 'dark')


def theme_controller():
    app = QApplication.instance()
    if app is None:
        raise RuntimeError('Create QApplication before the workspace theme')
    if not hasattr(app, '_sud_workspace_theme'):
        app._sud_workspace_theme = ThemeController(app)
    return app._sud_workspace_theme


def apply_theme(widget, name):
    colors = PALETTES[name]
    palette = QPalette(widget.palette())
    for role, key in [('Window', 'bg'), ('WindowText', 'text'), ('Base', 'panel'),
        ('AlternateBase', 'raised'), ('Text', 'text'), ('Button', 'panel'),
        ('ButtonText', 'text'), ('Highlight', 'selected'), ('HighlightedText', 'text'),
        ('ToolTipBase', 'raised'), ('ToolTipText', 'text'), ('Link', 'accent'), ('Light', 'line')]:
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors['disabled']))
    widget.setPalette(palette)
    widget.setStyleSheet(style(name))


def update_theme_button(button, name):
    button.setText('Светлая тема' if name == 'dark' else 'Тёмная тема')
    button.setToolTip('Переключить на светлую тему' if name == 'dark' else 'Переключить на тёмную тему')
    button.setAccessibleName(button.toolTip())


def workspace_font():
    """Matplotlib bundles a portable sans font with complete Cyrillic glyphs."""
    from pathlib import Path
    import matplotlib
    from PyQt6.QtGui import QFont,QFontDatabase
    path=Path(matplotlib.get_data_path())/'fonts/ttf/DejaVuSans.ttf'
    font_id=QFontDatabase.addApplicationFont(str(path))
    families=QFontDatabase.applicationFontFamilies(font_id)
    return QFont(families[0] if families else 'DejaVu Serif',10)
