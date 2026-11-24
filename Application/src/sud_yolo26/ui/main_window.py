import os
import sys
from pathlib import Path


from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QFileDialog,
    QMessageBox,
)

from sud_yolo26.analysis import parameters as sp
from sud_yolo26.settings import service as cf
from sud_yolo26.ui import settings_dialog as config_qt
from sud_yolo26.detection import service as ds
from sud_yolo26.imaging import importer as gi
from sud_yolo26.dataset import labels as gs
from sud_yolo26.ui import views as kidney_qt_views
class KidneyMainWindow(QMainWindow):
    """Главное окно приложения на PyQt6, сохраняющее функциональность PySimpleGUI-версии."""

    def __init__(self) -> None:
        super().__init__()

        (
            self.lang_of_interface,
            self.path_to_yolo_weights,
            self.default_input_dicom_folder,
            self.default_output_folder,
            self.current_color_theme,
            self.img_count,
            self.img_format,
        ) = cf.read_settings()
        (
            self.tooltips,
            self.buttons,
            self.list_text,
            self.checkbox,
            self.main_menu,
            self.window_heads,
            self.titles,
        ) = cf.set_language(self.lang_of_interface)

        self.setWindowTitle(self.window_heads[0])
        self._init_ui()
        self.adjustSize()
        # Окно открывается обычным компактным размером по содержимому.
        self.setMinimumSize(self.sizeHint())

    def _init_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Кнопки соответствуют buttons[0], [1], [3], [4], [14], [5] в kidney_GUI.main
        btn_open_ct = QPushButton(self.buttons[0])
        btn_view_slices = QPushButton(self.buttons[1])
        btn_detected_images = QPushButton(self.buttons[3])
        btn_train_dataset = QPushButton(self.buttons[4])
        btn_settings = QPushButton(self.buttons[14])
        btn_exit = QPushButton(self.buttons[5])

        btn_open_ct.setToolTip(self.tooltips[0])
        btn_view_slices.setToolTip(self.tooltips[1])
        btn_detected_images.setToolTip(self.tooltips[3])
        btn_train_dataset.setToolTip(self.tooltips[4])
        btn_settings.setToolTip(self.tooltips[9])
        btn_exit.setToolTip(self.tooltips[5])

        btn_open_ct.clicked.connect(self.on_open_ct)
        btn_view_slices.clicked.connect(self.on_view_slices)
        btn_detected_images.clicked.connect(self.on_view_detected_images)
        btn_train_dataset.clicked.connect(self.on_train_dataset)
        btn_settings.clicked.connect(self.on_settings)
        btn_exit.clicked.connect(self.close)
        btn_train_dataset.setVisible(False)

        for b in (
            btn_open_ct,
            btn_view_slices,
            btn_detected_images,
            btn_train_dataset,
            btn_settings,
            btn_exit,
        ):
            b.setMinimumHeight(48)
            layout.addWidget(b)

        central.setLayout(layout)
        self.setCentralWidget(central)

    # ====== СЛОТЫ, ПОВТОРЯЮЩИЕ ЛОГИКУ kidney_GUI.main ======

    def _select_directory(self, caption: str, initial: str) -> str:
        directory = QFileDialog.getExistingDirectory(
            self,
            caption,
            initial if initial else str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        return directory or ""

    def _show_critical(self, message: str) -> None:
        QMessageBox.critical(self, self.titles[2], message)

    def on_open_ct(self) -> None:
        """
        Аналог ветки:
        if event in (buttons[0], 'Open'):
            data_path = gi.get_dicom_path(default_input_dicom_folder)
            ...
        Здесь вместо PySimpleGUI используем стандартные Qt-диалоги.
        """
        from sud_yolo26.application import state as km

        km.global_messages.clear()

        data_path = self._select_directory(self.list_text[8], self.default_input_dicom_folder)
        if not data_path:
            self._show_critical(self.list_text[10] + self.list_text[8][6:])
            return

        output_path = self._select_directory(self.list_text[0], self.default_output_folder)
        if not output_path:
            self._show_critical(self.list_text[10] + "для сохранения снимков")
            return

        try:
            gi.main(data_path, output_path)
        except Exception as exc:
            self._show_critical(str(exc))
            return

        if km.global_messages:
            QMessageBox.information(
                self,
                self.window_heads[3],
                "\n".join(map(str, km.global_messages)),
            )
            km.global_messages.clear()

    def on_view_slices(self) -> None:
        """
        Аналог ветки:
        elif event in (buttons[1], 'View slice', 'Images for detect'):
        """
        start_folder = self.default_output_folder
        input_path = self._select_directory(self.list_text[0], start_folder)
        if not input_path or not os.path.exists(input_path):
            kidney_qt_views.show_warning(self.list_text[9], parent=self)
            return

        img_files = [
            os.path.join(input_path, f)
            for f in os.listdir(input_path)
            if f.lower().endswith(str(self.img_format))
        ]
        if not img_files:
            kidney_qt_views.show_warning(self.list_text[9], parent=self)
            return

        viewer = kidney_qt_views.ImageFolderViewer(input_path, parent=self)
        viewer.exec()

    def on_view_detected_images(self) -> None:
        """
        Аналог ветки:
        elif event in (buttons[3], 'Detected images'):
        """
        start_folder = self.default_output_folder
        input_path = self._select_directory(self.list_text[6], start_folder)
        if not input_path or not os.path.exists(input_path):
            kidney_qt_views.show_warning(self.list_text[4], parent=self)
            return

        img_files = [
            os.path.join(input_path, f)
            for f in os.listdir(input_path)
            if f.lower().endswith(str(self.img_format))
        ]
        if not img_files:
            kidney_qt_views.show_warning(self.list_text[4], parent=self)
            return

        viewer = kidney_qt_views.DetectedImagesViewer(
            os.path.join(input_path, "detect"), parent=self
        )
        viewer.exec()

    def on_train_dataset(self) -> None:
        """
        Аналог ветки:
        elif event in (buttons[4], 'Detected images'):
            gs.main()
        """
        gs.main()

    def on_settings(self) -> None:
        """
        Раньше вызывалось:
        cf.main(...)
        main_window.close()
        sets()
        main()
        Теперь используем диалог на PyQt6 и затем перечитываем настройки,
        чтобы обновить состояние.
        """
        config_qt.show_settings_dialog(
            self.lang_of_interface,
            self.path_to_yolo_weights,
            self.default_input_dicom_folder,
            self.default_output_folder,
            self.current_color_theme,
            self.img_count,
            self.img_format,
        )

        (
            self.lang_of_interface,
            self.path_to_yolo_weights,
            self.default_input_dicom_folder,
            self.default_output_folder,
            self.current_color_theme,
            self.img_count,
            self.img_format,
        ) = cf.read_settings()
        (
            self.tooltips,
            self.buttons,
            self.list_text,
            self.checkbox,
            self.main_menu,
            self.window_heads,
            self.titles,
        ) = cf.set_language(self.lang_of_interface)


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = KidneyMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
