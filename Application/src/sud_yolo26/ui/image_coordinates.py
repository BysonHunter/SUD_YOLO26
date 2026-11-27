"""Calibrated CT image with image-local axes and Matplotlib zoom/pan."""
from pathlib import Path
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QInputDialog
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import MultipleLocator, FuncFormatter, MaxNLocator
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from sud_yolo26.imaging.calibration import ImageCalibration, image_calibration


VIEW_BACKGROUND = '#16232d'
RULER_COLOR = '#bfd1d8'


class ImageTicks(MultipleLocator):
    """Keep ruler ticks inside the physical bounds of the image."""
    def __init__(self, step, end):
        super().__init__(step)
        self.end = end

    def tick_values(self, vmin, vmax):
        lower, upper = sorted((vmin, vmax))
        if upper < 0 or lower > self.end:
            return np.array([])
        ticks = super().tick_values(max(0, lower), min(self.end, upper))
        return ticks[(ticks >= 0) & (ticks <= self.end)]


class ImageCanvas(FigureCanvasQTAgg):
    def __init__(self, figure, prepare):
        self.prepare = prepare
        super().__init__(figure)

    def draw(self):
        self.prepare()
        super().draw()


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
        self.figure = Figure(figsize=(5, 5), facecolor=VIEW_BACKGROUND)
        self.axes = self.figure.add_subplot(111)
        self.canvas = ImageCanvas(self.figure, self._prepare_canvas)
        self.ruler_baseline = Line2D([], [], transform=self.figure.transFigure,
                                     color='#8ca5ae', linewidth=.8)
        self.figure.add_artist(self.ruler_baseline)
        self.label_steps_mm = (5, 5)
        self.toolbar = ImageNavigationToolbar(self.canvas, self, coordinates=False)
        self.dimensions = QLabel('')
        self.dimensions.setWordWrap(True)
        self.dimensions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.dimensions.setStyleSheet('font-weight: 600;')
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.grid_button = QPushButton('Сетка: вкл.')
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(True)
        self.grid_button.setToolTip('Показать или скрыть миллиметровую сетку на изображении')
        self.grid_button.toggled.connect(self._toggle_grid)
        self.grid_button.setEnabled(False)
        self.calibrate_button = QPushButton('Задать масштаб')
        self.calibrate_button.setToolTip('Физический размер одного пикселя исходного PNG, мм/пиксель')
        self.calibrate_button.clicked.connect(self._manual_calibration)
        self.calibrate_button.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.status, stretch=1)
        row.addWidget(self.grid_button)
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
        self.grid_button.setEnabled(calibrated)
        sx, sy = ((self.calibration.x_mm, self.calibration.y_mm) if calibrated else (1, 1))
        # Image edges define local zero. Top PNG row stays at the top (large Y).
        self.axes.imshow(self.pixels, origin='upper', extent=(0, width*sx, 0, height*sy),
                         interpolation='nearest', aspect='equal')
        self.axes.set(xlim=(0, width*sx), ylim=(0, height*sy),
                      xlabel='X, мм' if calibrated else 'X, пиксели',
                      ylabel='Y, мм' if calibrated else 'Y, пиксели')
        self.axes.spines[['top', 'right']].set_visible(False)
        self.axes.spines['bottom'].set_visible(False)
        self.axes.spines['left'].set_color('#8ca5ae')
        self.axes.set_facecolor(VIEW_BACKGROUND)
        self.axes.xaxis.label.set_color(RULER_COLOR)
        self.axes.yaxis.label.set_color(RULER_COLOR)
        self.axes.xaxis.label.set_fontsize(8)
        self.axes.yaxis.label.set_fontsize(8)
        for index, (axis, end) in enumerate(zip((self.axes.xaxis, self.axes.yaxis), (width*sx, height*sy))):
            axis.set_major_locator(ImageTicks(5, end) if calibrated else MaxNLocator(integer=True))
            axis.set_minor_locator(ImageTicks(1, end) if calibrated else MultipleLocator(10))
            axis.set_major_formatter(FuncFormatter(lambda v, _, n=index: self._format_tick(v, n)))
        self.axes.tick_params(which='major', length=6, width=1, labelsize=7, direction='out')
        self.axes.tick_params(which='minor', length=3, width=.6, direction='out')
        self.axes.tick_params(which='both', colors=RULER_COLOR)
        self.axes.tick_params(axis='x', labelrotation=0)
        self.ruler_baseline.set_visible(True)
        self.status.setText('Шкала: 1 мм; подписи: 5 мм. Увеличение — лупа, сброс — домик.' if calibrated
                            else 'Масштаб в мм неизвестен — показаны пиксели.')
        self.toolbar.update()  # discard zoom history belonging to the previous image
        # Axes.clear() resets callbacks; reconnect for each newly displayed image.
        self.axes.callbacks.connect('xlim_changed', self._update_grid)
        self.axes.callbacks.connect('ylim_changed', self._update_grid)
        self._update_grid()
        self.canvas.draw_idle()

    def _format_tick(self, value, axis):
        if self.calibration is None:
            return f'{value:g}'
        step = self.label_steps_mm[axis]
        return f'{value:g}' if np.isclose(value/step, round(value/step), atol=1e-7, rtol=0) else ''

    def _prepare_canvas(self):
        """Keep the left ruler at the viewport edge, without stretching the CT."""
        if self.pixels is None:
            return
        width, height = self.figure.bbox.width, self.figure.bbox.height
        ratio = self.canvas.device_pixel_ratio
        left, bottom, right, top = np.array([54, 44, 16, 14])*ratio
        if width <= left+right or height <= bottom+top:
            return
        self.axes.set_position([left/width, bottom/height,
                               (width-left-right)/width, (height-bottom-top)/height])
        self.axes.apply_aspect()  # equal physical scale; the image is centred in this rectangle
        bounds = self.axes.get_window_extent()
        self.axes.spines['left'].set_position(('axes', (left-bounds.x0)/bounds.width))
        self.ruler_baseline.set_data([left/width, (width-right)/width], [bounds.y0/height]*2)
        if self.calibration is not None:
            spans = [abs(np.diff(self.axes.get_xlim())[0]), abs(np.diff(self.axes.get_ylim())[0])]
            self.label_steps_mm = tuple(5*max(1, int(np.ceil(gap*span/(5*pixels))))
                for gap, span, pixels in zip((36*ratio, 24*ratio), spans, (bounds.width, bounds.height)))
            self._clip_grid()

    def _clip_grid(self):
        height, width = self.pixels.shape[:2]
        clip = Rectangle((0, 0), width*self.calibration.x_mm, height*self.calibration.y_mm,
                         transform=self.axes.transData)
        for axis in (self.axes.xaxis, self.axes.yaxis):
            for tick in (*axis.get_major_ticks(), *axis.get_minor_ticks()):
                tick.gridline.set_clip_path(clip)

    def _update_grid(self, _axes=None):
        self.axes.grid(False, which='both')
        self.grid_step_mm = None
        if self.calibration is not None and self.pixels is not None:
            height, width = self.pixels.shape[:2]
            full_width = width*self.calibration.x_mm
            full_height = height*self.calibration.y_mm
            zoomed = (abs(np.diff(self.axes.get_xlim())[0]) < full_width*(1-1e-6)
                      or abs(np.diff(self.axes.get_ylim())[0]) < full_height*(1-1e-6))
            if self.grid_button.isChecked():
                self.grid_step_mm = 1 if zoomed else 5
                self.axes.set_axisbelow(False)
                self.axes.grid(True, which='major', color='#b9e5e9', alpha=.3, linewidth=.6)
                if zoomed:
                    self.axes.grid(True, which='minor', color='#b9e5e9', alpha=.18, linewidth=.4)
                grid_status = f'Сетка: {self.grid_step_mm} мм'
            else:
                grid_status = 'Сетка выключена'
            self._clip_grid()
            self.status.setText(f'{grid_status}; основные деления: 5 мм. '
                                'Увеличение — лупа, сброс — домик.')
        self._update_dimensions()
        self.canvas.draw_idle()

    def _toggle_grid(self, visible):
        self.grid_button.setText('Сетка: вкл.' if visible else 'Сетка: выкл.')
        self._update_grid()

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
        self.ruler_baseline.set_visible(False)
        self.status.clear()
        self.dimensions.clear()
        self.calibrate_button.setEnabled(False)
        self.grid_button.setEnabled(False)
        self.canvas.draw_idle()
