import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from sud_yolo26.visualization.stone_geometry import (
    voxel_edges, cutaway, voxel_figure, surface_mesh, legacy_spacing_xyz,
    cube_xyz, projection_figure, interactive_figure, checked_spacing, density_layers)


class StoneGeometryTests(unittest.TestCase):
    def test_coronal_rows_map_to_z_up_without_mirroring_x_or_depth(self):
        volume = np.zeros((12,8,11), dtype=np.int16)
        volume[7:10,2:5,1:4] = 1200  # larger, lower-left on the CT image
        volume[2:4,2:4,7:9] = 700   # smaller, upper-right
        original = volume.copy()
        xyz = cube_xyz(volume)
        big, small = np.argwhere(xyz == 1200), np.argwhere(xyz == 700)
        self.assertLess(big[:,0].max(), small[:,0].min())
        self.assertLess(big[:,2].max(), small[:,2].min())
        self.assertEqual(xyz[1,2,2],1200)
        self.assertEqual(xyz[7,2,9],700)
        np.testing.assert_array_equal(xyz.transpose(2,1,0)[::-1], volume)
        np.testing.assert_array_equal(volume,original)
        fig = voxel_figure(xyz, (.5,.8,1.25))
        try:
            for ax in fig.axes[:2]:
                self.assertEqual((ax.elev,ax.azim),(0,-90))
        finally:
            plt.close(fig)
        camera = interactive_figure(xyz,(.5,.8,1.25)).layout.scene.camera
        self.assertEqual((camera.eye.x,camera.eye.y,camera.eye.z),(0,-2,0))

    def test_cutaway_may_remove_all_voxels_of_a_small_branching_stone(self):
        cube = np.zeros((2,2,2))
        cube[1,0,0] = cube[0,1,0] = cube[0,0,1] = 500
        fig = voxel_figure(cube, (.5,.8,1.25))
        try:
            self.assertGreater(len(fig.axes[0].collections),0)
            self.assertEqual(len(fig.axes[1].collections),0)
        finally:
            plt.close(fig)

    def test_layers_retain_enclosed_core_and_match_static_and_interactive_alpha(self):
        cube = np.full((7,7,7), 200.)
        cube[1:-1,1:-1,1:-1] = 700
        cube[2:-2,2:-2,2:-2] = 1300
        original = cube.copy()
        layers = list(density_layers(cube))
        np.testing.assert_array_equal(sum(band for band, _ in layers), cube)
        opacity = [alpha for _, alpha in layers]
        self.assertEqual(opacity[0], .25)
        self.assertEqual(opacity[-1], 1)
        self.assertTrue(np.all(np.diff(opacity) > 0))
        interactive = interactive_figure(cube, (.5,.8,1.25))
        self.assertEqual([trace.opacity for trace in interactive.data], opacity)
        # Fully enclosed dense voxels have their own rendered surface.
        self.assertTrue(np.all(np.asarray(interactive.data[-1].intensity) == 1300))
        self.assertGreater(len(interactive.data[-1].i), 0)
        # Outer shell and inner interface are not rendered twice in the same plane.
        self.assertEqual(len(interactive.data[0].i), 6*7*7*2)
        fig = voxel_figure(cube, (.5,.8,1.25))
        try:
            for ax in fig.axes[:2]:
                actual = np.unique(np.asarray(ax.collections[0].get_facecolor())[:,3])
                np.testing.assert_allclose(actual, opacity)
        finally:
            plt.close(fig)
        np.testing.assert_array_equal(cube, original)

    def test_cutaway_reuses_full_stone_opacity_even_without_core(self):
        cube = np.full((5,5,5), 200.)
        cube[-1,-1,-1] = 1300
        cropped = list(density_layers(cutaway(cube), reference=cube))
        self.assertEqual(len(cropped), 1)
        self.assertEqual(cropped[0][1], .25)

    def test_single_layer_and_last_layer_are_included_in_stone_volume(self):
        from sud_yolo26.analysis.parameters import stone_layer_range
        volume = np.arange(2*10*3).reshape(2,10,3)
        start, stop = stone_layer_range(5,5,10)
        self.assertEqual(volume[:,start:stop,:].shape, (2,1,3))
        np.testing.assert_array_equal(volume[:,start,:], volume[:,5,:])
        self.assertEqual(stone_layer_range(7,9,10), (7,10))
        with self.assertRaises(ValueError):
            stone_layer_range(10,10,10)

    def test_saved_voxel_previews_refresh_in_memory_and_are_cached(self):
        from PyQt6.QtWidgets import QApplication, QLabel
        from sud_yolo26.ui.views import StoneParametersViewer
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'st_rk_0.npy'
            np.save(path, np.full((3,3,3), 500))
            old_png = Path(temp)/'stonerk_0_1.png'
            old_png.write_bytes(b'original saved preview')
            before = path.read_bytes()
            params = ['0']*20
            params[4],params[6],params[7],params[8] = '.5','0','9.5','1'
            params[12],params[13],params[17] = '.8','.6','20'
            state = SimpleNamespace(_voxel_pixmaps={}, param_numpy=params,
                stones_dir_path=temp, index_of_stone=0, _current_lists=lambda: ([],[],'rk_'),
                stone_3d_img1=QLabel(), stone_3d_img2=QLabel())
            StoneParametersViewer._update_voxel_previews(state)
            self.assertFalse(state.stone_3d_img1.pixmap().isNull())
            self.assertFalse(state.stone_3d_img2.pixmap().isNull())
            self.assertEqual(len(state._voxel_pixmaps), 1)
            with patch('sud_yolo26.ui.views.v3d.stone_vox3D_visualisation', side_effect=RuntimeError('cache missed')):
                StoneParametersViewer._update_voxel_previews(state)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(old_png.read_bytes(), b'original saved preview')

    def test_voxel_edges_multiply_submillimetre_spacing(self):
        cube = np.ones((8, 6, 4))
        edges = voxel_edges(cube, (.5, 1.25, 2))
        self.assertEqual([float(a.max()) for a in edges], [4, 7.5, 8])
        self.assertEqual(edges[0].shape, (9, 7, 5))
        self.assertEqual(cutaway(cube).shape, (6, 5, 3))

    def test_constant_positive_voxels_visible_and_cutaway_keeps_same_axes(self):
        cube = np.full((4, 5, 6), 500.)
        original = cube.copy()
        fig = voxel_figure(cube, (.5, 1.25, 2))
        try:
            for ax in fig.axes[:2]:
                np.testing.assert_allclose([ax.get_xlim()[1], ax.get_ylim()[1], ax.get_zlim()[1]], [2,6.25,12])
                ratios = np.array(ax.get_box_aspect())/np.array([2,6.25,12])
                np.testing.assert_allclose(ratios, np.full(3, ratios[0]))
                self.assertGreater(len(ax.collections), 0)
                for collection in ax.collections:
                    self.assertTrue(np.all(np.asarray(collection.get_facecolor())[:,3] == 1))
            np.testing.assert_array_equal(cube, original)
        finally:
            plt.close(fig)

    def test_surface_boundary_exact_volume_and_no_internal_faces(self):
        spacing = (.5, .8, 1.25)
        points, triangles, hu = surface_mesh(np.full((2,1,1), 700), spacing)
        self.assertEqual(len(triangles), 20)  # two adjacent cells: ten exposed quads
        np.testing.assert_allclose(points.max(axis=0), [1,.8,1.25])
        a, b, c = (points[triangles[:,i]] for i in range(3))
        signed_volume = np.einsum('ij,ij->i', a, np.cross(b,c)).sum()/6
        self.assertAlmostEqual(signed_volume, 2*np.prod(spacing))
        self.assertTrue(np.all(hu == 700))

    def test_spacing_uses_plane_positions_not_slice_thickness_and_corrects_xy(self):
        params = ['0']*20
        params[4], params[6], params[7], params[8] = '.4','10','12','5'
        params[12], params[13], params[17] = '.8','.6','6'
        np.testing.assert_allclose(legacy_spacing_xyz(params), [.6,.8,.4])
        params[6] = 'missing'
        np.testing.assert_allclose(legacy_spacing_xyz(params), [.6,.8,.4])
        params[4] = '0'
        with self.assertRaises(ValueError):
            legacy_spacing_xyz(params)

    def test_projection_planes_keep_their_own_extent_and_peak_index(self):
        volume = np.zeros((6,4,8))
        volume[1,2,3] = 500
        volume[4,1,6] = 900
        xyz = cube_xyz(volume)
        self.assertEqual(xyz[6,1,1], 900)
        fig = projection_figure(volume, (.5,.8,1.25))
        try:
            image_axes = [ax for ax in fig.axes if ax.images]
            expected = [(0,4,0,7.5), (0,3.2,0,7.5), (0,4,0,3.2)]
            for ax, extent in zip(image_axes, expected):
                np.testing.assert_allclose(ax.images[0].get_extent(), extent)
                self.assertEqual(ax.images[0].origin,'upper')
            np.testing.assert_array_equal(image_axes[0].images[0].get_array(), volume[:,1,:])
            np.testing.assert_array_equal(image_axes[1].images[0].get_array(), volume[:,:,6])
            np.testing.assert_array_equal(image_axes[2].images[0].get_array(), volume[4,:,:])
        finally:
            plt.close(fig)

    def test_interactive_single_voxel_keeps_physical_cell_boundaries(self):
        fig = interactive_figure(np.full((1,1,1), 500), (.5,.8,1.25))
        trace = fig.data[0]
        self.assertEqual(trace.type, 'mesh3d')
        self.assertEqual(len(trace.i), 12)
        self.assertEqual(fig.layout.scene.aspectmode, 'data')
        np.testing.assert_allclose([max(trace.x),max(trace.y),max(trace.z)], [.5,.8,1.25])
        self.assertEqual(trace.opacity, 1)

    def test_invalid_spacing_and_empty_stone_rejected(self):
        for spacing in [(0,1,1), (-1,1,1), (np.nan,1,1)]:
            with self.assertRaises(ValueError):
                checked_spacing(spacing)
        with self.assertRaises(ValueError):
            surface_mesh(np.zeros((2,2,2)), (1,1,1))


if __name__ == '__main__':
    unittest.main()
