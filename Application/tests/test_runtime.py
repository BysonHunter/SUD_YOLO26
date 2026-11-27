from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

import cv2
import numpy as np
import pydicom

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sud_yolo26.detection.postprocess import Detection, accepted_detections
from sud_yolo26.detection.service import detect_stones
from sud_yolo26.imaging.geometry import ordered_headers, pixels_hu, render_coronal


def header(z, slope=1, intercept=0):
    ds=pydicom.Dataset()
    ds.SeriesInstanceUID='1.2.3';ds.Rows=8;ds.Columns=12
    ds.PixelSpacing=[1.5,.7];ds.ImageOrientationPatient=[1,0,0,0,1,0]
    ds.ImagePositionPatient=[0,0,z];ds.SliceThickness=2;ds.PhotometricInterpretation='MONOCHROME2'
    ds.RescaleSlope=slope;ds.RescaleIntercept=intercept
    ds.file_meta=pydicom.dataset.FileMetaDataset();ds.file_meta.TransferSyntaxUID=pydicom.uid.ExplicitVRLittleEndian
    ds.SamplesPerPixel=1;ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=1
    ds.PixelData=np.full((8,12),100,dtype=np.int16).tobytes()
    return ds


class RuntimeTests(unittest.TestCase):
    def test_series_sort_and_anisotropic_spacing(self):
        values=[(Path('c'),header(-1)),(Path('a'),header(0)),(Path('b'),header(-.5))]
        ordered,info=ordered_headers(values)
        self.assertEqual([p.name for p,_ in ordered],['a','b','c'])
        self.assertEqual(info['spacing_zyx_mm'],[.5,1.5,.7])

    def test_reject_duplicate_and_oblique_series(self):
        for values in [[(Path('a'),header(0)),(Path('b'),header(0))],
                       [(Path('a'),header(0)),(Path('b'),header(-1)),(Path('c'),header(-3))]]:
            with self.assertRaises(ValueError):ordered_headers(values)
        ds=header(0);ds.ImageOrientationPatient=[0,1,0,1,0,0]
        with self.assertRaises(ValueError):ordered_headers([(Path('a'),ds),(Path('b'),header(-1))])

    def test_per_slice_hu(self):
        self.assertTrue(np.all(pixels_hu(header(0,2,-100))==100))
        self.assertTrue(np.all(pixels_hu(header(-1,.5,-10))==40))

    def test_multivalue_window(self):
        image=render_coronal(np.zeros((10,12),dtype=np.float32),[.5,1.5,.7],[40,80],[350,400])
        self.assertEqual(image.dtype,np.uint8)
        self.assertEqual(image.shape,(200,200))

    def test_rules_keep_largest_per_kidney_class_and_stone_threshold(self):
        ds=[Detection(0,.6,.4,.1,.1,.9),Detection(0,.6,.4,.2,.2,.7),
            Detection(2,.8,.4,.2,.2,.99),Detection(1,.6,.4,.02,.02,.59),Detection(5,.6,.4,.02,.02,.6)]
        out=accepted_detections(ds)
        self.assertEqual([d.class_id for d in out],[5,0])
        self.assertEqual(out[1].width,.2)
        self.assertEqual(len(out[0].label().split()),6)

    def test_stone_thresholds_can_differ_by_class(self):
        ds=[Detection(1,.6,.4,.02,.02,.3),Detection(5,.6,.4,.02,.02,.59),
            Detection(5,.6,.4,.02,.02,.6)]
        out=accepted_detections(ds,{1:.3,5:.6})
        self.assertEqual([d.class_id for d in out],[1,5])

    def test_atomic_detection_backup_and_six_column_output(self):
        class Data:
            def cpu(self):return self
            def numpy(self):return np.array([[55,25,70,60,.9,0],[58,35,62,39,.7,1]],dtype=np.float32)
        class Model:
            def predict(self,**kwargs):return [SimpleNamespace(orig_img=np.zeros((100,100,3),np.uint8),boxes=SimpleNamespace(data=Data()))]
        class Broken:
            def predict(self,**kwargs):raise RuntimeError('interrupted')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);cv2.imwrite(str(p/'case_010101_100.png'),np.zeros((100,100,3),np.uint8))
            weights=p/'fake-test-checkpoint.bin';weights.write_bytes(b'test fixture only')
            out=detect_stones(p,yolo_weights=weights,model=Model())
            text=(out/'labels/case_010101_100.txt').read_text()
            self.assertTrue(all(len(line.split())==6 for line in text.splitlines()))
            with self.assertRaises(RuntimeError):detect_stones(p,yolo_weights=weights,model=Broken())
            self.assertEqual((out/'labels/case_010101_100.txt').read_text(),text)
            detect_stones(p,yolo_weights=weights,model=Model())
            self.assertEqual(len(list(p.glob('detect.backup_*'))),1)
            from sud_yolo26.analysis.parameters import _detection_signature
            before=_detection_signature(p)
            label=out/'labels/case_010101_100.txt'
            label.write_text(label.read_text()+'\n')
            self.assertNotEqual(_detection_signature(p),before)

    def test_formula_helpers_and_laser_time(self):
        from sud_yolo26.analysis.parameters import dens_stone_volume_from_hu
        from sud_yolo26.reporting.pdf_report import _calc_laser_table
        density,total=dens_stone_volume_from_hu(np.array([[[0,160,1000]]]))
        self.assertAlmostEqual(density[0,0,1],1.6166)
        self.assertAlmostEqual(total,1.6166+2.024)
        _,_,times,_=_calc_laser_table(1.0)
        self.assertAlmostEqual(times[0,0],round(1/(.4*.6*8),2))


if __name__=='__main__':unittest.main()
