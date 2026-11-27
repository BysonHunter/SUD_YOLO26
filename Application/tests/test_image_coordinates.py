import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import csv
import hashlib
import json
import sys
import tempfile
import unittest

import numpy as np
from PyQt6.QtGui import QImage, QColor
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from sud_yolo26.imaging.calibration import ImageCalibration, from_sidecar, image_calibration
from sud_yolo26.ui.image_coordinates import ImageCoordinates


class CoordinatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_resized_physical_steps_and_invalid_geometry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'plane.json'
            data = dict(width=100, height=80, column_step_lps_mm=[.6, 0, 0],
                        row_step_lps_mm=[0, 0, -1.25])
            path.write_text(json.dumps(data))
            calibration = from_sidecar(path, 100, 80)
            self.assertEqual((calibration.x_mm, calibration.y_mm), (.6, 1.25))
            with self.assertRaises(ValueError):
                from_sidecar(path, 200, 80)
            data['row_step_lps_mm'] = [1, 0, 1]
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                from_sidecar(path, 100, 80)

    def make_case(self, temp):
        root = Path(temp)
        images = root/'images'/'axial'
        planes = root/'geometry'/'planes'
        images.mkdir(parents=True)
        planes.mkdir(parents=True)
        image = images/'123_20261120_axe_1.png'
        png = QImage(20, 10, QImage.Format.Format_RGB32)
        png.fill(QColor('blue'))
        png.setPixelColor(0, 0, QColor('red'))
        png.save(str(image))
        metadata = dict(width=20, height=10, column_step_lps_mm=[.5, 0, 0],
                        row_step_lps_mm=[0, -.8, 0],
                        image_path=f'../../images/axial/{image.name}',
                        image_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
        (planes/f'{image.stem}.json').write_text(json.dumps(metadata))
        return image

    def test_detected_image_inherits_original_geometry_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            original = self.make_case(temp)
            detected = original.parent/'detect'/original.name
            detected.parent.mkdir()
            detected.write_bytes(original.read_bytes())
            actual = image_calibration(detected, 20, 10)
            self.assertEqual((actual.x_mm, actual.y_mm), (.5, .8))
            original.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                image_calibration(detected, 20, 10)

    def test_origin_orientation_ticks_and_zoom(self):
        with tempfile.TemporaryDirectory() as temp:
            image = self.make_case(temp)
            view = ImageCoordinates()
            view.set_image(image)
            view.canvas.draw()
            self.assertTrue(view.axes.axison)
            self.assertEqual(view.grid_step_mm, 5)
            self.assertTrue(all(t.gridline.get_visible() for t in view.axes.xaxis.get_major_ticks()))
            self.assertFalse(any(t.gridline.get_visible() for t in view.axes.xaxis.get_minor_ticks()))
            self.assertEqual(tuple(view.axes.images[0].get_extent()), (0, 10, 0, 8))
            self.assertEqual(view.axes.images[0].origin, 'upper')
            self.assertEqual(view.pixels[0, 0, :3].tolist(), [255, 0, 0])
            major = view.axes.xaxis.get_majorticklocs()
            self.assertEqual([float(v) for v in major if 0 <= v <= 10], [0, 5, 10])
            minors = view.axes.xaxis.get_minorticklocs()
            self.assertTrue(all(v in minors for v in [1, 2, 3, 4, 6, 7, 8, 9]))
            view.toolbar.push_current()
            view.axes.set_xlim(1, 6)
            self.assertEqual(view.grid_step_mm, 1)
            view.resize(900, 700)
            view.canvas.draw()
            self.assertEqual(tuple(view.axes.get_xlim()), (1, 6))
            self.assertTrue(all(t.gridline.get_visible() for t in view.axes.xaxis.get_minor_ticks()))
            view.toolbar.push_current()
            view.toolbar.home()
            self.assertEqual(view.grid_step_mm, 5)
            # A second image must reconnect the callbacks and start without zoom.
            view.set_image(image)
            self.assertEqual(view.grid_step_mm, 5)
            view.axes.set_ylim(1, 4)
            self.assertEqual(view.grid_step_mm, 1)
            view.clear()
            self.assertIsNone(view.pixels)
            self.assertFalse(view.calibrate_button.isEnabled())
            view.close()

    def test_unknown_scale_does_not_claim_mm(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'unknown.png'
            image = QImage(10, 20, QImage.Format.Format_RGB32)
            image.fill(QColor('black'))
            image.save(str(path))
            view = ImageCoordinates()
            view.set_image(path)
            self.assertIsNone(view.calibration)
            self.assertIsNone(view.grid_step_mm)
            self.assertFalse(view.grid_button.isEnabled())
            self.assertIn('пиксели', view.axes.get_xlabel())
            self.assertNotIn('мм', view.axes.get_xlabel())
            self.assertNotIn('мм', view.dimensions.text())
            self.assertIn('пикс.', view.dimensions.text())
            view.close()

    def test_grid_toggle_preserves_zoom_dimensions_and_slice_preference(self):
        with tempfile.TemporaryDirectory() as temp:
            image = self.make_case(temp)
            detected = image.parent/'detect'/image.name
            detected.parent.mkdir()
            detected.write_bytes(image.read_bytes())
            view = ImageCoordinates()
            try:
                self.assertFalse(view.grid_button.isEnabled())
                view.set_image(image)
                self.assertTrue(view.grid_button.isEnabled())
                self.assertTrue(view.grid_button.isChecked())
                view.toolbar.push_current()
                view.axes.set_xlim(1, 6)
                view.axes.set_ylim(2, 6)
                self.assertEqual(view.grid_step_mm, 1)
                limits = (view.axes.get_xlim(), view.axes.get_ylim())
                dimensions = view.dimensions.text()
                QTest.mouseClick(view.grid_button, Qt.MouseButton.LeftButton)
                view.canvas.draw()
                self.assertIsNone(view.grid_step_mm)
                self.assertIn('выкл.', view.grid_button.text())
                self.assertIn('Сетка выключена', view.status.text())
                self.assertEqual((view.axes.get_xlim(), view.axes.get_ylim()), limits)
                self.assertEqual(view.dimensions.text(), dimensions)
                self.assertTrue(view.axes.axison)
                for axis in (view.axes.xaxis, view.axes.yaxis):
                    self.assertFalse(any(t.gridline.get_visible() for t in axis.get_major_ticks()))
                    self.assertFalse(any(t.gridline.get_visible() for t in axis.get_minor_ticks()))
                # Switching to an annotated slice retains the user's choice.
                view.set_image(detected)
                self.assertFalse(view.grid_button.isChecked())
                self.assertIsNone(view.grid_step_mm)
                view.toolbar.push_current()
                view.axes.set_xlim(1, 6)
                QTest.mouseClick(view.grid_button, Qt.MouseButton.LeftButton)
                view.canvas.draw()
                self.assertEqual(view.grid_step_mm, 1)
                self.assertTrue(all(t.gridline.get_visible() for t in view.axes.xaxis.get_minor_ticks()))
                view.toolbar.home()
                self.assertEqual(view.grid_step_mm, 5)
                view.clear()
                self.assertFalse(view.grid_button.isEnabled())
            finally:
                view.close()

    def test_fragment_dimensions_selection_pan_and_home(self):
        with tempfile.TemporaryDirectory() as temp:
            view = ImageCoordinates()
            view.set_image(self.make_case(temp))
            view.canvas.draw()
            self.assertEqual(view._fragment_dimensions(), (10, 8))
            self.assertIn('10,00 мм', view.dimensions.text())
            view.toolbar.push_current()
            view.axes.set_xlim(1, 6)
            view.axes.set_ylim(2, 6)
            self.assertEqual(view._fragment_dimensions(), (5, 4))
            self.assertIn('высота 4,00 мм', view.dimensions.text())
            view.canvas.draw()
            points = view.axes.transData.transform([(2, 3), (5, 5)])
            view.toolbar.draw_rubberband(None, *points[0], *points[1])
            self.assertIn('Выделение:', view.dimensions.text())
            self.assertIn('3,00 мм × высота 2,00 мм', view.dimensions.text())
            view.toolbar.remove_rubberband()
            self.assertIn('Видимый фрагмент:', view.dimensions.text())
            view.axes.set_xlim(-2, 6)
            view.axes.set_ylim(6, 10)
            self.assertEqual(view._fragment_dimensions(), (6, 2))
            view.axes.set_xlim(20, 25)
            self.assertIn('вне изображения', view.dimensions.text())
            view.toolbar.push_current()
            view.toolbar.home()
            self.assertEqual(view._fragment_dimensions(), (10, 8))
            view.clear()
            self.assertEqual(view.dimensions.text(), '')
            view.close()

    def test_dark_viewport_rulers_and_grid_keep_physical_proportions(self):
        with tempfile.TemporaryDirectory() as temp:
            image = self.make_case(temp)
            view = ImageCoordinates()
            try:
                view.resize(1500, 850)
                view.show()
                self.app.processEvents()
                view.set_image(image)
                view.calibration = ImageCalibration(20.8, 49.4, 'test physical extent')
                view._draw_image()
                view.canvas.draw()
                self.assertEqual(tuple(view.figure.get_facecolor()[:3]),
                                 tuple(int(c,16)/255 for c in ('16','23','2d')))
                points = view.axes.transData.transform([(0,0),(1,1)])
                self.assertAlmostEqual(points[1,0]-points[0,0],points[1,1]-points[0,1])
                spine = view.axes.spines['left']
                line = spine.get_transform().transform(spine.get_path().vertices)
                self.assertAlmostEqual(line[0,0],54*view.canvas.device_pixel_ratio)
                self.assertGreater(view.axes.get_window_extent().x0,line[0,0])
                labels = view.axes.get_xticklabels()
                self.assertTrue(all(label.get_rotation()==0 for label in labels))
                self.assertLess(sum(bool(label.get_text()) for label in labels),len(labels))
                renderer = view.canvas.get_renderer()
                self.assertGreaterEqual(view.axes.xaxis.label.get_window_extent(renderer).y0, 0)
                self.assertGreaterEqual(view.axes.yaxis.label.get_window_extent(renderer).x0, 0)
                view.axes.set_xlim(-100,200)
                view.canvas.draw()
                clip = view.axes.xaxis.get_major_ticks()[0].gridline.get_clip_box()
                self.assertAlmostEqual(clip.x0,view.axes.transData.transform((0,0))[0])
                self.assertEqual(view._fragment_dimensions()[0],200)
            finally:
                view.close()

    def test_legacy_resize_including_minimum_height(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            row = {'Patient ID': '123', 'Columns': 320, 'Rows': 400, 'Z': 5,
                   'Pixel Spacing X': .5, 'Pixel Spacing Y': .6,
                   'Start Slice Location': 10, 'End Slice Location': 2}
            with (folder/'123arrayinfo.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            path = folder/'123_201126_1.png'
            scale = image_calibration(path, 400, 200)
            self.assertAlmostEqual(scale.x_mm, .48)
            self.assertAlmostEqual(scale.y_mm, .05)
            self.assertIsNone(image_calibration(path, 400, 201))


if __name__ == '__main__':
    unittest.main()
