"""Detailed analysis for the workspace; the original viewer remains independent."""
from pathlib import Path
import shutil

import numpy as np
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QPen
from PyQt6.QtWidgets import (
    QDialog, QWidget, QFrame, QLabel, QPushButton, QComboBox, QListWidget,
    QVBoxLayout, QHBoxLayout, QTabWidget, QStackedWidget, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QAbstractItemView, QStyledItemDelegate, QStyle, QScrollArea, QLayout,
)
from sud_yolo26.lithotripsy.config import Config
from sud_yolo26.reporting.pdf_report import detected_slice_image, _calc_laser_table
from sud_yolo26.ui.views import StaticImageLabel
from .theme import STYLE, workspace_font
from .volume_view import VolumeView
from .laser_legend import MODE_LEGEND, pdf_cell_colour, colour_mode


class LaserCellDelegate(QStyledItemDelegate):
    """Mark selection with a border, preserving the PDF background and text."""
    def paint(self, painter, option, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, index)
        if selected:
            painter.save()
            painter.setPen(QPen(QColor('#172c37'), 3))
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.setPen(QPen(QColor('white'), 1))
            painter.drawRect(option.rect.adjusted(3, 3, -4, -4))
            painter.restore()


def laser_values(mass):
    """Keep the existing model, ranges and rounding used in the PDF report."""
    cfg = Config.create_default(stone_mass=float(mass))
    energy = np.arange(cfg.energy_from, cfg.energy_to + cfg.energy_step, cfg.energy_step)
    frequency = np.arange(cfg.frequency_from, cfg.frequency_to + cfg.frequency_step, cfg.frequency_step)
    values = np.around(float(mass) / (cfg.gamma * energy[:, None] * frequency[None, :]),
                       cfg.round_numbers_count)
    return cfg, energy, frequency, values


def number(value, precision=2):
    try:
        return f'{float(value):.{precision}f}'.replace('.', ',') if np.isfinite(float(value)) else 'Не вычислено'
    except (TypeError, ValueError):
        return 'Не вычислено'


class StoneDetailsDialog(QDialog):
    def __init__(self, coronal, result, side=0, index=0, parent=None):
        super().__init__(parent)
        self.coronal = Path(coronal)
        self.result = result
        self.folder = Path(result['stones_dir_path'])
        self.setObjectName('StoneDetails')
        self.setWindowTitle('SUD YOLO26 — подробный анализ камня')
        self.setFont(workspace_font())
        self.setStyleSheet(STYLE + '''
            QDialog#StoneDetails {background:#f3f6f8;}
            QLabel#Metric {font-size:19pt;font-weight:600;color:#177f85;}
            QLabel#Section {font-size:12pt;font-weight:600;}
            QTabWidget::pane {border:0;}
            QTabBar::tab {padding:12px 20px;background:#e9eff2;color:#607783;
                border-bottom:3px solid transparent;}
            QTabBar::tab:selected {background:white;color:#177f85;border-bottom:3px solid #177f85;}
            QTableWidget::item:selected {background:#177f85;color:white;}
        ''')
        self.resize(1380, 900)
        self.setMinimumSize(1040, 720)
        self._build()
        # Opening from the workspace preserves its current selection, including an empty side.
        self.side.setCurrentIndex(side)
        self._populate(index)

    def _button(self, text, callback, primary=False):
        button = QPushButton(text)
        if primary:
            button.setObjectName('Primary')
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _label(text='', name=None):
        label = QLabel(text)
        label.setWordWrap(True)
        if name:
            label.setObjectName(name)
        return label

    @staticmethod
    def _panel():
        panel = QFrame()
        panel.setObjectName('Panel')
        box = QVBoxLayout(panel)
        box.setContentsMargins(18, 16, 18, 16)
        return panel, box

    @staticmethod
    def _table(columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.setWordWrap(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(self._label('SUD / Подробный анализ', 'Brand'))
        self.context = self._label('', 'Muted')
        titles.addWidget(self.context)
        header.addLayout(titles, 1)
        self.pdf_button = self._button('Сохранить PDF', self.export_pdf)
        self.pdf_button.setEnabled(Path(self.result.get('pdfFileName', '')).is_file())
        header.addWidget(self.pdf_button)
        header.addWidget(self._button('Вернуться к исследованию', self.accept))
        outer.addLayout(header)
        body = QHBoxLayout()
        outer.addLayout(body, 1)
        sidebar, sidebar_box = self._panel()
        sidebar.setFixedWidth(220)
        sidebar_box.addWidget(self._label('Камни исследования', 'Section'))
        self.side = QComboBox()
        self.side.addItems([f'Правая почка · {len(self.result["RS_params"])}',
                            f'Левая почка · {len(self.result["LS_params"])}'])
        sidebar_box.addWidget(self.side)
        self.stones = QListWidget()
        sidebar_box.addWidget(self.stones, 1)
        sidebar_box.addWidget(self._label('Выбор камня обновляет изображения, параметры и расчёт времени.', 'Muted'))
        body.addWidget(sidebar)
        content = QVBoxLayout()
        body.addLayout(content, 1)
        self.heading = self._label('', 'Heading')
        content.addWidget(self.heading)
        cards = QHBoxLayout()
        self.metrics = []
        for title in ('Габариты, мм', 'Объём, мм³', 'Масса, г', 'Среднее HU'):
            panel, box = self._panel()
            box.addWidget(self._label(title, 'Muted'))
            value = self._label('—', 'Metric')
            if title == 'Габариты, мм':
                value.setStyleSheet('font-size:14pt;font-weight:600;color:#177f85;')
            box.addWidget(value)
            cards.addWidget(panel, 2 if title == 'Габариты, мм' else 1)
            self.metrics.append(value)
        content.addLayout(cards)
        self.tabs = QTabWidget()
        content.addWidget(self.tabs, 1)
        self._build_parameters()
        self._build_laser()
        self.status = self._label('', 'Muted')
        outer.addWidget(self.status)
        self.side.currentIndexChanged.connect(lambda: self._populate())
        self.stones.currentRowChanged.connect(self._select)

    def _build_parameters(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split)
        image_panel, images = self._panel()
        controls = QHBoxLayout()
        self.preview = QComboBox()
        self.preview.addItems(['КТ · корональный срез', 'Камень · срез',
                               'Воксельная модель · HU', 'Воксельная модель · плотность'])
        self.preview.currentIndexChanged.connect(self._show_preview)
        controls.addWidget(self.preview, 1)
        images.addLayout(controls)
        self.views = QStackedWidget()
        self.image = StaticImageLabel()
        self.image.setMinimumSize(180, 180)
        self.volume = VolumeView()
        self.views.addWidget(self.image)
        self.views.addWidget(self.volume)
        images.addWidget(self.views, 1)
        self.image_caption = self._label('', 'Muted')
        images.addWidget(self.image_caption)
        actions = QHBoxLayout()
        self.stone_3d = self._button('3D камня', lambda: self.show_3d('stone'))
        self.kidney_3d = self._button('3D почки и камней', lambda: self.show_3d('kidney'))
        actions.addWidget(self.stone_3d)
        actions.addWidget(self.kidney_3d)
        images.addLayout(actions)
        split.addWidget(image_panel)
        panel, box = self._panel()
        box.addWidget(self._label('Измеренные параметры', 'Section'))
        self.parameters = self._table(['Параметр', 'Значение'])
        self.parameters.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.parameters.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        box.addWidget(self.parameters, 1)
        split.addWidget(panel)
        split.setSizes([520, 410])
        self.tabs.addTab(page, 'Параметры и изображения')

    def _build_laser(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.addWidget(self._label('Расчёт времени разрушения · гольмиевый лазер 2,1 мкм', 'Section'))
        layout.addWidget(self._label('Выберите ячейку: столбцы — частота, Гц; строки — энергия импульса, Дж.', 'Muted'))
        row = QHBoxLayout()
        layout.addLayout(row, 1)
        panel, box = self._panel()
        self.laser = self._table([])
        self.laser.setItemDelegate(LaserCellDelegate(self.laser))
        self.laser.verticalHeader().show()
        self.laser.verticalHeader().setDefaultSectionSize(28)
        self.laser.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.laser.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.laser.currentCellChanged.connect(self._select_regime)
        box.addWidget(self.laser, 1)
        box.addWidget(self._label('Цвета совпадают с PDF · наведите указатель на ячейку для подсказки.', 'Muted'))
        box.addWidget(self._button('Легенда режимов', self.show_mode_legend))
        row.addWidget(panel, 1)
        panel, box = self._panel()
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        box.addWidget(self._label('Выбранный режим', 'Section'))
        self.regime = self._label()
        box.addWidget(self.regime)
        box.addSpacing(18)
        box.addWidget(self._label('Расчётное время', 'Muted'))
        self.time = self._label('—', 'Metric')
        box.addWidget(self.time)
        box.addWidget(self._label('единицы исходной модели', 'Muted'))
        box.addSpacing(18)
        self.laser_mass = self._label()
        box.addWidget(self.laser_mass)
        box.addSpacing(12)
        box.addWidget(self._label('Режим по цветовой легенде', 'Muted'))
        self.mode_name = self._label()
        box.addWidget(self.mode_name)
        box.addStretch()
        box.addWidget(self._label('Название расшифровывает цвет; отдельная оценка риска и пригодности режима не выполняется.', 'Muted'))
        self.regime_scroll = QScrollArea()
        self.regime_scroll.setWidgetResizable(True)
        self.regime_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.regime_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.regime_scroll.setFixedWidth(265)
        self.regime_scroll.setWidget(panel)
        row.addWidget(self.regime_scroll)
        self.formula_toggle = self._button('Показать формулу и исходные данные', self._toggle_formula)
        self.formula_toggle.setCheckable(True)
        layout.addWidget(self.formula_toggle)
        self.formula = self._label('', 'Muted')
        self.formula.hide()
        layout.addWidget(self.formula)
        self.tabs.addTab(page, 'Время разрушения')

    def _toggle_formula(self):
        visible = self.formula_toggle.isChecked()
        self.formula.setVisible(visible)
        self.formula_toggle.setText('Скрыть формулу и исходные данные' if visible else 'Показать формулу и исходные данные')

    def show_mode_legend(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Цветовая легенда режимов')
        dialog.resize(760, 470)
        box = QVBoxLayout(dialog)
        box.addWidget(self._label('Название режима по цвету ячейки', 'Section'))
        table = self._table(['Цвет', 'Название режима'])
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 65)
        table.setWordWrap(True)
        table.verticalHeader().setMinimumSectionSize(32)
        table.setRowCount(len(MODE_LEGEND))
        for i, (colour, name) in enumerate(MODE_LEGEND):
            swatch = QTableWidgetItem()
            swatch.setBackground(QColor(*colour))
            table.setItem(i, 0, swatch)
            table.setItem(i, 1, QTableWidgetItem(name))
        table.resizeRowsToContents()
        box.addWidget(table, 1)
        box.addWidget(self._label('В PDF 10 оттенков: дополнительный синий относится к низкой плотности, '
            'сине-фиолетовый — к дроблению осколков. Цвет определяется интервалом расчётного времени, '
            'а не независимой классификацией состояния камня.', 'Muted'))
        box.addWidget(self._button('Закрыть', dialog.accept))
        dialog.exec()

    def current_stone(self):
        rows = self.result['RS_params' if self.side.currentIndex() == 0 else 'LS_params']
        index = self.stones.currentRow()
        return rows[index] if 0 <= index < len(rows) else None

    def _populate(self, index=0):
        rows = self.result['RS_params' if self.side.currentIndex() == 0 else 'LS_params']
        self.stones.blockSignals(True)
        self.stones.clear()
        for i, stone in enumerate(rows):
            self.stones.addItem(f'Камень {i + 1}\nСлой {stone[1]} · {number(stone[10], 3)} г')
        self.stones.setCurrentRow(min(max(0, index), len(rows) - 1))
        self.stones.blockSignals(False)
        self._select()

    def _select(self, *args):
        self.volume.clear()
        stone = self.current_stone()
        self.parameters.setRowCount(0)
        self.stone_3d.setEnabled(stone is not None)
        side = 'Правая' if self.side.currentIndex() == 0 else 'Левая'
        metadata = self.result.get('param_numpy', [])
        patient = str(metadata[3]) if len(metadata) > 3 else '—'
        date = str(metadata[0]) if metadata else '—'
        if len(date) == 8 and date.isdigit():
            date = f'{date[6:8]}.{date[4:6]}.{date[:4]}'
        self.context.setText(f'Пациент {patient} · исследование {date} · {side.lower()} почка')
        self.context.setToolTip(str(self.coronal.resolve()))
        self.kidney_3d.setEnabled((self.folder / ('RK.npy' if self.side.currentIndex() == 0 else 'LK.npy')).is_file())
        if stone is None:
            self.heading.setText(f'{side} почка · камни отсутствуют')
            for metric in self.metrics:
                metric.setText('—')
            self.laser.clearContents()
            self.laser.setRowCount(0)
            self.laser.setColumnCount(0)
            self.regime.clear()
            self.time.setText('—')
            self.laser_mass.clear()
            self.mode_name.clear()
            self.mode_name.setStyleSheet('')
            self.formula.clear()
            self._show_preview()
            self.status.setText('На выбранной стороне нет рассчитанных камней.')
            return
        self.heading.setText(f'Камень {self.stones.currentRow() + 1} · {side.lower()} почка · слой {stone[1]}')
        values = [' × '.join(number(v * 10, 1) for v in stone[2:5]),
                  number(stone[8] * 1000), number(stone[10], 3), number(stone[15], 0)]
        for metric, value in zip(self.metrics, values):
            metric.setText(value)
        rows = [
            ('Длина, мм', number(stone[2] * 10)), ('Высота, мм', number(stone[3] * 10)),
            ('Глубина габарита, мм', number(stone[4] * 10)),
            ('Объём камня, мм³', number(stone[8] * 1000)),
            ('Объём габарита, мм³', number(stone[7] * 1000)),
            ('Масса по вокселям, г', number(stone[10], 3)),
            ('Средняя плотность, г/см³', number(stone[11], 3)),
            ('Масса по ср. плотности, г', number(stone[12], 3)),
            ('Максимум HU', number(stone[13], 0)), ('Минимум HU', number(stone[14], 0)),
            ('Среднее HU', number(stone[15], 0)), ('Количество вокселей', str(stone[9])),
            ('Объём вокселя, мм³', number(stone[5], 4)),
            ('Слои, включительно', f'{stone[20]}–{int(stone[21]) - 1}'),
            ('Точность определения', number(stone[25])),
            ('Достоверность', number(stone[26])), ('Правдоподобие', number(stone[27])),
        ]
        self.parameters.setRowCount(len(rows))
        for i, (key, value) in enumerate(rows):
            self.parameters.setItem(i, 0, QTableWidgetItem(key))
            self.parameters.setItem(i, 1, QTableWidgetItem(value))
        self._show_preview()
        self._update_laser(stone[10])
        self.status.setText('Данные рассчитанного камня · исходный HU-массив · порог HU > 160')

    def _show_preview(self, *args):
        self.views.setCurrentIndex(0)
        stone = self.current_stone()
        self.image.set_image('')
        if stone is None:
            self.image.setText('На этой стороне нет рассчитанных камней')
            self.image_caption.clear()
            return
        side = 'rk' if self.side.currentIndex() == 0 else 'lk'
        index = self.stones.currentRow()
        mode = self.preview.currentIndex()
        if mode == 0:
            path = detected_slice_image(self.coronal / 'detect', stone[1]) or detected_slice_image(self.coronal, stone[1])
        else:
            name = f'stone_{side}_{index}.png' if mode == 1 else f'stone{side}_{index}_{mode - 1}.png'
            path = self.folder / name
        if path and Path(path).is_file():
            self.image.set_image(path)
            self.image_caption.setText(f'Слой {stone[1]}' if mode == 0 else
                ('Срез камня' if mode == 1 else 'Полный объём и срез 15% по каждой оси · физические пропорции'))
        else:
            self.image.setText('Изображение недоступно')
            self.image_caption.setText('Для интерактивной модели используйте кнопку 3D.')

    def _update_laser(self, mass):
        old_row, old_col = self.laser.currentRow(), self.laser.currentColumn()
        self.cfg, self.energies, self.frequencies, self.times = laser_values(mass)
        _, _, _, self.colour_bands = _calc_laser_table(float(mass))
        self.laser.blockSignals(True)
        self.laser.clear()
        self.laser.setRowCount(len(self.energies))
        self.laser.setColumnCount(len(self.frequencies))
        self.laser.setHorizontalHeaderLabels([number(v, 0) for v in self.frequencies])
        self.laser.setVerticalHeaderLabels([number(v, 1) for v in self.energies])
        for i in range(len(self.energies)):
            for j in range(len(self.frequencies)):
                value = self.times[i, j]
                band = pdf_cell_colour(value, self.colour_bands)
                mode = colour_mode(band['background'])
                item = QTableWidgetItem(number(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setBackground(QColor(*band['background']))
                item.setForeground(QColor(*band['text']))
                item.setData(Qt.ItemDataRole.UserRole, mode)
                item.setToolTip(f'{mode}\n\nЭнергия: {number(self.energies[i], 1)} Дж\n'
                    f'Частота: {number(self.frequencies[j], 0)} Гц\nВремя: {number(value)} ед. модели\n'
                    'Название по цветовой легенде; отдельная оценка риска и пригодности не выполняется.')
                self.laser.setItem(i, j, item)
        self.laser.blockSignals(False)
        self.laser.setCurrentCell(max(0, old_row), max(0, old_col))
        self._select_regime()
        self.laser_mass.setText(f'Масса камня: {number(mass, 3)} г')
        self.formula.setText(f't = m / (γ × E × f)   ·   m = {number(mass, 3)} г   ·   γ = {number(self.cfg.gamma, 1)}\n'
            'В исходной модели размерность γ не задана; значения не обозначены как секунды или минуты. '
            'Формула, диапазоны и округление сохранены. Это расчётная оценка, а не выбор режима лечения.')

    def _select_regime(self, *args):
        i, j = self.laser.currentRow(), self.laser.currentColumn()
        if i < 0 or j < 0 or self.current_stone() is None:
            return
        energy, frequency = self.energies[i], self.frequencies[j]
        self.regime.setText(f'Энергия импульса    {number(energy, 1)} Дж\n\n'
                            f'Частота    {number(frequency, 0)} Гц\n\n'
                            f'Мощность    {number(energy * frequency, 1)} Вт')
        self.time.setText(number(self.times[i, j]))
        item = self.laser.item(i, j)
        self.mode_name.setText(item.data(Qt.ItemDataRole.UserRole))
        self.mode_name.setStyleSheet(f'background:{item.background().color().name()};'
            f'color:{item.foreground().color().name()};padding:10px;border-radius:6px;')

    def show_3d(self, kind):
        if kind == 'stone' and self.current_stone() is None:
            return
        from sud_yolo26.visualization.stone_geometry import cube_xyz, legacy_spacing_xyz
        side = 'rk' if self.side.currentIndex() == 0 else 'lk'
        name = f'st_{side}_{self.stones.currentRow()}.npy' if kind == 'stone' else f'{side.upper()}.npy'
        try:
            self.volume.show_volume(cube_xyz(np.load(self.folder / name, allow_pickle=False)),
                legacy_spacing_xyz(self.result['param_numpy']), kind,
                'Камень' if kind == 'stone' else 'Почка и камни')
            self.views.setCurrentIndex(1)
            self.image_caption.setText('Мышь — вращение · колесо — масштаб · список сверху — возврат к изображению')
        except Exception as exc:
            self.volume.clear()
            self.status.setText('Не удалось открыть 3D: ' + str(exc))

    def export_pdf(self):
        source = Path(self.result.get('pdfFileName', ''))
        if not source.is_file():
            self.status.setText('PDF-отчёт недоступен.')
            return
        target, _ = QFileDialog.getSaveFileName(self, 'Сохранить отчёт', source.name, 'PDF (*.pdf)')
        if target:
            try:
                path = Path(target)
                if path.suffix.lower() != '.pdf':
                    path = path.with_suffix('.pdf')
                if path.resolve() != source.resolve():
                    shutil.copy2(source, path)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
                self.status.setText('Отчёт сохранён: ' + str(path))
            except OSError as exc:
                self.status.setText('Не удалось сохранить отчёт: ' + str(exc))

    def done(self, result):
        self.volume.clear()
        super().done(result)
