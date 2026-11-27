import sys
from pathlib import Path
import unittest
import tempfile
import json
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Application/src"))
from sudweb.artifacts import stone_model, kidney_model, laser_table


class ArtifactTests(unittest.TestCase):
    def test_refresh_saved_visuals_keeps_quantities_sources_and_pdf(self):
        from sudweb.refresh_visuals import refresh_visuals
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);output=root/'stones';output.mkdir()
            volume=np.full((6,5,4),500,dtype=np.int16)
            np.save(output/'st_rk_0.npy',volume)
            np.save(output/'RK.npy',volume)
            (output/'assembly.json').write_text(json.dumps(dict(spacing_xyz_mm=[.5,.8,1.25])))
            (output/'preview.png').write_bytes(b'old preview')
            (root/'report.pdf').write_bytes(b'old report')
            result=dict(series=[dict(_kidney_models=dict(right='stones/kidney.json'),
                stones=dict(right=[dict(index=0,mass_g=.986,_model='stones/full.json',
                    _cut_model='stones/cut.json',_previews=dict(hu='stones/preview.png'))],left=[]))])
            (root/'result.json').write_text(json.dumps(result))
            protected=[root/'result.json',root/'report.pdf',output/'st_rk_0.npy',output/'RK.npy',output/'assembly.json']
            before=[p.read_bytes() for p in protected]
            self.assertEqual(refresh_visuals(root),dict(models=3,previews=1))
            for name in ['kidney','full','cut']:
                model=json.loads((output/f'{name}.json').read_text())
                self.assertEqual(model['layout']['meta']['orientation'],'coronal-z-up-v2')
            self.assertTrue((output/'preview.png').read_bytes().startswith(b'\x89PNG'))
            self.assertEqual([p.read_bytes() for p in protected],before)

    def test_coronal_orientation_matches_stone_and_kidney_without_changing_hu(self):
        volume = np.full((12,8,11),-200,dtype=np.int16)
        volume[7:10,2:5,1:4] = 1200
        volume[2:4,2:4,7:9] = 700
        before = volume.copy()
        spacing = np.array([.5,.8,1.25])
        stone = stone_model(np.where(volume>160,volume,0),spacing)
        big = next(t for t in stone['data'] if max(t['intensity'])==1200)
        small = next(t for t in stone['data'] if max(t['intensity'])==700)
        self.assertLess(max(big['x']),min(small['x']))
        self.assertLess(max(big['z']),min(small['z']))
        kidney = kidney_model(volume,spacing)
        dense = next(t for t in kidney['data'] if t['name']=='Участки HU >160')
        points = np.column_stack([dense[a] for a in 'xyz'])
        self.assertLess(points[points[:,0]<2,2].max(), points[points[:,0]>3,2].min())
        self.assertEqual(stone['layout']['scene']['camera'],kidney['layout']['scene']['camera'])
        self.assertEqual(stone['layout']['scene']['camera']['eye'],dict(x=0,y=-2,z=0))
        self.assertEqual(stone['layout']['meta']['orientation'],'coronal-z-up-v2')
        np.testing.assert_array_equal(volume,before)

    def test_stone_physical_axes_and_cut(self):
        volume = np.full((10, 8, 6), 500, dtype=np.int16)
        spacing = np.array([0.7, 1.2, 2.5])
        for cut, expected in [(False, [4.2, 9.6, 25]), (True, [3.5, 7.2, 20])]:
            model = stone_model(volume, spacing, cut)
            trace = model["data"][0]
            for axis, length in zip("xyz", expected):
                self.assertAlmostEqual(min(trace[axis]), 0)
                self.assertAlmostEqual(max(trace[axis]), length)
            self.assertEqual(model["layout"]["scene"]["aspectmode"], "data")
        # The cut view retains full-volume bounds, as in the desktop viewer.
        self.assertAlmostEqual(model["layout"]["scene"]["zaxis"]["range"][1], 25)

    def test_density_layers_do_not_hide_dense_core(self):
        volume = np.full((10, 10, 10), 200, dtype=np.int16)
        volume[3:7, 3:7, 3:7] = 1200
        traces = stone_model(volume, np.ones(3))["data"]
        self.assertGreater(len(traces), 1)
        self.assertLess(traces[0]["opacity"], traces[-1]["opacity"])
        self.assertEqual(traces[-1]["opacity"], 1)
        self.assertEqual(max(traces[-1]["intensity"]), 1200)

    def test_kidney_context_and_dense_locations(self):
        volume = np.full((20, 24, 28), -200, dtype=np.int16)
        volume[2:18, 3:21, 4:24] = 30
        volume[8:12, 10:14, 12:16] = 700
        model = kidney_model(volume, np.array([0.8, 1.1, 2.0]))
        self.assertEqual(len(model["data"]), 2)
        dense = model["data"][1]
        self.assertEqual(dense["opacity"], 1)
        self.assertEqual(dense["name"], "Участки HU >160")
        self.assertAlmostEqual(min(dense["x"]), 12 * 0.8, places=3)
        self.assertAlmostEqual(max(dense["z"]), 12 * 2, places=3)

    def test_laser_values_colors_and_modes_match_pdf(self):
        from sud_yolo26.reporting.pdf_report import _calc_laser_table
        from sud_yolo26.ui_workspace.laser_legend import pdf_cell_colour, colour_mode

        freq, energy, values, bands = _calc_laser_table(0.986)
        table = laser_table(0.986)
        self.assertEqual(table["frequencies"], freq)
        self.assertEqual(table["energies"], energy)
        self.assertEqual(len(table["legend"]), 8)
        for i, row in enumerate(values):
            for j, value in enumerate(row):
                cell = table["cells"][i][j]
                band = pdf_cell_colour(value, bands)
                self.assertEqual(cell["value"], value)
                self.assertEqual(cell["background"], list(band["background"]))
                self.assertEqual(cell["mode"], colour_mode(band["background"]))


if __name__ == "__main__":
    unittest.main()
