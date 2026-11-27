import numpy as np
import plotly
import os, sys
import importlib
import plotly.graph_objs as go
import matplotlib
from matplotlib import cm
import matplotlib.colors
import matplotlib.pyplot as plt
from matplotlib import ticker
# Tk backend removed: runtime uses Qt6/Agg only
# import PySimpleGUI as sg
from mpl_toolkits.axes_grid1 import make_axes_locatable
import cv2
from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QApplication
from PyQt6.QtCore import QTimer
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
# Load VTK only when the kidney viewer is opened, not at application startup.
pv = None


def _load_pyvista():
    global pv
    if pv is None:
        try:
            pv = importlib.import_module('pyvista')
        except Exception as exc:
            raise RuntimeError(
                'Не удалось загрузить PyVista/VTK для 3D-модели почки.\n'
                'Установите зависимости в окружение приложения:\n'
                f'"{sys.executable}" -m pip install pyvista==0.49.2 vtk==9.7.2\n'
                f'Техническая причина: {exc}'
            ) from exc
    return pv

try:
    import pyqtgraph.opengl as gl
    _gl_import_error = None
except Exception:
    gl = None
    _gl_import_error = None

_kidney_windows = []


def PlotlyViewer(fig, win_name, exec=True):
    """
    Упрощённый viewer для Plotly-графиков без зависимости от PyQt5/WebEngine.
    Просто сохраняет html и открывает его в браузере по умолчанию.
    """
    from sud_yolo26.application.paths import APP_ROOT
    import tempfile
    html_dir = APP_ROOT / "data" / "visualization"
    html_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(suffix=".html", dir=html_dir, delete=False)
    file_path = handle.name
    handle.close()
    plotly.offline.plot(fig, filename=file_path, auto_open=True)
    return file_path


def show_kidney_volume_window(values, SliceThickness, PixelSpacingX, PixelSpacingY, win_name="Почка 3D"):
    """
    Быстрая визуализация объёма почки в отдельном Qt-окне через pyqtgraph.
    Использует все воксели внутри полезного объёма (без шага 2/3).
    """
    global gl, _gl_import_error
    if gl is None:
        # Ленивый повторный импорт: полезно, если библиотеку установили после старта приложения.
        try:
            import pyqtgraph.opengl as _gl
            gl = _gl
            _gl_import_error = None
        except Exception as e:
            _gl_import_error = e
            raise RuntimeError(
                "Не удалось загрузить pyqtgraph.opengl.\n"
                "Убедитесь, что установлены зависимости:\n"
                "  pip install pyqtgraph PyOpenGL PyOpenGL_accelerate\n"
                f"Техническая причина: {e}"
            )

    vol = values.copy().astype(np.float32)
    vol[vol <= 35] = 0

    nz = np.argwhere(vol > 0)
    if nz.size == 0:
        raise RuntimeError("Нет данных для 3D визуализации почки")

    mins = nz.min(axis=0)
    maxs = nz.max(axis=0) + 1
    vol = vol[mins[0]:maxs[0], mins[1]:maxs[1], mins[2]:maxs[2]]
    nz_local = np.argwhere(vol > 0)
    vals_local = vol[vol > 0]
    vmax = float(vals_local.max()) if vals_local.size else 1.0

    window = QMainWindow()
    window.setWindowTitle(f"{win_name} (оси в мм)")
    view = gl.GLViewWidget()
    window.setCentralWidget(view)
    view.setBackgroundColor((12, 12, 16))

    # Для больших массивов GLVolumeItem часто падает из-за ограничений GPU/3D-texture.
    # Используем устойчивый точечный рендер всех вокселей почки.
    pts = np.empty((nz_local.shape[0], 3), dtype=np.float32)
    pts[:, 0] = nz_local[:, 0] * float(PixelSpacingX)
    pts[:, 1] = nz_local[:, 1] * float(PixelSpacingY)
    pts[:, 2] = nz_local[:, 2] * float(SliceThickness)

    norm = np.clip(vals_local / vmax, 0.0, 1.0).astype(np.float32)
    colors = np.empty((norm.shape[0], 4), dtype=np.float32)
    colors[:, 0] = 0.1
    colors[:, 1] = 0.5 + 0.5 * norm
    colors[:, 2] = 0.9 * norm
    colors[:, 3] = 0.25 + 0.65 * norm

    scatter = gl.GLScatterPlotItem(pos=pts, color=colors, size=1.5, pxMode=True)
    view.addItem(scatter)

    axis = gl.GLAxisItem()
    axis.setSize(
        vol.shape[0] * float(PixelSpacingX),
        vol.shape[1] * float(PixelSpacingY),
        vol.shape[2] * float(SliceThickness),
    )
    view.addItem(axis)

    view.setCameraPosition(distance=max(vol.shape) * 2.5)
    window.resize(1200, 900)
    window.show()
    _kidney_windows.append(window)  # удерживаем ссылку
    return window


def show_kidney_volume_window_pyvista(values, SliceThickness, PixelSpacingX, PixelSpacingY, win_name="Почка 3D"):
    """Native viewer; legacy argument SliceThickness now receives centre spacing Z."""
    from .desktop import kidney_plotter
    plotter = kidney_plotter(_load_pyvista(), values,
                             (PixelSpacingX, PixelSpacingY, SliceThickness), win_name)
    plotter.show()
    return plotter


def show_stone_volume_window_pyvista(values, step_z, spacing_x, spacing_y, win_name='Камень 3D'):
    from .desktop import stone_plotter
    plotter = stone_plotter(_load_pyvista(), values, (spacing_x, spacing_y, step_z), win_name)
    plotter.show()
    return plotter


def show_kidney_volume_window_mpl(values, SliceThickness, PixelSpacingX, PixelSpacingY, win_name="Почка 3D"):
    """
    Надёжный fallback без OpenGL: отдельное окно matplotlib с 3D scatter.
    Используется, когда GL-контекст не работает на машине.
    """
    fig = plt.figure(figsize=(11, 8))
    try:
        fig.canvas.manager.set_window_title(f"{win_name} (оси в мм)")
    except Exception:
        pass
    ax = fig.add_subplot(111, projection='3d')
    ax.set_xlabel("X, мм")
    ax.set_ylabel("Y, мм")
    ax.set_zlabel("Z, мм")
    ax.set_title(f"{win_name} (подготовка...)")

    # Рисуем figure в отдельном Qt-окне (без зависимости от интерактивного pyplot backend)
    window = QMainWindow()
    window.setWindowTitle(f"{win_name} (оси в мм)")
    container = QWidget()
    layout = QVBoxLayout(container)
    canvas = FigureCanvas(fig)
    layout.addWidget(canvas)
    window.setCentralWidget(container)
    window.resize(1200, 900)
    window.show()
    QApplication.processEvents()

    def _build_plot():
        vol = values.copy().astype(np.float32)
        vol[vol <= 35] = 0

        nz = np.argwhere(vol > 0)
        if nz.size == 0:
            ax.set_title("Нет данных для 3D визуализации почки")
            canvas.draw()
            return

        # Обрезка пустых областей
        mins = nz.min(axis=0)
        maxs = nz.max(axis=0) + 1
        vol = vol[mins[0]:maxs[0], mins[1]:maxs[1], mins[2]:maxs[2]]
        nz = np.argwhere(vol > 0)
        vals = vol[vol > 0]

        # Ускорение: адаптивное ограничение количества точек.
        max_points = 45000
        if nz.shape[0] > max_points:
            step = int(np.ceil(nz.shape[0] / max_points))
            nz = nz[::step]
            vals = vals[::step]

        x = nz[:, 0].astype(np.float32) * float(PixelSpacingX)
        y = nz[:, 1].astype(np.float32) * float(PixelSpacingY)
        z = nz[:, 2].astype(np.float32) * float(SliceThickness)

        vmax = float(vals.max()) if vals.size else 1.0
        norm = np.clip(vals / vmax, 0.0, 1.0)
        colors = plt.cm.plasma(norm)

        ax.clear()
        ax.set_xlabel("X, мм")
        ax.set_ylabel("Y, мм")
        ax.set_zlabel("Z, мм")
        ax.set_title(win_name)
        ax.scatter(x, y, z, c=colors, s=0.5, depthshade=False)
        plt.tight_layout()
        canvas.draw()

    # Строим после показа окна, чтобы UI не "залипал".
    QTimer.singleShot(0, _build_plot)
    _kidney_windows.append(window)  # удерживаем ссылку на окно
    return window


def stone_slice_visualisation_in_window(stone_figure):
    # Отдельное окно matplotlib без использования PySimpleGUI.
    stone_figure.show()


def normalize(arr):
    if np.any(arr != 0):
        arr_min = np.min(arr)
    else:
        arr_min = 0
    try:
        return (arr - arr_min) / (np.max(arr) - arr_min)
    except Exception:
        return arr


def stone_slice_figure(current_stone_array, med_slice, realLength_stone, realHeight_stone, kid_stone_index, dpi=100):
    """
    2D срез камня (как при расчёте параметров). Возвращает matplotlib.figure.Figure.
    """
    ticks = [0, 160, 300, 500, 800, 900, 1000, 1100, 1200, 1250, 1300]
    ticks_labels = ['0', '160', '300', '500', '800', '900', '1000', '1100', '1200', '1250', '1300HU']
    fig = plt.figure(figsize=(8, 8), dpi=dpi)
    ax = fig.add_subplot()
    ax.set_xlabel('Width, mm', fontsize=10)
    ax.set_ylabel('Height, mm', fontsize=10)
    ax.set_title(f'Stone {kid_stone_index} at {med_slice} slice')
    ax.xaxis.set_major_locator(ticker.MultipleLocator(2))
    ax.xaxis.set_minor_locator(ticker.MultipleLocator(1))
    ax.yaxis.set_major_locator(ticker.MultipleLocator(2))
    ax.yaxis.set_minor_locator(ticker.MultipleLocator(1))
    img = ax.imshow(
        current_stone_array,
        origin='upper',
        cmap=plt.cm.inferno,
        extent=(0, realLength_stone, 0, realHeight_stone),
        vmin=0,
        vmax=1300,
    )
    cbar_ax = make_axes_locatable(ax).append_axes(position='right', size='5%', pad=0.1)
    cbar = fig.colorbar(mappable=img, cax=cbar_ax)
    cbar.set_ticks(ticks)
    cbar.set_ticklabels(ticks_labels)
    fig.tight_layout()
    return fig


def explode(data):
    shape_arr = np.array(data.shape)
    size = shape_arr[:3] * 2 - 1
    exploded = np.zeros(np.concatenate([size, shape_arr[3:]]), dtype=data.dtype)
    exploded[::2, ::2, ::2] = data
    return exploded


def expand_coordinates(indices):
    x, y, z = indices
    x[1::2, :, :] += 1
    y[:, 1::2, :] += 1
    z[:, :, 1::2] += 1
    return x, y, z


def stone_vox3D_visualisation(cube, SliceThickness=1, x_thin=1, y_thin=1, st_angle=0, angle=-90, cmap=cm.afmhot, dpi=100):
    from .stone_geometry import voxel_figure
    return voxel_figure(cube, (x_thin, y_thin, SliceThickness), st_angle, angle, cmap, dpi)


def stone_3proj_view(ds_array, frame_size_of_stone, realLength_stone, realHeight_stone, SliceThickness=1,
                     x_thin=1, y_thin=1, st_angle=0, angle=-90, cmap=cm.afmhot):
    from .stone_geometry import projection_figure
    return projection_figure(ds_array, (x_thin, y_thin, SliceThickness), st_angle, angle, cmap)


def kidney_3D_visualisation(values, SliceThickness, PixelSpacingX, PixelSpacingY):
    values = values.copy()
    values[values <= 35] = 0

    # Ускорение без потери деталей: обрезаем только пустые области вокруг почки,
    # при этом используем все точки внутри полезного объёма (без шагового прореживания).
    nz = np.argwhere(values > 0)
    if nz.size > 0:
        mins = nz.min(axis=0)
        maxs = nz.max(axis=0) + 1
        values = values[mins[0]:maxs[0], mins[1]:maxs[1], mins[2]:maxs[2]]

    X, Y, Z = np.mgrid[0:values.shape[0], 0:values.shape[1], 0:values.shape[2]]
    flat_values = values.flatten()
    non_zero = flat_values[flat_values > 0]
    if non_zero.size == 0:
        raise RuntimeError("Нет данных для 3D визуализации почки")

    # Подбираем диапазон объёмного рендера по полезным данным,
    # чтобы сохранить читаемость и не терять детали.
    isomin = float(np.percentile(non_zero, 5))
    isomax = float(np.percentile(non_zero, 99))
    if isomin >= isomax:
        isomin = float(non_zero.min())
        isomax = float(non_zero.max())
    """ colorsmaps
    ['aggrnyl', 'agsunset', 'algae', 'amp', 'armyrose', 'balance',
     'blackbody', 'bluered', 'blues', 'blugrn', 'bluyl', 'brbg',
     'brwnyl', 'bugn', 'bupu', 'burg', 'burgyl', 'cividis', 'curl',
     'darkmint', 'deep', 'delta', 'dense', 'earth', 'edge', 'electric',
     'emrld', 'fall', 'geyser', 'gnbu', 'gray', 'greens', 'greys',
     'haline', 'hot', 'hsv', 'ice', 'icefire', 'inferno', 'jet',
     'magenta', 'magma', 'matter', 'mint', 'mrybm', 'mygbm', 'oranges',
     'orrd', 'oryel', 'oxy', 'peach', 'phase', 'picnic', 'pinkyl',
     'piyg', 'plasma', 'plotly3', 'portland', 'prgn', 'pubu', 'pubugn',
     'puor', 'purd', 'purp', 'purples', 'purpor', 'rainbow', 'rdbu',
     'rdgy', 'rdpu', 'rdylbu', 'rdylgn', 'redor', 'reds', 'solar',
     'spectral', 'speed', 'sunset', 'sunsetdark', 'teal', 'tealgrn',
     'tealrose', 'tempo', 'temps', 'thermal', 'tropic', 'turbid',
     'turbo', 'twilight', 'viridis', 'ylgn', 'ylgnbu', 'ylorbr',
     'ylorrd']
    """
    data = go.Volume(
        # Координаты в миллиметрах: индекс вокселя * физический размер шага.
        x=(X * float(PixelSpacingX)).flatten(),
        y=(Y * float(PixelSpacingY)).flatten(),
        z=(Z * float(SliceThickness)).flatten(),
        value=flat_values,
        isomin=isomin,
        isomax=isomax,
        colorscale='plasma',
        opacity=0.08,
        opacityscale='linear',
        surface_count=20,
        colorbar_nticks=5,
    )

    fig = go.Figure(data)
    fig.update_layout(
        scene=dict(
            xaxis_title="X, мм",
            yaxis_title="Y, мм",
            zaxis_title="Z, мм",
            # Стартовый ракурс близок к «взгляду на корональную проекцию».
            camera=dict(
                eye=dict(x=0.0, y=2.2, z=0.15),
                center=dict(x=0.0, y=0.0, z=0.0),
                up=dict(x=0.0, y=0.0, z=1.0),
            ),
            aspectmode="data",
        )
    )
    return fig


def stone_3D_visualisation(values, SliceThickness, PixelSpacingX, PixelSpacingY):
    from .stone_geometry import interactive_figure
    return interactive_figure(values, (PixelSpacingX, PixelSpacingY, SliceThickness))
