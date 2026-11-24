"""Use a bundled Unicode font so Russian labels also render without OS font fallback."""
from PyQt6.QtGui import QFont, QFontDatabase
from .paths import RESOURCES


def configure_font(app):
    font_id=QFontDatabase.addApplicationFont(str(RESOURCES/'DejaVuSerifCondensed.ttf'))
    families=QFontDatabase.applicationFontFamilies(font_id) if font_id>=0 else []
    app.setFont(QFont(families[0] if families else 'Segoe UI',10))
