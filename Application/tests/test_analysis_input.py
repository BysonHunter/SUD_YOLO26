import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sud_yolo26.analysis.input_data import load_geometry_input,signature_files
from sud_yolo26.visualization.stone_geometry import legacy_spacing_xyz


class AnalysisInputTests(unittest.TestCase):
    def test_new_coronal_layout_uses_image_row_order_and_physical_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            folder=root/'images/coronal'
            (folder/'detect').mkdir(parents=True)
            (root/'geometry').mkdir();(root/'volume').mkdir()
            values=np.arange(60,dtype='float32').reshape(5,3,4)
            np.save(root/'volume/hu_lps.npy',values)
            (root/'geometry/geometry.json').write_text(json.dumps(dict(
                export=dict(coronal_rendering='shared_window_legacy_coronal_resize'),
                canonical_volume=dict(path='volume/hu_lps.npy',shape_zyx=[5,3,4],spacing_xyz_mm=[.8,.9,.5]),
                source_frames=[dict(slice_thickness_mm=1)])))
            (root/'selection.json').write_text(json.dumps(dict(identity=dict(
                patient_id='5544',study_code='20201212',series_number='2'))))
            result=load_geometry_input(folder)
            np.testing.assert_array_equal(result['volume'],values[::-1])
            self.assertFalse(result['volume'].flags.writeable)
            np.testing.assert_allclose(legacy_spacing_xyz(result['params']),(.8,.9,.5))
            self.assertEqual(result['params'][3],'5544')
            self.assertEqual(signature_files(folder),result['files'])
            np.testing.assert_array_equal(np.load(root/'volume/hu_lps.npy'),values)
            with self.assertRaisesRegex(ValueError,'корональным'):
                load_geometry_input(root/'images/axial')
            result['volume']._mmap.close()

    def test_old_layout_stays_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(load_geometry_input(temp))


if __name__=='__main__': unittest.main()
