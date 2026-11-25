"""Optional embedded VTK view; reuses the same plotters as the original UI."""
from PyQt6.QtWidgets import QWidget,QVBoxLayout


class VolumeView(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.box=QVBoxLayout(self)
        self.box.setContentsMargins(0,0,0,0)
        self.plotter=None
        self.widget=None

    def show_volume(self,values,spacing,kind,title):
        from PyQt6.QtGui import QGuiApplication
        if QGuiApplication.platformName()=='offscreen':
            raise RuntimeError('Встроенный 3D требует обычного запуска Qt с поддержкой OpenGL, без QT_QPA_PLATFORM=offscreen.')
        from sud_yolo26.visualization.rendering import _load_pyvista
        from sud_yolo26.visualization.desktop import kidney_plotter,stone_plotter
        from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
        self.clear()
        factory=kidney_plotter if kind=='kidney' else stone_plotter
        self.plotter=factory(_load_pyvista(),values,spacing,title,off_screen=False)
        # Compact captions and a shorter HU scale fit the embedded viewport.
        # These changes affect this plotter instance, not the original viewer.
        for renderer in self.plotter.renderers:
            for actor in renderer.actors.values():
                if hasattr(actor,'SetMaximumFontSize'):
                    actor.SetMaximumFontSize(16)
                    actor.SetMinimumFontSize(10)
        for bar in self.plotter.scalar_bars.values():
            bar.SetLabelFormat('%.0f')
            bar.SetPosition(.79,.28)
            bar.GetLabelTextProperty().SetFontSize(10)
            bar.GetTitleTextProperty().SetFontSize(11)
        self.widget=QVTKRenderWindowInteractor(self,rw=self.plotter.render_window,
                                             iren=self.plotter.iren.interactor)
        self.box.addWidget(self.widget)
        self.widget.Initialize()
        self.widget.Render()

    def clear(self):
        if self.widget is not None:
            self.box.removeWidget(self.widget)
            self.widget.close()
            self.widget.deleteLater()
            self.widget=None
        if self.plotter is not None:
            self.plotter.close()
            self.plotter=None
