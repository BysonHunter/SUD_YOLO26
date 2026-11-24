"""Calibrated CT image with image-local axes and Matplotlib zoom/pan."""
from pathlib import Path
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QInputDialog
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator, FuncFormatter, MaxNLocator
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from sud_yolo26.imaging.calibration import ImageCalibration, image_calibration


class ImageNavigationToolbar(NavigationToolbar2QT):
    def draw_rubberband(self, event, x0, y0, x1, y1):
        super().draw_rubberband(event, x0, y0, x1, y1)
        view = self.parent()
        points = view.axes.transData.inverted().transform([(x0, y0), (x1, y1)])
        view._update_dimensions(points[:, 0], points[:, 1], selection=True)

    def remove_rubberband(self):
        super().remove_rubberband()
        self.parent()._update_dimensions()


class ImageCoordinates(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(5, 5), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        self.toolbar = ImageNavigationToolbar(self.canvas, self, coordinates=False)
        self.dimensions = QLabel('')
        self.dimensions.setWordWrap(True)
        self.dimensions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.dimensions.setStyleSheet('font-weight: 600;')
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.calibrate_button = QPushButton('Задать масштаб')
        self.calibrate_button.setToolTip('Физический размер одного пикселя исходного PNG, мм/пиксель')
        self.calibrate_button.clicked.connect(self._manual_calibration)
        self.calibrate_button.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.status, stretch=1)
        row.addWidget(self.calibrate_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas, stretch=1)
        layout.addWidget(self.dimensions)
        layout.addWidget(self.toolbar)
        layout.addLayout(row)
        self.pixels = None
        self.calibration = None
        self.path = None
        self.grid_step_mm = None
        self.clear()

    def set_image(self, path):
        self.path = Path(path)
        image = QImage(str(path))
        if image.isNull():
            self.clear()
            self.status.setText('Не удалось прочитать изображение')
            return
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        pointer = image.bits()
        pointer.setsize(image.sizeInBytes())
        self.pixels = np.frombuffer(pointer, np.uint8).reshape(image.height(), image.bytesPerLine())
        self.pixels = self.pixels[:, :image.width()*4].reshape(image.height(), image.width(), 4).copy()
        reason = ''
        try:
            self.calibration = image_calibration(self.path, image.width(), image.height())
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.calibration = None
            reason = str(exc)
        self.status.setToolTip(reason or (self.calibration.source if self.calibration else 'Нет физической геометрии PNG'))
        self.calibrate_button.setEnabled(True)
        self._draw_image()

    def _draw_image(self):
        self.axes.clear()
        if self.pixels is None:
            return
        self.axes.set_axis_on()
        height, width = self.pixels.shape[:2]
        calibrated = self.calibration is not None
        sx, sy = ((self.calibration.x_mm, self.calibration.y_mm) if calibrated else (1, 1))
        # Image edges define local zero. Top PNG row stays at the top (large Y).
        self.axes.imshow(self.pixels, origin='upper', extent=(0, width*sx, 0, height*sy),
                         interpolation='nearest', aspect='equal')
        self.axes.set(xlim=(0, width*sx), ylim=(0, height*sy),
                      xlabel='X, мм' if calibrated else 'X, пиксели',
                      ylabel='Y, мм' if calibrated else 'Y, пиксели')
        self.axes.spines[['top', 'right']].set_visible(False)
        self.axes.set_facecolor('black')
        for axis in (self.axes.xaxis, self.axes.yaxis):
            axis.set_major_locator(MultipleLocator(5) if calibrated else MaxNLocator(integer=True))
            axis.set_minor_locator(MultipleLocator(1) if calibrated else MultipleLocator(10))
            axis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:g}'))
        self.axes.tick_params(which='major', length=6, width=1, labelsize=7, direction='out')
        self.axes.tick_params(which='minor', length=3, width=.6, direction='out')
        self.axes.tick_params(axis='x', labelrotation=90)
        self.status.setText('Шкала: 1 мм; подписи: 5 мм. Увеличение — лупа, сброс — домик.' if calibrated
                            else 'Масштаб в мм неизвестен — показаны пиксели.')
        self.toolbar.update()  # discard zoom history belonging to the previous image
        # Axes.clear() resets callbacks; reconnect for each newly displayed image.
        self.axes.callbacks.connect('xlim_changed', self._update_grid)
        self.axes.callbacks.connect('ylim_changed', self._update_grid)
        self._update_grid()
        self.canvas.draw_idle()

    def _update_grid(self, _axes=None):
        self.axes.grid(False, which='both')
        self.grid_step_mm = None
        if self.calibration is not None and self.pixels is not None:
            height, width = self.pixels.shape[:2]
            full_width = width*self.calibration.x_mm
            full_height = height*self.calibration.y_mm
            zoomed = (abs(np.diff(self.axes.get_xlim())[0]) < full_width*(1-1e-6)
                      or abs(np.diff(self.axes.get_ylim())[0]) < full_height*(1-1e-6))
            self.grid_step_mm = 1 if zoomed else 5
            self.axes.set_axisbelow(False)
            self.axes.grid(True, which='major', color='white', alpha=.3, linewidth=.6)
            if zoomed:
                self.axes.grid(True, which='minor', color='white', alpha=.18, linewidth=.4)
            self.status.setText(f'Сетка: {self.grid_step_mm} мм; подписи: 5 мм. '
                                'Увеличение — лупа, сброс — домик.')
        self._update_dimensions()
        self.canvas.draw_idle()

    def _fragment_dimensions(self, xlim=None, ylim=None):
        if self.pixels is None:
            return None
        height, width = self.pixels.shape[:2]
        sx, sy = ((self.calibration.x_mm, self.calibration.y_mm)
                  if self.calibration else (1, 1))
        x0, x1 = sorted(self.axes.get_xlim() if xlim is None else xlim)
        y0, y1 = sorted(self.axes.get_ylim() if ylim is None else ylim)
        # Exclude the blank area when a pan/zoom extends beyond the PNG edges.
        return (max(0., min(x1, width*sx)-max(x0, 0.)),
                max(0., min(y1, height*sy)-max(y0, 0.)))

    def _update_dimensions(self, xlim=None, ylim=None, selection=False):
        size = self._fragment_dimensions(xlim, ylim)
        if size is None:
            self.dimensions.clear()
            return
        width, height = size
        prefix = 'Выделение' if selection else 'Видимый фрагмент'
        if width <= 0 or height <= 0:
            self.dimensions.setText(f'{prefix}: вне изображения')
            return
        unit = 'мм' if self.calibration else 'пикс.'
        w = f'{width:.2f}'.replace('.', ',')
        h = f'{height:.2f}'.replace('.', ',')
        self.dimensions.setText(f'{prefix}: ширина {w} {unit} × высота {h} {unit}')

    def _manual_calibration(self):
        if self.pixels is None:
            return
        sx, ok = QInputDialog.getDouble(self, 'Калибровка изображения',
            'Размер пикселя PNG по X, мм/пиксель:', self.calibration.x_mm if self.calibration else 1,
            .000001, 1000, 6)
        if not ok:
            return
        sy, ok = QInputDialog.getDouble(self, 'Калибровка изображения',
            'Размер пикселя PNG по Y, мм/пиксель:', self.calibration.y_mm if self.calibration else sx,
            .000001, 1000, 6)
        if ok:
            self.calibration = ImageCalibration(sx, sy, 'Ручная калибровка текущего изображения')
            self.status.setToolTip(self.calibration.source)
            self._draw_image()

    def clear(self):
        self.pixels = None
        self.calibration = None
        self.grid_step_mm = None
        self.axes.clear()
        self.axes.set_axis_off()
        self.status.clear()
        self.dimensions.clear()
        self.calibrate_button.setEnabled(False)
        self.canvas.draw_idle()
