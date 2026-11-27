"""Dependency diagnostics for the kidney volume viewer."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, sentinel
from types import SimpleNamespace
import tempfile
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from sud_yolo26.visualization import rendering
from sud_yolo26.visualization.desktop import kidney_plotter, stone_plotter, compact_kidney_surface,kidney_context_surface


class KidneyViewerTests(unittest.TestCase):
    def test_context_envelope_is_connected_and_discards_separate_fragments(self):
        cube=np.full((36,24,24),-1000.)
        cube[6:27,5:19,5:19]=20
        cube[14:16,5:19,5:19]=-1000
        cube[9:12,9:12,9:12]=-1000
        cube[32:35,1:4,1:4]=60
        original=cube.copy()
        mesh=kidney_context_surface(rendering._load_pyvista(),cube,(1,1,1))
        regions=mesh.connectivity().cell_data['RegionId']
        self.assertEqual(len(np.unique(regions)),1)
        self.assertLess(mesh.bounds[1],32)
        np.testing.assert_array_equal(cube,original)

    def test_soft_kidney_and_dense_stone_are_separate_actors_and_empty_kidney_is_supported(self):
        cube=np.full((12,12,12),50.)
        cube[5:7,5:7,5:7]=700
        p=kidney_plotter(rendering._load_pyvista(),cube,(1,1,1),off_screen=True)
        try:
            self.assertIn('kidney',p.renderer.actors)
            stones=p.renderer.actors['kidney-stones']
            np.testing.assert_allclose(stones.bounds,(5,7,5,7,5,7))
            self.assertEqual(stones.prop.opacity,1)
        finally:
            p.close()
        p=kidney_plotter(rendering._load_pyvista(),np.full((4,4,4),50.),(1,1,1),off_screen=True)
        try:
            self.assertIn('kidney',p.renderer.actors)
            self.assertNotIn('kidney-stones',p.renderer.actors)
        finally:
            p.close()

    def test_kidney_surface_keeps_physical_bounds_opacity_and_source(self):
        pv = rendering._load_pyvista()
        cube = np.full((4,5,6), 500.)
        cube[0,0,0] = -100
        before = cube.copy()
        plotter = kidney_plotter(pv, cube, (.5,1.25,2), off_screen=True)
        try:
            actor = plotter.renderer.actors['kidney']
            np.testing.assert_allclose(actor.bounds, (0,2,0,6.25,0,12))
            np.testing.assert_allclose(actor.scale, (1,1,1))
            self.assertEqual(actor.prop.opacity, .35)
            self.assertEqual(plotter.renderer.actors['kidney-stones'].prop.opacity,1)
            self.assertTrue(plotter.camera.parallel_projection)
            direction=np.asarray(plotter.camera.position)-plotter.camera.focal_point
            self.assertAlmostEqual(direction[0],0)
            self.assertLess(direction[1],0)
            self.assertAlmostEqual(direction[2],0)
            np.testing.assert_allclose(plotter.camera.up,(0,0,1))
            self.assertEqual(plotter.renderer.cube_axes_actor.GetFlyMode(),3)  # static origin triad
            self.assertGreater(actor.mapper.dataset.n_cells,0)
            self.assertFalse(plotter.renderer.GetUseDepthPeeling())
            widget = plotter.widgets.slider_widgets[0]
            widget.GetRepresentation().SetValue(.3)
            widget.InvokeEvent('InteractionEvent')
            self.assertAlmostEqual(actor.prop.opacity, .3)
            np.testing.assert_array_equal(cube, before)
        finally:
            plotter.close()

    def test_compact_surface_uses_strict_hu_threshold_and_physical_registration(self):
        cube = np.full((6,7,8),50.)
        cube[0,0,0]=160
        cube[2,3,4]=700
        original=cube.copy()
        mesh=compact_kidney_surface(rendering._load_pyvista(),cube,(.5,.8,1.25))
        np.testing.assert_allclose(mesh.bounds,(1,1.5,2.4,3.2,5,6.25),atol=1e-6)
        np.testing.assert_array_equal(cube,original)
        with self.assertRaisesRegex(ValueError,'HU >160'):
            compact_kidney_surface(rendering._load_pyvista(),np.full((3,3,3),160),(1,1,1))

    def test_stone_information_preview_has_no_interaction_controls_or_heading_row(self):
        from PyQt6.QtWidgets import QApplication,QWidget
        from PyQt6.QtGui import QImage
        from sud_yolo26.ui.views import StaticImageLabel,StoneParametersViewer
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'image.png'
            picture=QImage(80,40,QImage.Format.Format_RGB32);picture.fill(0xff9944)
            picture.save(str(path))
            before=path.read_bytes()
            preview=StaticImageLabel();preview.resize(240,240);preview.set_image(path)
            self.assertEqual(preview.pixmap().width(),240)
            self.assertEqual(preview.pixmap().height(),120)
            self.assertEqual(preview.findChildren(QWidget),[])
            self.assertEqual(path.read_bytes(),before)
        rows=StoneParametersViewer._build_params_rows_from_text(None,
            'Параметры камня: \nразмеры камня - 1 см\nмасса камня, грамм - 2\n')
        self.assertEqual(rows,[('размеры камня','1 см'),('масса камня, грамм','2')])

    def test_native_stone_keeps_layers_and_synchronized_cutaway(self):
        cube = np.full((5,5,5), 200.)
        cube[-1,-1,-1] = 1300
        plotter = stone_plotter(rendering._load_pyvista(), cube, (.5,1.25,2), off_screen=True)
        try:
            full, cut = plotter.renderers
            self.assertEqual(full.actors['layer-0'].prop.opacity, .25)
            self.assertEqual(full.actors['layer-1'].prop.opacity, 1)
            self.assertEqual(cut.actors['layer-0'].prop.opacity, .25)
            self.assertNotIn('layer-1', cut.actors)
            np.testing.assert_allclose(cut.actors['layer-0'].bounds, (0,2,0,5,0,8))
            self.assertEqual(full.camera, cut.camera)
            self.assertTrue(full.camera.parallel_projection)
        finally:
            plotter.close()

    def test_ui_routes_both_volumes_to_native_viewers_with_correct_spacing(self):
        from sud_yolo26.ui.views import StoneParametersViewer
        with tempfile.TemporaryDirectory() as temp:
            volume = np.arange(2*3*4).reshape(2,3,4)
            np.save(Path(temp)/'RK.npy', volume)
            np.save(Path(temp)/'st_rk_0.npy', volume)
            params = ['0']*20
            params[4], params[6], params[7], params[8] = '.5','0','4.5','1'
            params[12], params[13], params[17] = '.8','.6','10'
            state = SimpleNamespace(kidney_key='right', stones_dir_path=temp,
                param_numpy=params, list_text=['test']*21, index_of_stone=0,
                _current_lists=lambda: ([],[],'rk_'))
            with patch.object(rendering,'show_kidney_volume_window_pyvista') as kidney, \
                 patch.object(rendering,'show_stone_volume_window_pyvista') as stone, \
                 patch.object(rendering,'PlotlyViewer') as browser:
                StoneParametersViewer._on_kidney_3d(state)
                StoneParametersViewer._on_stone_3d(state)
                for viewer in (kidney, stone):
                    viewer.assert_called_once()
                    np.testing.assert_array_equal(viewer.call_args.args[0], volume[::-1].transpose(2,1,0))
                    np.testing.assert_allclose(viewer.call_args.args[1:4], (.5,.6,.8))
                browser.assert_not_called()

    def test_dependency_error_preserves_cause_and_can_retry(self):
        error = ImportError('VTK DLL failed to load')
        with patch.object(rendering, 'pv', None):
            with patch.object(rendering.importlib, 'import_module', side_effect=error):
                with self.assertRaises(RuntimeError) as result:
                    rendering._load_pyvista()
                self.assertIs(result.exception.__cause__, error)
                self.assertIn(sys.executable, str(result.exception))
                self.assertIn('VTK DLL failed to load', str(result.exception))
                self.assertIsNone(rendering.pv)
            with patch.object(rendering.importlib, 'import_module', return_value=sentinel.pyvista):
                self.assertIs(rendering._load_pyvista(), sentinel.pyvista)

    def test_loaded_dependency_is_reused(self):
        with patch.object(rendering, 'pv', sentinel.pyvista):
            with patch.object(rendering.importlib, 'import_module') as importer:
                self.assertIs(rendering._load_pyvista(), sentinel.pyvista)
                importer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
