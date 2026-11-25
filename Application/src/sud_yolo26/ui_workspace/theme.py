"""Styles applied to the workspace window only."""
STYLE='''
QMainWindow#Workspace {background:#f3f6f8;}
QWidget#Sidebar, QFrame#Panel {background:white;border:1px solid #dce6eb;border-radius:9px;}
QWidget {color:#243944;font-size:10pt;}
QLabel#Brand {font-size:17pt;font-weight:600;color:#177f85;}
QLabel#Heading {font-size:18pt;font-weight:600;}
QLabel#Muted {color:#607783;}
QPushButton {background:white;border:1px solid #d6e2e7;border-radius:6px;padding:8px 12px;}
QPushButton:hover {background:#e6f3f2;}
QPushButton:disabled {color:#91a0a7;background:#edf1f3;}
QPushButton#Primary {background:#177f85;color:white;border-color:#177f85;font-weight:600;}
QPushButton:checked {background:#e6f3f2;color:#177f85;border-color:#177f85;}
QListWidget {background:white;border:1px solid #dce6eb;border-radius:6px;}
QListWidget::item {padding:9px;}
QListWidget::item:selected {background:#e6f3f2;color:#135f65;}
QComboBox {background:white;border:1px solid #d6e2e7;border-radius:5px;padding:7px;}
QTableWidget {background:white;gridline-color:#e5ecef;border:0;}
QHeaderView::section {background:#f1f5f7;padding:7px;border:0;font-weight:600;}
QProgressBar {border:0;background:#dce9eb;border-radius:4px;min-height:8px;}
QProgressBar::chunk {background:#177f85;border-radius:4px;}
QTextEdit {background:#fff4ed;border:1px solid #ecd5c7;}
'''


def workspace_font():
    """Matplotlib bundles a portable sans font with complete Cyrillic glyphs."""
    from pathlib import Path
    import matplotlib
    from PyQt6.QtGui import QFont,QFontDatabase
    path=Path(matplotlib.get_data_path())/'fonts/ttf/DejaVuSans.ttf'
    font_id=QFontDatabase.addApplicationFont(str(path))
    families=QFontDatabase.applicationFontFamilies(font_id)
    return QFont(families[0] if families else 'DejaVu Serif',10)
