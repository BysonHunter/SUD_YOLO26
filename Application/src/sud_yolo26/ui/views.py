from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QGuiApplication, QColor, QBrush, QImage
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QCheckBox,
    QRadioButton,
    QFileDialog,
    QMessageBox,
    QTextEdit,
    QWidget,
    QTabWidget,
    QProgressBar,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QStyle,
    QProgressDialog,
    QApplication,
)

from sud_yolo26.settings import service as cf
from sud_yolo26.detection import service as ds
from sud_yolo26.dataset import labels as gs
from sud_yolo26.analysis import parameters as sp
from sud_yolo26.application import state as km
from sud_yolo26.application.files import image_filenames
from sud_yolo26.ui.image_coordinates import ImageCoordinates
from sud_yolo26.reporting import pdf_report as pw
from sud_yolo26.visualization import rendering as v3d
from sud_yolo26.visualization.stone_geometry import legacy_spacing_xyz, cube_xyz
from sud_yolo26.lithotripsy.config import Config


def _load_ui_text():
    lang_of_interface, path_to_yolo_weights, default_input_dicom_folder, default_output_folder, current_color_theme, \
        img_count, img_format = cf.read_settings()
    tooltips, buttons, list_text, checkbox, main_menu, window_heads, titles = cf.set_language(lang_of_interface)
    return (
        lang_of_interface,
        path_to_yolo_weights,
        default_input_dicom_folder,
        default_output_folder,
        current_color_theme,
        img_count,
        img_format,
        tooltips,
        buttons,
        list_text,
        checkbox,
        main_menu,
        window_heads,
        titles,
    )


class StaticImageLabel(QLabel):
    """Aspect-fitted preview with no coordinate overlays or zoom controls."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self,path):
        self._source_pixmap = QPixmap(str(path))
        self._fit_image()

    def _fit_image(self):
        if self._source_pixmap.isNull():
            super().clear()
        else:
            self.setPixmap(self._source_pixmap.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'_source_pixmap'):
            self._fit_image()


class ClickableLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._callback = None

    def set_clicked_callback(self, cb):
        self._callback = cb

    def mousePressEvent(self, event):
        if self._callback is not None:
            self._callback()
        return super().mousePressEvent(event)


class StonesCalcWorker(QThread):
    """Поток для расчёта параметров камней, чтобы не блокировать GUI."""
    finished = pyqtSignal(object)   # result dict or None
    error = pyqtSignal(str)

    def __init__(self, parent_folder: Path, parent=None):
        super().__init__(parent)
        self.parent_folder = parent_folder

    def run(self):
        try:
            result = sp.main(self.parent_folder, show_ui=False)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class StonesProgressDialog(QDialog):
    """Окно с крупным прогресс-баром во время расчёта параметров камней."""
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Расчёт параметров камней")
        self.setMinimumWidth(420)
        self.setMinimumHeight(140)
        layout = QVBoxLayout(self)
        self.label = QLabel("Идёт расчёт параметров камней. Пожалуйста, подождите...")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)  # индикатор «в процессе»
        self.progress.setMinimumHeight(28)
        layout.addWidget(self.progress)
        self.setLayout(layout)


class ImageFolderViewer(QDialog):
    """Просмотр изображений в папке (аналог kidney_GUI.view_image_folder на PyQt6)."""

    def __init__(self, input_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        (
            self.lang_of_interface,
            self.path_to_yolo_weights,
            _default_input_dicom_folder,
            _default_output_folder,
            self.current_color_theme,
            self.img_count,
            self.img_format,
            self.tooltips,
            self.buttons,
            self.list_text,
            self.checkbox,
            self.main_menu,
            self.window_heads,
            self.titles,
        ) = _load_ui_text()

        self.folder = input_path
        self.filenames_only = image_filenames(self.folder, self.img_format)
        self.images_filenames = [os.path.join(self.folder, f) for f in self.filenames_only]
        if not self.images_filenames:
            QMessageBox.critical(self, self.titles[2], self.list_text[9])
            self.close()
            return

        self.filenum = 0
        self.save_confidence = True

        self.setWindowTitle(self.window_heads[2])
        self._init_ui()
        self._update_image()

    def _init_ui(self) -> None:
        main_layout = QHBoxLayout()

        # Левая колонка: список файлов и настройки
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(self.list_text[0]))

        folder_row = QHBoxLayout()
        self.folder_label = QLabel(self.folder)
        self.folder_label.setWordWrap(True)
        self.folder_label.setMaximumWidth(320)
        btn_browse = QPushButton("...")
        btn_browse.setToolTip(self.buttons[13])
        btn_browse.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self.folder_label)
        folder_row.addWidget(btn_browse)
        left_layout.addLayout(folder_row)

        self.list_widget = QListWidget()
        for name in self.filenames_only:
            QListWidgetItem(name, self.list_widget)
        self.list_widget.currentRowChanged.connect(self._on_list_selection_changed)
        left_layout.addWidget(self.list_widget)

        left_layout.addWidget(QLabel(self.list_text[1], alignment=Qt.AlignmentFlag.AlignCenter))

        # выбор модели
        self.radio_model1 = QRadioButton(self.buttons[17])
        self.radio_model2 = QRadioButton(self.buttons[18])
        self.radio_model1.setChecked(True)
        radio_row = QHBoxLayout()
        radio_row.addWidget(self.radio_model1)
        radio_row.addWidget(self.radio_model2)
        left_layout.addLayout(radio_row)

        # чекбокс сохранения confidence
        self.chk_save_conf = QCheckBox(self.checkbox[1])
        self.chk_save_conf.setChecked(True)
        self.chk_save_conf.setEnabled(False)
        self.chk_save_conf.setToolTip(self.tooltips[6])
        self.chk_save_conf.stateChanged.connect(self._on_save_conf_changed)
        left_layout.addWidget(self.chk_save_conf)

        btn_detect = QPushButton(self.buttons[10])
        btn_cancel = QPushButton(self.buttons[5])
        btn_cancel.setStyleSheet("background-color: teal; color: white;")
        btn_detect.clicked.connect(self._on_detect)
        btn_cancel.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_detect)
        btn_row.addWidget(btn_cancel)
        left_layout.addLayout(btn_row)

        # Правая колонка: просмотр изображения
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel(self.list_text[2]))

        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        self.path_label.setMinimumWidth(400)
        right_layout.addWidget(self.path_label)

        self.image_label = ImageCoordinates()
        self.image_label.setMinimumSize(360, 360)
        right_layout.addWidget(self.image_label, stretch=1)

        nav_row = QHBoxLayout()
        self.btn_first = QPushButton("<<")
        self.btn_prev = QPushButton("<")
        self.btn_next = QPushButton(">")
        self.btn_last = QPushButton(">>")
        for b in (self.btn_first, self.btn_prev, self.btn_next, self.btn_last):
            b.setFixedWidth(34)
        self.btn_first.clicked.connect(self._on_first)
        self.btn_prev.clicked.connect(self._on_prev)
        self.btn_next.clicked.connect(self._on_next)
        self.btn_last.clicked.connect(self._on_last)
        self.btn_first.setToolTip(self.buttons[6])
        self.btn_prev.setToolTip(self.buttons[7])
        self.btn_next.setToolTip(self.buttons[8])
        self.btn_last.setToolTip(self.buttons[9])
        nav_row.addWidget(self.btn_first)
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.btn_next)
        nav_row.addWidget(self.btn_last)

        self.counter_label = QLabel("")
        nav_row.addWidget(self.counter_label)
        right_layout.addLayout(nav_row)

        main_layout.addLayout(left_layout, stretch=0)
        main_layout.addLayout(right_layout, stretch=1)

        self.setLayout(main_layout)

        screen_geom = QGuiApplication.primaryScreen().availableGeometry()
        w = min(1200, int(screen_geom.width() * 0.9))
        h = min(800, int(screen_geom.height() * 0.9))
        self.resize(w, h)

    # ==== навигация / действия ====

    def _on_browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self.list_text[0],
            self.folder,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        self.folder = folder
        self.folder_label.setText(self.folder)

        self.filenames_only = image_filenames(self.folder, self.img_format)
        self.images_filenames = [os.path.join(self.folder, f) for f in self.filenames_only]
        self.list_widget.clear()
        for name in self.filenames_only:
            QListWidgetItem(name, self.list_widget)

        self.filenum = 0
        if self.filenames_only:
            self.list_widget.setCurrentRow(0)
            self._update_image()

    def _on_list_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.filenames_only):
            return
        self.filenum = row
        self._update_image()

    def _on_save_conf_changed(self, state: int) -> None:
        self.save_confidence = state == Qt.CheckState.Checked

    def _on_first(self) -> None:
        if not self.filenames_only:
            return
        self.filenum = 0
        self.list_widget.setCurrentRow(self.filenum)
        self._update_image()

    def _on_last(self) -> None:
        if not self.filenames_only:
            return
        self.filenum = len(self.filenames_only) - 1
        self.list_widget.setCurrentRow(self.filenum)
        self._update_image()

    def _on_next(self) -> None:
        if self.filenum < len(self.filenames_only) - 1:
            self.filenum += 1
            self.list_widget.setCurrentRow(self.filenum)
            self._update_image()

    def _on_prev(self) -> None:
        if self.filenum > 0:
            self.filenum -= 1
            self.list_widget.setCurrentRow(self.filenum)
            self._update_image()

    def _on_detect(self) -> None:
        if not self.folder or not os.path.exists(self.folder):
            return
        km.global_messages.clear()
        if self.radio_model1.isChecked():
            progress = QProgressDialog('YOLO26', None, 0, 100, self)
            progress.setModal(True)
            def update(index, total, filename):
                progress.setMaximum(total)
                progress.setValue(index)
                progress.setLabelText(filename)
                QApplication.processEvents()
            try:
                ds.detect_stones(self.folder, True, self.path_to_yolo_weights, progress=update)
            except Exception as exc:
                QMessageBox.critical(self, "YOLO26", str(exc))
                return
            finally:
                progress.close()
            # после детекции открыть просмотр детектированных изображений
            viewer = DetectedImagesViewer(os.path.join(self.folder, "detect"), parent=self.parent() or self)
            viewer.exec()
        # второй вариант модели пока оставляем как pass (как в исходнике)
        self.accept()

    def _update_image(self) -> None:
        if not self.filenames_only:
            return
        filename = os.path.join(self.folder, self.filenames_only[self.filenum])
        self.path_label.setText(filename)
        self.image_label.set_image(filename)
        self.counter_label.setText(
            f"File {self.filenum + 1} of {len(self.filenames_only)}"
        )


class DetectedImagesViewer(QDialog):
    """Просмотр детектированных изображений и их разметки (аналог kidney_GUI.view_detected_images на PyQt6)."""

    def __init__(self, input_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stones_worker = None
        self._calc_in_progress = False

        (
            self.lang_of_interface,
            _path_to_yolo_weights,
            _default_input_dicom_folder,
            _default_output_folder,
            self.current_color_theme,
            self.img_count,
            self.img_format,
            self.tooltips,
            self.buttons,
            self.list_text,
            self.checkbox,
            self.main_menu,
            self.window_heads,
            self.titles,
        ) = _load_ui_text()

        self.folder = input_path
        if not os.path.exists(os.path.join(self.folder, "labels")) and self.folder:
            QMessageBox.warning(
                self,
                self.window_heads[1],
                f"{self.folder} {self.list_text[4]}",
            )
            self.close()
            return

        self.filenames_only = image_filenames(self.folder, self.img_format)
        self.img_filenames = [os.path.join(self.folder, f) for f in self.filenames_only]
        if not self.img_filenames:
            QMessageBox.critical(
                self,
                self.titles[2],
                self.list_text[10] + self.list_text[6][16:],
            )
            self.close()
            return

        self.filenum = 0
        self.label_filename = self._calc_label_filename(self.img_filenames[0])

        self.setWindowTitle(self.window_heads[2])
        self._init_ui()
        self._update_view()

    def _init_ui(self) -> None:
        main_layout = QHBoxLayout()

        # Левая колонка: список файлов
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(self.list_text[6]))

        folder_row = QHBoxLayout()
        self.folder_label = QLabel(self.folder)
        self.folder_label.setWordWrap(True)
        self.folder_label.setMaximumWidth(320)
        btn_browse = QPushButton("...")
        btn_browse.setToolTip(self.buttons[13])
        btn_browse.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self.folder_label)
        folder_row.addWidget(btn_browse)
        left_layout.addLayout(folder_row)

        self.list_widget = QListWidget()
        for name in self.filenames_only:
            QListWidgetItem(name, self.list_widget)
        self.list_widget.currentRowChanged.connect(self._on_list_selection_changed)
        left_layout.addWidget(self.list_widget)

        left_layout.addWidget(QLabel(self.list_text[1], alignment=Qt.AlignmentFlag.AlignCenter))

        btn_calc = QPushButton(self.buttons[19])
        btn_cancel = QPushButton(self.buttons[5])
        btn_cancel.setStyleSheet("background-color: teal; color: white;")
        btn_calc.clicked.connect(self._on_calc_stones)
        btn_cancel.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_calc)
        btn_row.addWidget(btn_cancel)
        left_layout.addLayout(btn_row)

        # Правая колонка: изображение + разметка
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel(self.list_text[2]))

        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        right_layout.addWidget(self.path_label)

        self.image_label = ImageCoordinates()
        self.image_label.setMinimumSize(360, 360)
        right_layout.addWidget(self.image_label, stretch=1)

        # блок текста разметки
        self.label_name = QLabel("")
        self.label_text = QTextEdit()
        self.label_text.setReadOnly(True)
        right_layout.addWidget(self.label_name)
        right_layout.addWidget(self.label_text)

        nav_row = QHBoxLayout()
        self.btn_first = QPushButton(self.buttons[6])
        self.btn_prev = QPushButton(self.buttons[7])
        self.btn_next = QPushButton(self.buttons[8])
        self.btn_last = QPushButton(self.buttons[9])
        self.btn_first.clicked.connect(self._on_first)
        self.btn_prev.clicked.connect(self._on_prev)
        self.btn_next.clicked.connect(self._on_next)
        self.btn_last.clicked.connect(self._on_last)
        nav_row.addWidget(self.btn_first)
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.btn_next)
        nav_row.addWidget(self.btn_last)

        self.counter_label = QLabel("")
        nav_row.addWidget(self.counter_label)
        right_layout.addLayout(nav_row)

        # Удаление файлов и доп. действия
        actions_row = QHBoxLayout()
        self.btn_delete = QPushButton(self.buttons[11])
        self.btn_delete.setToolTip(self.tooltips[7])
        self.btn_delete.clicked.connect(self._on_delete_file)
        actions_row.addWidget(self.btn_delete)
        right_layout.addLayout(actions_row)

        main_layout.addLayout(left_layout, stretch=0)
        main_layout.addLayout(right_layout, stretch=1)

        self.setLayout(main_layout)

        screen_geom = QGuiApplication.primaryScreen().availableGeometry()
        w = min(1400, int(screen_geom.width() * 0.9))
        h = min(800, int(screen_geom.height() * 0.9))
        self.resize(w, h)

    # ==== служебные методы ====

    @staticmethod
    def _calc_label_filename(image_filename: str) -> str:
        # повторяем оригинальную формулу:
        # label_filename = str(filename)[:-20] + '/labels' + str(filename)[-20:-4] + '.txt'
        s = str(image_filename)
        return str(Path(s).parent / 'labels' / (Path(s).stem + '.txt'))

    def _read_label_file(self, file_name: str) -> str:
        if Path(file_name).is_file():
            try:
                with open(file_name, "rt", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                return self.list_text[3]
        return self.list_text[3]

    # ==== события ====

    def _on_browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self.list_text[6],
            self.folder,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        self.folder = folder
        self.folder_label.setText(self.folder)

        self.filenames_only = image_filenames(self.folder, self.img_format)
        self.img_filenames = [os.path.join(self.folder, f) for f in self.filenames_only]
        self.list_widget.clear()
        for name in self.filenames_only:
            QListWidgetItem(name, self.list_widget)

        self.filenum = 0
        if self.img_filenames:
            self.list_widget.setCurrentRow(0)
            self._update_view()

    def _on_list_selection_changed(self, row: int) -> None:
        if row < 0 or row >= len(self.img_filenames):
            return
        self.filenum = row
        self._update_view()

    def _on_first(self) -> None:
        if not self.img_filenames:
            return
        self.filenum = 0
        self.list_widget.setCurrentRow(self.filenum)
        self._update_view()

    def _on_last(self) -> None:
        if not self.img_filenames:
            return
        self.filenum = len(self.img_filenames) - 1
        self.list_widget.setCurrentRow(self.filenum)
        self._update_view()

    def _on_next(self) -> None:
        if self.filenum < len(self.img_filenames) - 1:
            self.filenum += 1
            self.list_widget.setCurrentRow(self.filenum)
            self._update_view()

    def _on_prev(self) -> None:
        if self.filenum > 0:
            self.filenum -= 1
            self.list_widget.setCurrentRow(self.filenum)
            self._update_view()

    def _on_delete_file(self) -> None:
        if not self.img_filenames:
            return
        filename = self.img_filenames[self.filenum]
        gs.delete_files(filename)

        # обновить списки
        self.filenames_only = image_filenames(self.folder, self.img_format)
        self.img_filenames = [os.path.join(self.folder, f) for f in self.filenames_only]
        if self.filenum >= len(self.filenames_only):
            self.filenum = max(0, len(self.filenames_only) - 1)

        self.list_widget.clear()
        for name in self.filenames_only:
            QListWidgetItem(name, self.list_widget)

        if self.filenames_only:
            self.list_widget.setCurrentRow(self.filenum)
            self._update_view()
        else:
            self.image_label.clear()
            self.label_name.clear()
            self.label_text.clear()

    def _on_calc_stones(self) -> None:
        if self._calc_in_progress:
            return
        parent_folder = Path(self.folder).parent
        choice = QMessageBox(self)
        choice.setWindowTitle("Параметры камней")
        choice.setText("Выберите режим:")
        recalc_btn = choice.addButton("Пересчитать параметры", QMessageBox.ButtonRole.AcceptRole)
        prev_btn = choice.addButton("Использовать ранее рассчитанные", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = choice.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        choice.exec()

        clicked = choice.clickedButton()
        if clicked == cancel_btn:
            return

        if clicked == prev_btn:
            cached = sp.load_cached_result(parent_folder)
            if cached is not None:
                viewer = StoneParametersViewer(
                    parent_folder=parent_folder,
                    stones_result=cached,
                    parent=self,
                )
                viewer.setWindowState(viewer.windowState() | Qt.WindowState.WindowMaximized)
                viewer.exec()
                return
            QMessageBox.information(
                self,
                "Параметры камней",
                "Нет актуальных результатов для текущей версии сборки и входных данных. "
                "Будет выполнен новый расчёт в отдельном каталоге. Прежние результаты сохранятся.",
            )

        self._start_stone_calc(parent_folder)

    def _start_stone_calc(self, parent_folder: Path) -> None:
        progress_dlg = StonesProgressDialog(self)
        progress_dlg.setWindowFlags(
            progress_dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        progress_dlg.setModal(True)
        progress_dlg.setWindowModality(Qt.WindowModality.ApplicationModal)

        worker = StonesCalcWorker(parent_folder, None)
        self._stones_worker = worker
        self._calc_in_progress = True

        def on_finish(result):
            progress_dlg.close()
            self._calc_in_progress = False
            if self._stones_worker is not None:
                self._stones_worker.deleteLater()
                self._stones_worker = None
            if result is not None:
                viewer = StoneParametersViewer(
                    parent_folder=parent_folder,
                    stones_result=result,
                    parent=self,
                )
                viewer.setWindowState(viewer.windowState() | Qt.WindowState.WindowMaximized)
                viewer.exec()

        def on_error(msg: str):
            progress_dlg.close()
            self._calc_in_progress = False
            if self._stones_worker is not None:
                self._stones_worker.deleteLater()
                self._stones_worker = None
            QMessageBox.critical(self, "Ошибка", f"Ошибка расчёта параметров камней:\n{msg}")

        worker.finished.connect(on_finish)
        worker.error.connect(on_error)

        progress_dlg.show()
        worker.start()

    def reject(self) -> None:
        if self._calc_in_progress:
            QMessageBox.information(self, "Подождите", "Идёт расчёт параметров камней. Дождитесь завершения.")
            return
        super().reject()

    def _update_view(self) -> None:
        if not self.img_filenames:
            return
        filename = self.img_filenames[self.filenum]
        self.label_filename = self._calc_label_filename(filename)
        self.path_label.setText(filename)

        self.image_label.set_image(filename)

        self.label_name.setText(self.label_filename)
        self.label_text.setPlainText(self._read_label_file(self.label_filename))
        self.counter_label.setText(
            f"File {self.filenum + 1} of {len(self.img_filenames)}"
        )


def show_warning(message: str, parent: QWidget | None = None) -> None:
    """Аналог kidney_GUI.warning на PyQt6."""
    QMessageBox.warning(parent, "Warning", message)


class StoneParametersViewer(QDialog):
    """Просмотр параметров камней и связанных изображений на PyQt6 (аналог окна из calcstoneparam.main)."""

    def __init__(self, parent_folder: Path, stones_result: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        (
            self.lang_of_interface,
            _path_to_yolo_weights,
            _default_input_dicom_folder,
            _default_output_folder,
            self.current_color_theme,
            self.img_count,
            self.img_format,
            self.tooltips,
            self.buttons,
            self.list_text,
            self.checkbox,
            self.main_menu,
            self.window_heads,
            self.titles,
        ) = _load_ui_text()

        self.parent_folder = str(parent_folder)
        self.detect_folder = os.path.join(self.parent_folder, "detect")

        self.stones_dir_path: str = stones_result["stones_dir_path"]
        self.RS_params = stones_result["RS_params"]
        self.LS_params = stones_result["LS_params"]
        self.param_numpy = stones_result["param_numpy"]
        self._voxel_pixmaps = {}
        self.list_slices_of_stones_RK = stones_result["list_slices_of_stones_RK"]
        self.list_slices_of_stones_LK = stones_result["list_slices_of_stones_LK"]
        self.pdfFileName = stones_result["pdfFileName"]

        self.filenames_only = image_filenames(self.detect_folder, '.png')

        self.index_of_stone = 0
        self.kidney_key = "right" if len(self.RS_params) > 0 else "left"

        self.setWindowTitle(self.window_heads[4])
        self._init_ui()
        self._update_view()

    def _init_ui(self) -> None:
        main_layout = QHBoxLayout()

        # Левая колонка: табы и список камней
        left_layout = QVBoxLayout()

        self.tabs = QTabWidget()
        self.tab_right = QWidget()
        self.tab_left = QWidget()
        self.tabs.addTab(
            self.tab_right,
            self.list_text[19],
        )
        self.tabs.addTab(
            self.tab_left,
            self.list_text[20],
        )
        self.tabs.setTabEnabled(0, len(self.RS_params) > 0)
        self.tabs.setTabEnabled(1, len(self.LS_params) > 0)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        # Показываем только полосу вкладок, без пустой области страниц
        self.tabs.setStyleSheet("QTabWidget::pane { border: 0; margin: 0; padding: 0; }")
        self.tabs.setFixedHeight(self.tabs.tabBar().sizeHint().height() + 8)

        left_layout.addWidget(self.tabs)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_list_selection_changed)
        tabs_width = max(220, self.tabs.tabBar().sizeHint().width())
        self.list_widget.setFixedWidth(tabs_width)
        left_layout.addWidget(self.list_widget)

        nav_row = QHBoxLayout()
        self.btn_first = QPushButton(self.buttons[6])
        self.btn_prev = QPushButton(self.buttons[7])
        self.btn_next = QPushButton(self.buttons[8])
        self.btn_last = QPushButton(self.buttons[9])
        self.btn_first.clicked.connect(self._on_first)
        self.btn_prev.clicked.connect(self._on_prev)
        self.btn_next.clicked.connect(self._on_next)
        self.btn_last.clicked.connect(self._on_last)
        nav_row.addWidget(self.btn_first)
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.btn_next)
        nav_row.addWidget(self.btn_last)
        left_layout.addLayout(nav_row)

        self.btn_print_pdf = QPushButton("Печать параметров камней")
        self.btn_print_pdf.clicked.connect(self._on_print_pdf)
        self.btn_close = QPushButton(self.buttons[5])
        self.btn_close.setStyleSheet("background-color: teal; color: white;")
        self.btn_close.clicked.connect(self.reject)
        self.btn_print_pdf.setFixedWidth(tabs_width)
        self.btn_close.setFixedWidth(tabs_width)

        left_layout.addWidget(self.btn_print_pdf)
        left_layout.addWidget(self.btn_close)

        # Правая часть: изображения + таблицы
        right_main_layout = QVBoxLayout()

        self.index_label = QLabel(self.list_text[21])
        right_main_layout.addWidget(self.index_label)

        # Верхняя строка: изображение среза + 2D проекция
        top_images_row = QHBoxLayout()
        self.ct_image_label = StaticImageLabel()
        self.ct_image_label.setMinimumSize(280, 220)
        self.ct_image_label.setMaximumSize(520, 320)
        self.ct_image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top_images_row.addWidget(self.ct_image_label)

        self.stone_preview_label = ClickableLabel()
        self.stone_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stone_preview_label.setMinimumSize(220, 150)
        self.stone_preview_label.setMaximumSize(520, 220)
        self.stone_preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.stone_preview_label.set_clicked_callback(self._on_stone_preview_clicked)
        top_images_row.addWidget(self.stone_preview_label)
        right_main_layout.addLayout(top_images_row)

        kidney_stone_row = QHBoxLayout()
        self.btn_kidney_3d = QPushButton("3D модель почки")
        self.btn_kidney_3d.clicked.connect(self._on_kidney_3d)
        self.btn_stone_3d = QPushButton("3D модель камня")
        self.btn_stone_3d.clicked.connect(self._on_stone_3d)
        kidney_stone_row.addWidget(self.btn_kidney_3d)
        kidney_stone_row.addWidget(self.btn_stone_3d)
        right_main_layout.addLayout(kidney_stone_row)

        # Вторая строка: две 3D реконструкции рядом
        three_d_row = QHBoxLayout()
        self.stone_3d_img1 = QLabel()
        self.stone_3d_img1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stone_3d_img1.setMinimumSize(247, 171)
        self.stone_3d_img1.setMaximumSize(494, 247)
        self.stone_3d_img1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.stone_3d_img2 = QLabel()
        self.stone_3d_img2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stone_3d_img2.setMinimumSize(247, 171)
        self.stone_3d_img2.setMaximumSize(494, 247)
        self.stone_3d_img2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        three_d_row.addWidget(self.stone_3d_img1)
        three_d_row.addWidget(self.stone_3d_img2)
        right_main_layout.addLayout(three_d_row)

        # Третья строка: таблица параметров + таблица времени разрушения
        tables_row = QHBoxLayout()

        params_layout = QVBoxLayout()
        self.params_table_title = QLabel("Параметры камня")
        params_layout.addWidget(self.params_table_title)

        self.params_table = QTableWidget()
        self.params_table.setColumnCount(2)
        self.params_table.setHorizontalHeaderLabels(["Параметр", "Значение"])
        self.params_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.params_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.params_table.verticalHeader().setVisible(False)
        self.params_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.params_table.setMinimumHeight(220)
        self.params_table.setMaximumHeight(290)
        params_layout.addWidget(self.params_table)
        tables_row.addLayout(params_layout, 1)

        laser_layout = QVBoxLayout()
        self.laser_table_title = QLabel("Время разрушения камня гольмиевым лазером с длиной волны 2,1 мкм")
        self.laser_table_title.setWordWrap(True)
        laser_layout.addWidget(self.laser_table_title)
        self.laser_axes_title = QLabel("Частота, Гц  |  Энергия, Дж")
        laser_layout.addWidget(self.laser_axes_title)

        self.laser_table = QTableWidget()
        self.laser_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.laser_table.setMinimumHeight(220)
        self.laser_table.setMaximumHeight(290)
        self.laser_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.laser_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        laser_layout.addWidget(self.laser_table)
        tables_row.addLayout(laser_layout, 1)

        right_main_layout.addLayout(tables_row)

        main_layout.addLayout(left_layout, stretch=0)
        main_layout.addLayout(right_main_layout, stretch=4)

        self.setLayout(main_layout)

        screen_geom = QGuiApplication.primaryScreen().availableGeometry()
        w = min(1600, int(screen_geom.width() * 0.9))
        h = min(900, int(screen_geom.height() * 0.9))
        self.resize(w, h)

    # ==== вспомогательные методы ====

    def _current_lists(self):
        if self.kidney_key == "right":
            return self.RS_params, self.list_slices_of_stones_RK, "rk_"
        return self.LS_params, self.list_slices_of_stones_LK, "lk_"

    def _current_stone_param(self):
        params_list, _, _ = self._current_lists()
        if not params_list:
            return None
        return params_list[self.index_of_stone]

    def _slice_image_path(self) -> str | None:
        params_list, slices_list, _ = self._current_lists()
        if not params_list or not self.filenames_only:
            return None
        if self.index_of_stone >= len(slices_list) or self.index_of_stone >= len(self.filenames_only):
            return None
        slice_idx = slices_list[self.index_of_stone]
        base_name = self.filenames_only[self.index_of_stone]
        base = Path(base_name)
        slice_image_name = f"{base.stem.rsplit('_', 1)[0]}_{slice_idx}{base.suffix}"
        return os.path.join(self.detect_folder, slice_image_name)

    def _param_file_name(self) -> str:
        _, _, index_kidney = self._current_lists()
        return os.path.join(self.stones_dir_path, f"stnpar_{index_kidney}{self.index_of_stone}.txt")

    def _stone_image_paths(self):
        _, _, index_kidney = self._current_lists()
        base = os.path.join(self.stones_dir_path, f"stone_{index_kidney}{self.index_of_stone}.png")
        img1 = os.path.join(self.stones_dir_path, f"stone{index_kidney}{self.index_of_stone}_1.png")
        img2 = os.path.join(self.stones_dir_path, f"stone{index_kidney}{self.index_of_stone}_2.png")
        return base, img1, img2

    def _read_params_file(self) -> str:
        file_name = self._param_file_name()
        if Path(file_name).is_file():
            for enc in ("utf-8", "cp1251", "windows-1251"):
                try:
                    with open(file_name, "rt", encoding=enc) as f:
                        return f.read()
                except Exception:
                    continue
            return self.list_text[3]
        return self.list_text[3]

    def _build_params_rows_from_text(self, text: str):
        rows = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.rstrip(':').strip().casefold() == 'параметры камня':
                continue
            if ":" in line:
                left, right = line.split(":", 1)
                rows.append((left.strip(), right.strip()))
            elif "=" in line:
                left, right = line.split("=", 1)
                rows.append((left.strip(), right.strip()))
            elif " - " in line:
                left, right = line.split(" - ", 1)
                rows.append((left.strip(), right.strip()))
            else:
                rows.append((line, ""))
        return rows

    def _update_params_table(self):
        text = self._read_params_file()
        rows = self._build_params_rows_from_text(text)
        self.params_table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.params_table.setItem(i, 0, QTableWidgetItem(k))
            self.params_table.setItem(i, 1, QTableWidgetItem(v))

    def _update_laser_table(self):
        self.laser_table.setToolTip('Цветовая шкала отражает время. Нечёткая классификация режимов R1–R8 здесь не реализована.')
        stone_param = self._current_stone_param()
        if stone_param is None:
            return
        mass = float(stone_param[10])
        cfg = Config.create_default(stone_mass=mass)
        gamma = float(cfg.gamma)
        round_numbers = int(cfg.round_numbers_count)
        freq_labels = list(np.arange(cfg.frequency_from, cfg.frequency_to + cfg.frequency_step, cfg.frequency_step))
        energy_labels = list(np.arange(cfg.energy_from, cfg.energy_to + cfg.energy_step, cfg.energy_step))

        self.laser_table.clear()
        self.laser_table.setRowCount(len(energy_labels))
        self.laser_table.setColumnCount(len(freq_labels))
        self.laser_table.setHorizontalHeaderLabels([str(round(v, round_numbers)) for v in freq_labels])
        self.laser_table.setVerticalHeaderLabels([str(round(v, round_numbers)) for v in energy_labels])

        values = []
        for energy in energy_labels:
            row_vals = []
            for freq in freq_labels:
                row_vals.append(round(mass / (gamma * energy * freq), round_numbers))
            values.extend(row_vals)
        vmin = min(values) if values else 0
        vmax = max(values) if values else 1
        bounds = np.linspace(vmin, vmax, len(cfg.color_theme) + 1)

        for i, energy in enumerate(energy_labels):
            for j, freq in enumerate(freq_labels):
                t = round(mass / (gamma * energy * freq), round_numbers)
                item = QTableWidgetItem(str(t))
                for idx in range(len(bounds) - 1):
                    if bounds[idx] <= t <= bounds[idx + 1]:
                        bg = cfg.color_theme[idx]
                        fg = cfg.color_text_theme[idx]
                        item.setBackground(QBrush(QColor(*bg)))
                        item.setForeground(QBrush(QColor(*fg)))
                        break
                self.laser_table.setItem(i, j, item)

    def _update_list_widget(self) -> None:
        r_list_stones = [self.list_text[18] + " № " + str(i) for i in range(len(self.RS_params))]
        l_list_stones = [self.list_text[18] + " № " + str(i) for i in range(len(self.LS_params))]
        list_stones = r_list_stones if self.kidney_key == "right" else l_list_stones
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for name in list_stones:
            QListWidgetItem(name, self.list_widget)
        if list_stones:
            self.list_widget.setCurrentRow(self.index_of_stone)
        self.list_widget.blockSignals(False)

    def _update_view(self) -> None:
        self._update_list_widget()

        stone_param = self._current_stone_param()
        if stone_param is None:
            self.stone_preview_label.clear()
            self.stone_3d_img1.clear()
            self.stone_3d_img2.clear()
            return

        # Индекс среза
        slices_list = self.list_slices_of_stones_RK if self.kidney_key == "right" else self.list_slices_of_stones_LK
        if slices_list:
            self.index_label.setText(f"{self.list_text[21]} № {slices_list[self.index_of_stone]}")

        # CT-срез
        slice_path = self._slice_image_path()
        if slice_path and os.path.isfile(slice_path):
            self.ct_image_label.set_image(slice_path)

        # Изображения камня (PNG из папки stones после расчёта)
        stone_base, img1, img2 = self._stone_image_paths()
        if os.path.isfile(stone_base):
            pix0 = QPixmap(stone_base)
            if not pix0.isNull():
                self.stone_preview_label.setPixmap(
                    pix0.scaled(
                        self.stone_preview_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                )
        else:
            self.stone_preview_label.clear()

        self._update_voxel_previews()

        # Таблицы
        self._update_params_table()
        self._update_laser_table()

    def _update_voxel_previews(self):
        # Re-render from the saved voxels: old PNG previews may have distorted proportions.
        _, _, side = self._current_lists()
        path = Path(self.stones_dir_path) / f'st_{side}{self.index_of_stone}.npy'
        try:
            sx, sy, sz = legacy_spacing_xyz(self.param_numpy)
            key = (str(path), path.stat().st_mtime_ns, (sx, sy, sz))
            if key not in self._voxel_pixmaps:
                values = np.load(path, allow_pickle=False)
                density, _ = sp.dens_stone_volume_from_hu(values)
                density = sp.new_dens_stone_from_density(density)
                from matplotlib import cm
                from matplotlib.backends.backend_agg import FigureCanvasAgg
                import matplotlib.pyplot as plt
                pixmaps = []
                for data, cmap in ((values, cm.afmhot), (density, cm.Set1)):
                    fig = v3d.stone_vox3D_visualisation(cube_xyz(data), sz, sx, sy, cmap=cmap)
                    try:
                        canvas = FigureCanvasAgg(fig)
                        canvas.draw()
                        rgba = np.asarray(canvas.buffer_rgba())
                        image = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0],
                                       QImage.Format.Format_RGBA8888).copy()
                        pixmaps.append(QPixmap.fromImage(image))
                    finally:
                        plt.close(fig)
                self._voxel_pixmaps[key] = pixmaps
            for label, pixmap in zip((self.stone_3d_img1, self.stone_3d_img2), self._voxel_pixmaps[key]):
                label.setPixmap(pixmap.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
                label.setToolTip('Полный объём и срез 15% по каждой оси; физические пропорции, мм')
        except (ValueError, OSError, IndexError) as exc:
            for label in (self.stone_3d_img1, self.stone_3d_img2):
                label.clear()
                label.setText('Нет данных для воксельной модели')
                label.setToolTip(str(exc))

    # ==== обработчики ====

    def _on_tab_changed(self, index: int) -> None:
        if index == 0 and self.RS_params:
            self.kidney_key = "right"
        elif index == 1 and self.LS_params:
            self.kidney_key = "left"
        self.index_of_stone = 0
        self._update_view()

    def _on_list_selection_changed(self, row: int) -> None:
        if row < 0:
            return
        self.index_of_stone = row
        self._update_view()

    def _on_first(self) -> None:
        self.index_of_stone = 0
        self._update_view()

    def _on_last(self) -> None:
        params_list, _, _ = self._current_lists()
        if not params_list:
            return
        self.index_of_stone = len(params_list) - 1
        self._update_view()

    def _on_next(self) -> None:
        params_list, _, _ = self._current_lists()
        if not params_list:
            return
        if self.index_of_stone < len(params_list) - 1:
            self.index_of_stone += 1
            self._update_view()

    def _on_prev(self) -> None:
        if self.index_of_stone > 0:
            self.index_of_stone -= 1
            self._update_view()

    def _on_print_pdf(self) -> None:
        if self.pdfFileName:
            pw.read_n_print_pdf(self.pdfFileName)

    def _on_kidney_3d(self) -> None:
        ind_kid = "RK" if self.kidney_key == "right" else "LK"
        try:
            values = np.load(os.path.join(self.stones_dir_path, ind_kid + '.npy'),
                             allow_pickle=False)
            xthin, ythin, step_z = legacy_spacing_xyz(self.param_numpy)
            v3d.show_kidney_volume_window_pyvista(
                cube_xyz(values),
                step_z,
                xthin,
                ythin,
                self.list_text[19] if self.kidney_key == "right" else self.list_text[20],
            )
        except Exception as e:
            QMessageBox.critical(self, "3D визуализация", str(e))

    def _on_stone_3d(self) -> None:
        _, _, index_kidney = self._current_lists()
        try:
            curr_stone = np.load(
                os.path.join(self.stones_dir_path, f"st_{index_kidney}{self.index_of_stone}.npy"),
                allow_pickle=False,
            ).astype(np.int32)
            xthin, ythin, step_z = legacy_spacing_xyz(self.param_numpy)
            v3d.show_stone_volume_window_pyvista(
                cube_xyz(curr_stone), step_z, xthin, ythin,
                f'{self.list_text[18]} № {self.index_of_stone}')
        except Exception as exc:
            QMessageBox.warning(self, '3D модель камня', str(exc))
            return

    def _on_stone_preview_clicked(self) -> None:
        stone_param = self._current_stone_param()
        if stone_param is None:
            return
        realLength = stone_param[22]
        realHeight = stone_param[23]
        frame_size_stone = stone_param[24]
        _, _, index_kidney = self._current_lists()

        curr_stone = np.load(
            os.path.join(self.stones_dir_path, f"st_{index_kidney}{self.index_of_stone}.npy")
        ).astype(np.int16)
        curr_stone[curr_stone < 160] = 0
        xthin, ythin, step_z = legacy_spacing_xyz(self.param_numpy)
        try:
            fig_stone = v3d.stone_3proj_view(
                curr_stone,
                frame_size_stone,
                realLength,
                realHeight,
                SliceThickness=step_z,
                x_thin=xthin,
                y_thin=ythin,
            )
        except ValueError as exc:
            QMessageBox.warning(self, 'Проекции камня', str(exc))
            return
        v3d.stone_slice_visualisation_in_window(fig_stone)
