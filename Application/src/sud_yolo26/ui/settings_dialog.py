from __future__ import annotations

from pathlib import PurePath

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QSpinBox,
)

from sud_yolo26.settings import service as cf
class SettingsDialog(QDialog):
    """Диалог настроек системы на PyQt6 с сохранением логики config.main."""

    def __init__(
        self,
        lang_of_interface: str,
        path_to_yolo_weights: str,
        default_input_dicom_folder: str,
        default_output_folder: str,
        current_color_theme: str,
        img_count: str,
        img_format: str,
        parent=None,
    ) -> None:
        super().__init__(parent)

        (
            self.tooltips,
            self.buttons,
            self.list_text,
            self.checkbox,
            self.main_menu,
            self.window_heads,
            self.titles,
        ) = cf.set_language(lang_of_interface)

        self.lang_of_interface = lang_of_interface
        self.path_to_yolo_weights = path_to_yolo_weights
        self.default_input_dicom_folder = default_input_dicom_folder
        self.default_output_folder = default_output_folder
        self.current_color_theme = current_color_theme
        self.img_count = int(img_count) if img_count.isdigit() else 100
        self.img_format = img_format

        self.setWindowTitle("Настройка параметров системы")
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout()
        form = QFormLayout()

        # Язык интерфейса
        self.lang_combo = QComboBox()
        for lang in cf.languages:
            self.lang_combo.addItem(lang)
        self.lang_combo.setCurrentText(self.lang_of_interface)

        # Входная папка DICOM
        self.input_edit = QLineEdit(self.default_input_dicom_folder)
        btn_input_browse = QPushButton("...")
        btn_input_browse.clicked.connect(self._browse_input_folder)
        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit)
        input_row.addWidget(btn_input_browse)

        # Выходная папка
        self.output_edit = QLineEdit(self.default_output_folder)
        btn_output_browse = QPushButton("...")
        btn_output_browse.clicked.connect(self._browse_output_folder)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(btn_output_browse)

        # Файл модели YOLO
        self.model_edit = QLineEdit(self.path_to_yolo_weights)
        btn_model_browse = QPushButton("...")
        btn_model_browse.clicked.connect(self._browse_model_file)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_edit)
        model_row.addWidget(btn_model_browse)

        # Цветовая тема
        self.color_combo = QComboBox()
        for theme in cf.color_themes:
            self.color_combo.addItem(theme)
        self.color_combo.setCurrentText(self.current_color_theme)
        btn_default_scheme = QPushButton(self.buttons[21])
        btn_default_scheme.clicked.connect(self._set_default_scheme)
        color_row = QHBoxLayout()
        color_row.addWidget(self.color_combo)
        color_row.addWidget(btn_default_scheme)

        # Кол-во файлов для выгрузки
        self.img_count_spin = QSpinBox()
        self.img_count_spin.setRange(1, 100000)
        self.img_count_spin.setValue(self.img_count)

        # Формат изображений
        self.img_format_combo = QComboBox()
        for f in cf.img_formats:
            self.img_format_combo.addItem(f)
        self.img_format_combo.setCurrentText(self.img_format)

        form.addRow(QLabel(self.list_text[26]), self.lang_combo)
        form.addRow(QLabel(self.list_text[22]), input_row)
        form.addRow(QLabel(self.list_text[23]), output_row)
        form.addRow(QLabel(self.list_text[24]), model_row)
        form.addRow(QLabel(self.list_text[25]), color_row)
        form.addRow(QLabel("Кол-во файлов для выгрузки"), self.img_count_spin)
        form.addRow(QLabel("Формат изображений"), self.img_format_combo)

        main_layout.addLayout(form)

        # Кнопки сохранить / отмена
        buttons_row = QHBoxLayout()
        btn_save = QPushButton(self.buttons[20])
        btn_cancel = QPushButton(self.buttons[5])
        btn_save.clicked.connect(self._on_save)
        btn_cancel.clicked.connect(self.reject)
        buttons_row.addStretch(1)
        buttons_row.addWidget(btn_save)
        buttons_row.addWidget(btn_cancel)

        main_layout.addLayout(buttons_row)
        self.setLayout(main_layout)
        self.setModal(True)

    # ==== обработчики ====

    def _browse_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self.list_text[22],
            self.default_input_dicom_folder,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self.input_edit.setText(folder)

    def _browse_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self.list_text[23],
            self.default_output_folder,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self.output_edit.setText(folder)

    def _browse_model_file(self) -> None:
        folder_model = str(PurePath(self.path_to_yolo_weights).parent)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.list_text[24],
            folder_model,
            "Model files (*.*)",
        )
        if file_path:
            self.model_edit.setText(file_path)

    def _set_default_scheme(self) -> None:
        self.color_combo.setCurrentText("SystemDefault")

    def _on_save(self) -> None:
        lang_of_interface = self.lang_combo.currentText()
        path_to_yolo_weights = self.model_edit.text()
        default_input_dicom_folder = self.input_edit.text()
        default_output_folder = self.output_edit.text()
        current_color_theme = self.color_combo.currentText()
        img_count = str(self.img_count_spin.value())
        img_format = self.img_format_combo.currentText()

        cf.save_settings(
            lang_of_interface,
            path_to_yolo_weights,
            default_input_dicom_folder,
            default_output_folder,
            current_color_theme,
            img_count,
            img_format,
        )

        self.accept()


def show_settings_dialog(
    lang_of_interface: str,
    path_to_yolo_weights: str,
    default_input_dicom_folder: str,
    default_output_folder: str,
    current_color_theme: str,
    img_count: str,
    img_format: str,
    parent=None,
) -> None:
    """Открыть диалог настроек и при необходимости сохранить изменения."""
    dialog = SettingsDialog(
        lang_of_interface,
        path_to_yolo_weights,
        default_input_dicom_folder,
        default_output_folder,
        current_color_theme,
        img_count,
        img_format,
        parent=parent,
    )
    dialog.exec()
