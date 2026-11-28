"""Independent workspace shell using the existing, unmodified SUD services."""
from pathlib import Path
import shutil
import numpy as np
from PyQt6.QtCore import Qt,QUrl
from PyQt6.QtGui import QDesktopServices,QFont
from PyQt6.QtWidgets import (QMainWindow,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,
    QHBoxLayout,QListWidget,QListWidgetItem,QComboBox,QStackedWidget,QSplitter,QSlider,
    QTableWidget,QTableWidgetItem,QHeaderView,QProgressBar,QTextEdit,QFileDialog,QButtonGroup)
from sud_yolo26.settings import service as settings
from sud_yolo26.ui.image_coordinates import ImageCoordinates
from sud_yolo26.ui.views import StaticImageLabel
from .study import Study,find_studies,image_paths,layer_number
from .jobs import Job,import_dicom,detect,calculate
from .theme import workspace_font,theme_controller,apply_theme,update_theme_button
from .volume_view import VolumeView


class WorkspaceWindow(QMainWindow):
    STAGES=('Исследование','Подготовка изображений','Детектирование','Анализ камней')

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('Workspace')
        self.setFont(workspace_font())
        self.setWindowTitle('SUD YOLO26 — рабочее пространство')
        self.theme = theme_controller()
        self.resize(1380,900)
        self.setMinimumSize(1000,700)
        self.study=None
        self.result=None
        self.paths=[]
        self.job=None
        self.stage=0
        self.pending_source=None
        self.pending_output=None
        self._build()
        self.theme.changed.connect(self._apply_theme)
        self._apply_theme(self.theme.name)
        self._refresh_model()
        self.set_stage(0)

    def _button(self,text,callback,primary=False):
        button=QPushButton(text)
        if primary: button.setObjectName('Primary')
        button.clicked.connect(callback)
        return button

    def _build(self):
        root=QWidget();root.setObjectName('WorkspaceRoot');outer=QVBoxLayout(root);outer.setContentsMargins(18,14,18,10)
        self.setCentralWidget(root)
        header=QHBoxLayout()
        brand=QLabel('SUD / Рабочее пространство');brand.setObjectName('Brand')
        header.addWidget(brand);header.addStretch()
        self.study_picker=QComboBox();self.study_picker.setMinimumWidth(280)
        self.study_picker.setPlaceholderText('Исследование не выбрано')
        self.study_picker.currentIndexChanged.connect(self._select_study)
        header.addWidget(self.study_picker)
        self.open_button=self._button('Открыть исследование',self.choose_study)
        header.addWidget(self.open_button);outer.addLayout(header)
        body=QHBoxLayout();outer.addLayout(body,1)
        sidebar=QWidget();sidebar.setObjectName('Sidebar');sidebar.setFixedWidth(205)
        left=QVBoxLayout(sidebar);left.setContentsMargins(12,20,12,14)
        self.identity=QLabel('Откройте DICOM\nили готовое исследование');self.identity.setWordWrap(True)
        left.addWidget(self.identity);left.addSpacing(18)
        self.stage_group=QButtonGroup(self);self.stage_group.setExclusive(True)
        for index,title in enumerate(self.STAGES):
            if index==1:title='Изображения'
            button=self._button(f'{index+1}. {title}',lambda checked=False,n=index:self.set_stage(n))
            button.setCheckable(True);self.stage_group.addButton(button,index);left.addWidget(button)
        left.addStretch();self.model_status=QLabel();self.model_status.setWordWrap(True)
        self.model_status.setObjectName('Muted');left.addWidget(self.model_status)
        self.theme_button=self._button('',self.theme.toggle);left.addWidget(self.theme_button)
        self.settings_button=self._button('Настройки',self.show_settings);left.addWidget(self.settings_button)
        self.exit_button=self._button('Выход',self.close);left.addWidget(self.exit_button)
        body.addWidget(sidebar)
        work=QVBoxLayout();body.addLayout(work,1)
        title_row=QHBoxLayout();text_box=QVBoxLayout()
        self.heading=QLabel();self.heading.setObjectName('Heading')
        self.subtitle=QLabel();self.subtitle.setObjectName('Muted');self.subtitle.setWordWrap(True)
        text_box.addWidget(self.heading);text_box.addWidget(self.subtitle);title_row.addLayout(text_box,1)
        self.primary=self._button('',self.primary_action,True);title_row.addWidget(self.primary)
        work.addLayout(title_row)
        self.pages=QStackedWidget();work.addWidget(self.pages,1)
        self.welcome=QFrame();self.welcome.setObjectName('Panel');welcome=QVBoxLayout(self.welcome)
        welcome.setContentsMargins(28,24,28,24)
        welcome.addWidget(QLabel('Начните с исследования пациента'))
        label=QLabel('Откройте DICOM для подготовки изображений или выберите ранее экспортированное исследование. '
                     'ID пациента, дата и серия будут прочитаны из метаданных.');label.setWordWrap(True)
        welcome.addWidget(label)
        welcome.addWidget(self._button('Открыть DICOM',self.choose_dicom))
        welcome.addWidget(self._button('Открыть готовое исследование',self.choose_study))
        self.study_details=QLabel();self.study_details.setWordWrap(True);welcome.addWidget(self.study_details)
        welcome.addStretch();self.pages.addWidget(self.welcome)
        self.workspace=QSplitter(Qt.Orientation.Horizontal);self.pages.addWidget(self.workspace)
        view=QWidget();view_box=QVBoxLayout(view);view_box.setContentsMargins(0,0,0,0)
        toolbar=QHBoxLayout()
        self.projection=QComboBox();self.projection.addItems(['Корональная','Аксиальная'])
        self.projection.currentIndexChanged.connect(self.refresh_images);toolbar.addWidget(self.projection)
        self.show_boxes=self._button('Рамки детектора',self.refresh_images)
        self.show_boxes.setCheckable(True);self.show_boxes.setChecked(True);toolbar.addWidget(self.show_boxes)
        toolbar.addStretch()
        self.back_2d=self._button('Снимок',lambda:self._display_image(self.slider.value()));toolbar.addWidget(self.back_2d)
        self.kidney_3d=self._button('3D почки',lambda:self.show_3d('kidney'));toolbar.addWidget(self.kidney_3d)
        view_box.addLayout(toolbar)
        self.views=QStackedWidget();self.image=ImageCoordinates()
        self.plain=StaticImageLabel();self.plain.setStyleSheet('background:#111b23;')
        self.plain.setMinimumSize(200,200)
        self.volume=VolumeView();self.views.addWidget(self.image);self.views.addWidget(self.plain);self.views.addWidget(self.volume)
        view_box.addWidget(self.views,1)
        self.image_name=QLabel('Нет изображений');self.image_name.setObjectName('Muted')
        self.image_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        view_box.addWidget(self.image_name)
        self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.valueChanged.connect(self._display_image)
        view_box.addWidget(self.slider)
        navigation=QHBoxLayout()
        navigation.addWidget(self._button('← Предыдущий',lambda:self.slider.setValue(self.slider.value()-1)))
        self.slice_label=QLabel();navigation.addWidget(self.slice_label,1,Qt.AlignmentFlag.AlignCenter)
        navigation.addWidget(self._button('Следующий →',lambda:self.slider.setValue(self.slider.value()+1)))
        view_box.addLayout(navigation);self.workspace.addWidget(view)
        self.right=QFrame();self.right.setObjectName('Panel');self.right.setMinimumWidth(280)
        right=QVBoxLayout(self.right);right.setContentsMargins(14,16,14,16)
        self.right_title=QLabel('Параметры');right.addWidget(self.right_title)
        self.info=QLabel();self.info.setWordWrap(True);right.addWidget(self.info)
        self.side=QComboBox();self.side.addItems(['Правая почка','Левая почка'])
        self.side.currentIndexChanged.connect(self._populate_stones);right.addWidget(self.side)
        self.stones=QListWidget();self.stones.currentRowChanged.connect(self._select_stone)
        right.addWidget(self.stones,1)
        self.table=QTableWidget(0,2);self.table.setHorizontalHeaderLabels(['Параметр','Значение'])
        self.table.verticalHeader().hide();self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(230);right.addWidget(self.table)
        self.stone_3d=self._button('3D выбранного камня',lambda:self.show_3d('stone'));right.addWidget(self.stone_3d)
        self.details=self._button('Подробные параметры и расчёт лазера',self.show_details);right.addWidget(self.details)
        self.recalculate=self._button('Пересчитать параметры',lambda:self.calculate(use_cache=False));right.addWidget(self.recalculate)
        self.workspace.addWidget(self.right);self.workspace.setSizes([760,320])
        self.error=QTextEdit();self.error.setReadOnly(True);self.error.setMaximumHeight(150);self.error.hide();work.addWidget(self.error)
        progress_row=QHBoxLayout();self.progress=QProgressBar();self.progress.hide();progress_row.addWidget(self.progress,1)
        self.cancel=self._button('Отмена',self.cancel_job);self.cancel.hide();progress_row.addWidget(self.cancel)
        work.addLayout(progress_row)
        self.status=QLabel('Готово к работе');self.status.setWordWrap(True);outer.addWidget(self.status)

    def _apply_theme(self,name):
        apply_theme(self,name)
        update_theme_button(self.theme_button,name)
        self.image.refresh_toolbar_theme()
        self.volume.set_theme(name)

    def _refresh_model(self):
        self.config=settings.read_settings()
        self.model_status.setText('YOLO26 · '+('веса найдены' if Path(self.config[1]).is_file() else 'установите обученные веса'))

    def set_stage(self,index):
        self.stage=index;self.stage_group.button(index).setChecked(True)
        self.heading.setText(self.STAGES[index]);self.pages.setCurrentIndex(0 if index==0 else 1)
        descriptions=['Исследование выбирается один раз для всех этапов.',
            'Окно из DICOM · исходные изображения и физическая геометрия.',
            'Детектирование на корональных изображениях обученной моделью YOLO26.',
            'Выберите почку и камень. Срез, параметры и 3D связаны между собой.']
        self.subtitle.setText(descriptions[index])
        analysis=index==3
        for widget in (self.side,self.stones,self.table,self.stone_3d,self.details,self.recalculate):
            widget.setVisible(analysis)
        self.kidney_3d.setVisible(analysis);self.right_title.setText('Находки' if analysis else 'Исследование')
        self.show_boxes.setVisible(index>=2)
        self.info.setText('Расчёт использует исходный HU-массив; PNG служат для просмотра.' if analysis else
            ('Пороги камней: обычные 0,3; коралловидные 0,6. Предыдущие детекции сохраняются.' if index==2 else
             'Аксиальная и корональная проекции. Настройки окна и геометрия берутся из DICOM.'))
        self.refresh_images();self._update_actions()

    def _update_actions(self):
        busy=self.job is not None
        have=self.study is not None
        labels=have and any((self.study.detection/'labels').glob('*.txt'))
        text=['Открыть DICOM','Подготовить изображения','Запустить детектирование',
              'Сохранить PDF' if self.result else 'Рассчитать параметры'][self.stage]
        if self.stage==1 and have and self.pending_source is None:text='Перейти к детектированию'
        self.primary.setText(text)
        allowed=[True,self.pending_source is not None or have,have and bool(image_paths(self.study.coronal)),bool(self.result) or labels][self.stage]
        self.primary.setEnabled(not busy and bool(allowed))
        for widget in (self.study_picker,self.open_button,self.settings_button,self.projection,self.side,self.stones):
            widget.setEnabled(not busy)
        self.welcome.setEnabled(not busy)
        for button in self.stage_group.buttons():button.setEnabled(not busy)
        self.recalculate.setEnabled(not busy and bool(labels))
        self.details.setEnabled(not busy and self.result is not None)
        self.kidney_3d.setEnabled(not busy and self.result is not None)
        self.stone_3d.setEnabled(not busy and self.stones.currentRow()>=0)

    def _fail(self,message,details=''):
        self.error.setPlainText(message+('\n\nПодробности:\n'+details if details else ''))
        self.error.show();self.status.setText('Операция не завершена. Причина показана выше.')

    def choose_study(self):
        path=QFileDialog.getExistingDirectory(self,'Открыть исследование',self.config[3])
        if path:
            try:self.open_study(path)
            except Exception as exc:self._fail(str(exc))

    def open_study(self,path):
        self._set_studies(find_studies(path))

    def _set_studies(self,studies):
        self.study_picker.blockSignals(True);self.study_picker.clear()
        for study in studies:
            self.study_picker.addItem(f'{study.patient} · {study.date} · {study.series}',study)
        self.study_picker.blockSignals(False);self.study_picker.setCurrentIndex(0)
        self._select_study(0)

    def _select_study(self,index):
        study=self.study_picker.itemData(index)
        if study is None:return
        self.study=study;self.result=None;self.volume.clear();self.error.hide()
        self.stones.clear();self.table.setRowCount(0)
        self.identity.setText(f'Пациент {study.patient}\n{study.date}\n{study.series}')
        self.study_details.setText(f'Открыто: {study.root}')
        self.projection.setCurrentIndex(0)
        self.set_stage(2 if image_paths(study.detection) else 1)
        if any((study.detection/'labels').glob('*.txt')):
            self._load_result()

    def choose_dicom(self):
        source=QFileDialog.getExistingDirectory(self,'Каталог DICOM или DICOMDIR',self.config[2])
        if not source:return
        output=QFileDialog.getExistingDirectory(self,'Каталог сохранения исследований',self.config[3])
        if not output:return
        self.pending_source=source;self.pending_output=output
        self.study=None;self.result=None;self.volume.clear();self.stones.clear();self.table.setRowCount(0)
        self.study_picker.clear();self.identity.setText('Новая серия DICOM\nОжидает подготовки')
        self.set_stage(1)
        self.info.setText(f'Источник: {source}\n\nСохранение: {output}\n\nНажмите «Подготовить изображения».')

    def primary_action(self):
        if self.stage==0:self.choose_dicom()
        elif self.stage==1:
            if self.pending_source:self.prepare()
            else:self.set_stage(2)
        elif self.stage==2:self.detect()
        elif self.result:self.export_pdf()
        else:self.calculate()

    def _start(self,title,operation,done,cancellable=False):
        if self.job is not None:return
        self.error.hide();self.status.setText(title);self.progress.setRange(0,0);self.progress.show()
        self.job=Job(operation,self)
        self.cancel.setVisible(cancellable);self.cancel.setEnabled(True)
        def progress(index,total,message):
            self.progress.setRange(0,total if total>0 else 0)
            if total>0:self.progress.setValue(index)
            self.status.setText(f'{message} · {index}/{total}' if total>0 else message)
        def completed(value):
            try:done(value)
            except Exception as exc:self._fail(str(exc))
        job=self.job
        self.job.progress.connect(progress);self.job.succeeded.connect(completed)
        self.job.failed.connect(self._fail)
        self.job.cancelled.connect(lambda:self.status.setText('Операция отменена. Завершённые результаты сохранены.'))
        def finished():
            self.job=None;self.progress.hide();self.cancel.hide();self._update_actions();job.deleteLater()
        job.finished.connect(finished);self._update_actions();job.start()

    def cancel_job(self):
        if self.job:self.job.requestInterruption();self.cancel.setEnabled(False);self.status.setText('Ожидание безопасной остановки…')

    def prepare(self):
        if not self.pending_source:return
        source,output,count=self.pending_source,self.pending_output,int(self.config[5])
        def ready(paths):
            self.pending_source=None
            if paths:self._set_studies([Study.open(path) for path in paths])
            self.status.setText(f'Подготовлено серий: {len(paths)}')
        self._start('Чтение DICOMDIR и выбор серий',lambda report:import_dicom(source,output,count,report),ready,True)

    def detect(self):
        if not self.study:return
        study=self.study;weights=self.config[1]
        if not Path(weights).is_file():self._fail('Установите обученные веса YOLO26 через install_model.py или укажите их в настройках.');return
        def ready(path):
            self.result=None;self.volume.clear();self.stones.clear();self.table.setRowCount(0)
            self.set_stage(3);self.status.setText('Детектирование завершено. Рассчитайте параметры камней.')
        self._start('Загрузка модели YOLO26',lambda report:detect(study.coronal,weights,report),ready,True)

    def _load_result(self):
        from sud_yolo26.analysis.parameters import load_cached_result
        study=self.study
        def ready(result):
            if result:self.set_result(result)
            else:self.status.setText('Исследование открыто. Для анализа выполните расчёт параметров.')
        self._start('Проверка ранее рассчитанных результатов',lambda report:load_cached_result(study.coronal),ready)

    def calculate(self,use_cache=True):
        if not self.study:return
        study=self.study
        self._start('Расчёт параметров камней',lambda report:calculate(study.coronal,report,use_cache),self.set_result)

    def set_result(self,result):
        self.result=result
        right,left=len(result['RS_params']),len(result['LS_params'])
        self.side.setItemText(0,f'Правая почка · {right}')
        self.side.setItemText(1,f'Левая почка · {left}')
        self.side.setCurrentIndex(0 if right or not left else 1)
        self.set_stage(3);self._populate_stones()
        self.status.setText(f'Параметры рассчитаны. Камней справа: {right}, слева: {left}.')

    def _populate_stones(self):
        self.volume.clear()
        self._display_image(self.slider.value())
        self.stones.clear();self.table.setRowCount(0)
        if self.result is None:return
        rows=self.result['RS_params' if self.side.currentIndex()==0 else 'LS_params']
        for index,row in enumerate(rows):
            self.stones.addItem(f'Камень {index+1}   ·   слой {row[1]}')
        if rows:self.stones.setCurrentRow(0)
        else:self.info.setText('На этой стороне камни не включены в результат расчёта.')
        self._update_actions()

    def current_stone(self):
        if self.result is None:return None
        index=self.stones.currentRow();side=self.side.currentIndex()
        rows=self.result['RS_params' if side==0 else 'LS_params']
        return rows[index] if 0<=index<len(rows) else None

    def _select_stone(self,index):
        row=self.current_stone()
        if row is None:return
        values=[('Размеры, мм',f'{row[2]*10:.2f} × {row[3]*10:.2f} × {row[4]*10:.2f}'),
                ('Объём, мм³',f'{row[8]*1000:.2f}'),('Масса, г',f'{row[10]:.3f}'),
                ('Средняя HU',f'{row[15]:.0f}'),('Максимальная HU',f'{row[13]:.0f}'),
                ('Минимальная HU',f'{row[14]:.0f}')]
        self.table.setRowCount(len(values))
        for i,(name,value) in enumerate(values):
            self.table.setItem(i,0,QTableWidgetItem(name));self.table.setItem(i,1,QTableWidgetItem(value))
        self.info.setText('Выбранный камень связан со срезом и трёхмерной моделью.')
        self.projection.setCurrentIndex(0);self.refresh_images()
        for i,path in enumerate(self.paths):
            if layer_number(path)==int(row[1]):self.slider.setValue(i);self._display_image(i);break
        self._update_actions()

    def refresh_images(self,*args):
        old_layer=layer_number(self.paths[self.slider.value()]) if self.paths and self.slider.value()<len(self.paths) else None
        self.paths=[]
        if self.study:
            folder=self.study.axial if self.projection.currentIndex()==1 else self.study.coronal
            self.paths=image_paths(folder)
            if self.projection.currentIndex()==0 and self.stage>=2 and self.show_boxes.isChecked():
                # Retain every source layer. Use annotated image only when it exists.
                self.paths=[self.study.detection/p.name if (self.study.detection/p.name).is_file() else p for p in self.paths]
        self.slider.blockSignals(True);self.slider.setRange(0,max(0,len(self.paths)-1));self.slider.setEnabled(bool(self.paths))
        current=next((i for i,p in enumerate(self.paths) if layer_number(p)==old_layer),0)
        self.slider.setValue(current);self.slider.blockSignals(False);self._display_image(current)

    def _display_image(self,index):
        if not self.paths:
            self.image.clear();self.plain.set_image('');self.image_name.setText('Нет изображений для выбранной проекции');self.slice_label.clear();return
        index=max(0,min(index,len(self.paths)-1));path=self.paths[index]
        self.image_name.setText(path.name);self.image_name.setToolTip(str(path))
        self.slice_label.setText(f'Слой {layer_number(path)}   ·   {index+1} из {len(self.paths)}')
        self.image.set_image(path);self.views.setCurrentIndex(0)

    def show_3d(self,kind):
        if not self.result:return
        from sud_yolo26.visualization.stone_geometry import cube_xyz,legacy_spacing_xyz
        side='rk' if self.side.currentIndex()==0 else 'lk'
        if kind=='stone' and self.current_stone() is None:return
        folder=Path(self.result['stones_dir_path'])
        name=(side.upper()+'.npy') if kind=='kidney' else f'st_{side}_{self.stones.currentRow()}.npy'
        self.status.setText('Подготовка 3D-модели…')
        try:
            self.volume.show_volume(cube_xyz(np.load(folder/name,allow_pickle=False)),
                legacy_spacing_xyz(self.result['param_numpy']),kind,'Почка' if kind=='kidney' else 'Камень')
            self.views.setCurrentIndex(2);self.status.setText('3D: мышь — вращение, колесо — масштаб.')
        except Exception as exc:
            self.volume.clear()
            self._fail('Не удалось открыть 3D: '+str(exc))

    def export_pdf(self):
        if not self.result:return
        source=Path(self.result['pdfFileName'])
        target,_=QFileDialog.getSaveFileName(self,'Сохранить отчёт',source.name,'PDF (*.pdf)')
        if target:
            try:
                target=Path(target)
                if target.suffix.lower()!='.pdf':target=target.with_suffix('.pdf')
                if target.resolve()!=source.resolve():shutil.copy2(source,target)
                self.status.setText('PDF сохранён: '+str(target))
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))
            except Exception as exc:self._fail(str(exc))

    def show_details(self):
        if self.result is None:return
        from .details import StoneDetailsDialog
        dialog=StoneDetailsDialog(self.study.coronal,self.result,
            side=self.side.currentIndex(),index=self.stones.currentRow(),parent=self)
        dialog.exec()
        self.side.setCurrentIndex(dialog.side.currentIndex())
        self.stones.setCurrentRow(dialog.stones.currentRow())

    def show_settings(self):
        from sud_yolo26.ui.settings_dialog import show_settings_dialog
        show_settings_dialog(*self.config,parent=self,appearance_controller=self.theme)
        self._refresh_model();self._update_actions()

    def closeEvent(self,event):
        if self.job is not None:
            self.status.setText('Дождитесь завершения операции или нажмите «Отмена», затем закройте окно.')
            event.ignore();return
        self.volume.clear();super().closeEvent(event)
