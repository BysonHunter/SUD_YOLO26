import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sud_yolo26.ui_workspace.study import Study,find_studies,image_paths
from sud_yolo26.ui_workspace.window import WorkspaceWindow
from sud_yolo26.ui_workspace.jobs import detect,Cancelled


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def study(self,root):
        for folder in ('geometry','images/coronal/detect/labels','images/axial'):
            (root/folder).mkdir(parents=True,exist_ok=True)
        (root/'geometry/geometry.json').write_text('{}')
        (root/'selection.json').write_text(json.dumps(dict(identity=dict(patient_id='DEMO',study_code='20200101',series_description='Native'))))
        for name in ('DEMO_20200101_cor_10.png','DEMO_20200101_cor_2.png'):
            image=QImage(32,48,QImage.Format.Format_RGB32);image.fill(0xff444444)
            image.save(str(root/'images/coronal'/name))
        return root/'images/coronal'

    def test_open_detect_or_series_has_same_context_and_natural_sort(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);coronal=self.study(root)
            self.assertEqual(Study.open(root),Study.open(coronal/'detect'))
            self.assertEqual(Study.open(root).patient,'DEMO')
            self.assertEqual([p.stem.rsplit('_',1)[1] for p in image_paths(coronal)],['2','10'])
            self.assertEqual(len(find_studies(root)),1)

    def test_selection_syncs_source_layer_and_analysis_grid(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);coronal=self.study(root)
            planes=root/'geometry/planes';planes.mkdir()
            for path in image_paths(coronal):
                metadata=dict(width=32,height=48,column_step_lps_mm=[.5,0,0],
                              row_step_lps_mm=[0,0,-.8],
                              image_path=f'../../images/coronal/{path.name}',
                              image_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                (planes/f'{path.stem}.json').write_text(json.dumps(metadata))
            window=WorkspaceWindow()
            try:
                window.open_study(root)
                self.assertIsNone(window.job)
                self.assertEqual(window.stage,1)
                self.assertTrue(window.primary.isEnabled())
                row=[0]*28;row[1]=10;row[2:5]=[1.3,1.6,.9];row[8]=.536;row[10]=.99;row[15]=619
                window.set_result(dict(RS_params=[row],LS_params=[],stones_dir_path=str(root),param_numpy=[],pdfFileName=''))
                self.assertEqual(window.stage,3)
                self.assertEqual(window.slider.value(),1)
                self.assertEqual(window.views.currentIndex(),0)
                self.assertTrue(window.image.grid_button.isEnabled())
                self.assertEqual(window.image.grid_step_mm,5)
                window.image.grid_button.click()
                self.assertIsNone(window.image.grid_step_mm)
                window.slider.setValue(0)
                self.assertFalse(window.image.grid_button.isChecked())
                self.assertIsNone(window.image.grid_step_mm)
                window.image.grid_button.click()
                self.assertEqual(window.image.grid_step_mm,5)
                window.image.axes.set_xlim(1,8)
                self.assertEqual(window.image.grid_step_mm,1)
                window._display_image(window.slider.value())
                self.assertEqual(window.views.currentIndex(),0)
                self.assertEqual(window.image.grid_step_mm,5)
                self.assertEqual(window.table.item(1,1).text(),'536.00')
                self.assertTrue(window.stone_3d.isEnabled())
                window.side.setCurrentIndex(1)
                self.assertEqual(window.stones.count(),0)
                self.assertFalse(window.stone_3d.isEnabled())
            finally:window.close();self.app.processEvents()

    def test_failed_job_restores_controls_and_shows_error(self):
        window=WorkspaceWindow()
        try:
            def fail(report):raise ValueError('test failure')
            window._start('Test',fail,lambda result:None)
            for _ in range(100):
                QTest.qWait(10)
                if window.job is None:break
            self.assertIsNone(window.job)
            self.assertIn('test failure',window.error.toPlainText())
            self.assertTrue(window.open_button.isEnabled())
        finally:
            if window.job is not None:window.job.wait();self.app.processEvents()
            window.close()

    def test_cancelled_detection_keeps_old_outputs_and_does_not_publish_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'detect').mkdir();(root/'detect/old.txt').write_text('old')
            (root/'.detect-existing').mkdir()
            def cancelled(folder,**kwargs):
                (root/'.detect-new').mkdir();raise Cancelled()
            with patch('sud_yolo26.detection.service.detect_stones',side_effect=cancelled):
                with self.assertRaises(Cancelled):detect(root,'weights.pt',lambda *args:None)
            self.assertTrue((root/'.detect-existing').exists())
            self.assertTrue((root/'.detect-new').exists())
            self.assertEqual((root/'detect/old.txt').read_text(),'old')


if __name__=='__main__':unittest.main()
