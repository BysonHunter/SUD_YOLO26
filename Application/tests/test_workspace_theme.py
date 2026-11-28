"""Appearance persistence and switching must preserve study state and scientific colors."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QImage, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sud_yolo26.ui_workspace.theme import ThemeController, PALETTES
from sud_yolo26.ui_workspace.window import WorkspaceWindow
from sud_yolo26.ui_workspace.details import StoneDetailsDialog
from sud_yolo26.ui_workspace.volume_view import VolumeView


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = QSettings(str(self.root/'appearance.ini'), QSettings.Format.IniFormat)
        self.controller = ThemeController(settings=self.store)
        self.previous = getattr(self.app, '_sud_workspace_theme', None)
        self.app._sud_workspace_theme = self.controller

    def tearDown(self):
        if self.previous is None:
            del self.app._sud_workspace_theme
        else:
            self.app._sud_workspace_theme = self.previous
        self.temp.cleanup()

    def test_theme_persists_and_invalid_saved_value_uses_dark(self):
        self.assertEqual(self.controller.name, 'dark')
        self.controller.toggle()
        self.store.sync()
        restored = ThemeController(settings=QSettings(str(self.root/'appearance.ini'), QSettings.Format.IniFormat))
        self.assertEqual(restored.name, 'light')
        self.store.setValue('workspace/theme', 'invalid')
        self.assertEqual(ThemeController(settings=self.store).name, 'dark')
        with self.assertRaises(ValueError): self.controller.set_theme('invalid')

    def test_switch_preserves_ct_pixels_zoom_and_grid_without_global_palette_changes(self):
        untouched = QWidget()
        original = untouched.palette().color(QPalette.ColorRole.Window).name()
        window = WorkspaceWindow()
        image = QImage(32,48,QImage.Format.Format_RGB32)
        image.fill(0xff777777)
        source = self.root/'test.png'
        image.save(str(source))
        try:
            window.image.set_image(source)
            window.image.axes.set_xlim(3,18)
            window.image.grid_button.setChecked(False)
            pixels = window.image.pixels.copy()
            self.assertEqual(window.palette().color(QPalette.ColorRole.Window).name(),PALETTES['dark']['bg'])
            window.theme_button.click()
            self.app.processEvents()
            self.assertEqual(self.controller.name,'light')
            self.assertEqual(window.palette().color(QPalette.ColorRole.Window).name(),PALETTES['light']['bg'])
            self.assertEqual(tuple(window.image.axes.get_xlim()),(3,18))
            self.assertFalse(window.image.grid_button.isChecked())
            np.testing.assert_array_equal(pixels,window.image.pixels)
            self.assertEqual(untouched.palette().color(QPalette.ColorRole.Window).name(),original)
            self.assertEqual(window.image.toolbar.palette().color(QPalette.ColorRole.WindowText).name(),PALETTES['light']['text'])
        finally:
            window.close(); untouched.close()

    def test_details_and_main_window_switch_together_preserving_laser_selection_and_colors(self):
        row=[0]*28
        row[:5]=[0,232,1.3,1.6,.9]
        row[8:16]=[.536,1624,1.,1.84,1.,1368,161,619]
        row[20:22]=[227,238]
        result=dict(RS_params=[row],LS_params=[],stones_dir_path=str(self.root),
            param_numpy=['20201212','Native','','TEST'],pdfFileName='')
        window=WorkspaceWindow()
        dialog=StoneDetailsDialog(self.root,result,parent=window)
        try:
            dialog.laser.setCurrentCell(3,2)
            colors=[(dialog.laser.item(i,j).background().color().name(),dialog.laser.item(i,j).foreground().color().name())
                for i in range(dialog.laser.rowCount()) for j in range(dialog.laser.columnCount())]
            for expected in ('light','dark'):
                dialog.theme_button.click()
                self.app.processEvents()
                self.assertEqual(self.controller.name,expected)
                for widget in (window,dialog):
                    self.assertEqual(widget.palette().color(QPalette.ColorRole.Window).name(),PALETTES[expected]['bg'])
                self.assertEqual((dialog.laser.currentRow(),dialog.laser.currentColumn()),(3,2))
                actual=[(dialog.laser.item(i,j).background().color().name(),dialog.laser.item(i,j).foreground().color().name())
                    for i in range(dialog.laser.rowCount()) for j in range(dialog.laser.columnCount())]
                self.assertEqual(colors,actual)
        finally:
            dialog.close();window.close()

    def test_vtk_theme_keeps_camera_geometry_and_hu_colors(self):
        import pyvista as pv
        from sud_yolo26.visualization.desktop import stone_plotter,kidney_plotter
        cube=np.full((5,6,7),70.,dtype=np.float32)
        cube[1:4,2:5,2:5]=700
        cube[2,3,3]=1200
        viewer=VolumeView()
        for factory in (stone_plotter,kidney_plotter):
            viewer.plotter=factory(pv,cube,(.8,1.2,1.6),off_screen=True)
            try:
                camera=np.array(list(viewer.plotter.camera_position))
                bounds=viewer.plotter.bounds
                actors=[actor for renderer in viewer.plotter.renderers for actor in renderer.actors.values()
                    if hasattr(actor,'mapper')]
                ranges=[actor.mapper.scalar_range for actor in actors if hasattr(actor.mapper,'scalar_range')]
                for name in ('dark','light'):
                    viewer.set_theme(name)
                    np.testing.assert_allclose(camera,np.array(list(viewer.plotter.camera_position)))
                    self.assertEqual(bounds,viewer.plotter.bounds)
                    self.assertEqual(ranges,[actor.mapper.scalar_range for actor in actors if hasattr(actor.mapper,'scalar_range')])
            finally:
                viewer.clear()

    def test_workspace_settings_uses_same_theme_and_cancel_does_not_change_it(self):
        from sud_yolo26.ui.settings_dialog import SettingsDialog
        window=WorkspaceWindow()
        try:
            dialog=SettingsDialog(*window.config,parent=window,appearance_controller=self.controller)
            dialog.color_combo.setCurrentIndex(dialog.color_combo.findData('light'))
            dialog.reject()
            self.assertEqual(self.controller.name,'dark')
            dialog=SettingsDialog(*window.config,parent=window,appearance_controller=self.controller)
            dialog.color_combo.setCurrentIndex(dialog.color_combo.findData('light'))
            with patch('sud_yolo26.ui.settings_dialog.cf.save_settings') as save:
                dialog._on_save()
                self.assertEqual(save.call_args.args[4],window.config[4])
            self.assertEqual(self.controller.name,'light')
            self.assertEqual(window.palette().color(QPalette.ColorRole.Window).name(),PALETTES['light']['bg'])
        finally:
            window.close()


if __name__ == '__main__': unittest.main()
