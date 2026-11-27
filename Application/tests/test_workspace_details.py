import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sud_yolo26.ui_workspace.details import StoneDetailsDialog, laser_values
from sud_yolo26.ui_workspace.window import WorkspaceWindow
from sud_yolo26.reporting.pdf_report import _calc_laser_table
from sud_yolo26.ui_workspace.laser_legend import MODE_LEGEND, pdf_cell_colour, colour_mode


class StoneDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def result(self, root):
        rows = []
        for index, mass in enumerate((1.0, .25)):
            row = [0] * 28
            row[0:5] = [index, 232 + index, 1.3, 1.6, .9]
            row[8:16] = [.536, 1624, mass, 1.84, mass, 1368, 161, 619]
            row[20:22] = [227, 238]
            row[25:28] = [float('nan')] * 3
            rows.append(row)
        return dict(RS_params=rows, LS_params=[], stones_dir_path=str(root),
                    param_numpy=['20201212', 'Native', '', '5544'], pdfFileName='')

    def test_laser_model_matches_existing_pdf_for_each_regime(self):
        for mass in (1.0, .25, .986):
            cfg, energies, frequencies, actual = laser_values(mass)
            old_f, old_e, expected, _ = _calc_laser_table(mass)
            np.testing.assert_array_equal(actual, expected)
            np.testing.assert_allclose(energies, [float(e) for e in old_e])
            np.testing.assert_allclose(frequencies, [float(f) for f in old_f])
            self.assertEqual(cfg.gamma, .4)
            self.assertAlmostEqual(energies[-1], 2.4)

    def test_every_cell_colour_and_tooltip_matches_pdf_and_legend(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = StoneDetailsDialog(temp, self.result(Path(temp)))
            try:
                for stone_index in (0, 1):
                    dialog.stones.setCurrentRow(stone_index)
                    _, _, expected, bands = _calc_laser_table(dialog.current_stone()[10])
                    for i, row in enumerate(expected):
                        for j, value in enumerate(row):
                            band = next(b for b in bands if b['min'] <= value <= b['max'])
                            item = dialog.laser.item(i, j)
                            self.assertEqual(item.background().color().getRgb()[:3], band['background'])
                            self.assertEqual(item.foreground().color().getRgb()[:3], band['text'])
                            self.assertIn(colour_mode(band['background']), item.toolTip())
                            dialog.laser.setCurrentCell(i, j)
                            self.assertEqual(dialog.mode_name.text(), colour_mode(band['background']))
                dialog.side.setCurrentIndex(1)
                self.assertEqual(dialog.mode_name.text(), '')
                self.assertEqual(dialog.mode_name.styleSheet(), '')
            finally:
                dialog.close()

    def test_legend_has_eight_modes_and_groups_extra_pdf_shades(self):
        self.assertEqual(len(MODE_LEGEND), 8)
        self.assertEqual(colour_mode((30, 136, 229)), colour_mode((3, 155, 229)))
        self.assertEqual(colour_mode((94, 53, 177)), colour_mode((57, 73, 171)))
        cfg, *_ = laser_values(1)
        self.assertEqual(len(set(colour_mode(c) for c in cfg.color_theme)), 8)

    def test_shared_and_flat_colour_boundaries_match_first_pdf_interval(self):
        _, _, _, bands = _calc_laser_table(1)
        for i in range(len(bands) - 1):
            self.assertIs(pdf_cell_colour(bands[i]['max'], bands), bands[i])
        _, _, values, flat = _calc_laser_table(.000001)
        self.assertIs(pdf_cell_colour(values[0, 0], flat), flat[0])

    def test_selected_cell_preserves_pdf_background_in_rendered_table(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = StoneDetailsDialog(temp, self.result(Path(temp)))
            try:
                dialog.tabs.setCurrentIndex(1)
                dialog.show()
                self.app.processEvents()
                dialog.laser.setCurrentCell(0, 0)
                self.app.processEvents()
                rect = dialog.laser.visualItemRect(dialog.laser.item(0, 0))
                image = dialog.laser.viewport().grab().toImage()
                self.assertEqual(image.pixelColor(rect.left() + 8, rect.top() + 8).getRgb()[:3],
                                 dialog.laser.item(0, 0).background().color().getRgb()[:3])
            finally:
                dialog.close()

    def test_selection_refreshes_parameters_preview_and_regime_mass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = QImage(20, 30, QImage.Format.Format_RGB32)
            image.fill(0xff777777)
            image.save(str(root / '5544_20201212_cor_233.png'))
            dialog = StoneDetailsDialog(root, self.result(root), index=1)
            try:
                self.assertEqual(dialog.stones.currentRow(), 1)
                self.assertEqual(dialog.metrics[2].text(), '0,250')
                self.assertEqual(dialog.image_caption.text(), 'Слой 233')
                self.assertFalse(dialog.image._source_pixmap.isNull())
                dialog.laser.setCurrentCell(4, 2)
                self.assertEqual(dialog.time.text(), '0,06')
                self.assertIn('1,0 Дж', dialog.regime.text())
                self.assertIn('10 Гц', dialog.regime.text())
                dialog.stones.setCurrentRow(0)
                self.assertEqual(dialog.time.text(), '0,25')
                self.assertIn('1,000 г', dialog.laser_mass.text())
                self.assertEqual(dialog.laser.currentRow(), 4)
                self.assertEqual(dialog.laser.currentColumn(), 2)
                self.assertIn('Не вычислено', [dialog.parameters.item(i, 1).text()
                                             for i in range(dialog.parameters.rowCount())])
                self.assertEqual(dialog.parameters.item(13, 1).text(), '227–237')
                self.assertTrue(dialog.image._source_pixmap.isNull())
            finally:
                dialog.close()

    def test_voxel_preview_uses_current_orientation_from_npy_and_preserves_saved_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            values=np.full((4,3,5),500,dtype=np.int16)
            path=root/'st_rk_0.npy';np.save(path,values)
            old_png=root/'stonerk_0_1.png';old_png.write_bytes(b'old preview')
            result=self.result(root)
            params=['0']*20
            params[4],params[6],params[7],params[12],params[13],params[17]='.5','0','9.5','.8','.6','20'
            result['param_numpy']=params
            before=path.read_bytes()
            dialog=StoneDetailsDialog(root,result)
            try:
                dialog.preview.setCurrentIndex(2)
                self.assertFalse(dialog.image._source_pixmap.isNull())
                self.assertEqual(len(dialog._voxel_pixmaps),1)
                with patch('sud_yolo26.visualization.stone_geometry.voxel_figure',side_effect=AssertionError('cache missed')):
                    dialog._show_preview()
                dialog.preview.setCurrentIndex(3)
                self.assertFalse(dialog.image._source_pixmap.isNull())
                self.assertEqual(len(dialog._voxel_pixmaps),2)
                self.assertEqual(path.read_bytes(),before)
                self.assertEqual(old_png.read_bytes(),b'old preview')
            finally:
                dialog.close()

    def test_empty_side_clears_previous_values_and_can_be_opened_directly(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = StoneDetailsDialog(temp, self.result(Path(temp)))
            try:
                dialog.side.setCurrentIndex(1)
                self.assertIsNone(dialog.current_stone())
                self.assertEqual(dialog.time.text(), '—')
                self.assertEqual(dialog.laser.rowCount(), 0)
                self.assertEqual(dialog.parameters.rowCount(), 0)
                self.assertTrue(dialog.image._source_pixmap.isNull())
                self.assertFalse(dialog.stone_3d.isEnabled())
                self.assertEqual(dialog.metrics[2].text(), '—')
                dialog.side.setCurrentIndex(0)
                self.assertEqual(dialog.laser.rowCount(), 19)
                self.assertEqual(dialog.metrics[2].text(), '1,000')
            finally:
                dialog.close()
            empty = StoneDetailsDialog(temp, self.result(Path(temp)), side=1)
            self.assertIsNone(empty.current_stone())
            empty.close()

    def test_workspace_opens_new_details_with_current_selection(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            window = WorkspaceWindow()
            try:
                window.study = SimpleNamespace(coronal=Path(temp))
                window.result = self.result(Path(temp))
                window.stones.blockSignals(True)
                window.stones.addItems(['one', 'two'])
                window.stones.setCurrentRow(1)
                window.stones.blockSignals(False)
                # Avoid study filesystem access while setting up this bridge-only test.
                with patch.object(window, 'refresh_images'), patch.object(StoneDetailsDialog, 'exec', return_value=0) as run:
                    window.show_details()
                    run.assert_called_once()
                self.assertEqual(window.stones.currentRow(), 1)
            finally:
                window.close()

    def test_exit_button_closes_idle_window_and_keeps_active_job_alive(self):
        window = WorkspaceWindow()
        window.show()
        window.exit_button.click()
        self.assertFalse(window.isVisible())
        window.show()
        window._start('Test', lambda report: QTest.qSleep(200), lambda result: None)
        try:
            window.exit_button.click()
            self.assertTrue(window.isVisible())
            self.assertIn('Дождитесь', window.status.text())
        finally:
            window.job.wait()
            self.app.processEvents()
            window.close()


if __name__ == '__main__':
    unittest.main()
