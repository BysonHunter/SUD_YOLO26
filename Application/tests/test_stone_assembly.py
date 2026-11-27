import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import pickle
import csv
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sud_yolo26.domain.dto import SliceDTO
from sud_yolo26.analysis.assembly import assemble_stone,assembly_conflicts,new_result_directory,ASSEMBLY_VERSION,consolidate_clusters
from sud_yolo26.analysis import parameters


def box(x,z,w=1,h=1,size=10):
    return SliceDTO(1,x/size,z/size,w/size,h/size,1)


class AssemblyTests(unittest.TestCase):
    def test_runtime_rejects_ownership_conflict_before_any_results_are_written(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'detect/labels').mkdir(parents=True)
            (root/'stones').mkdir()
            original=root/'stones/old.png';original.write_bytes(b'old preview')
            volume=np.zeros((10,7,20),dtype='int16');volume[4,3,5:14]=500
            np.save(root/'1array.npy',volume)
            fields=['Study Date','Series Description',"Patient's Name",'Patient ID',
                'Spacing Between Slices','Series Number','Start Slice Location','End Slice Location',
                'Slice Thickness','Rows','Columns','Samples per Pixel','Pixel Spacing X',
                'Pixel Spacing Y','Rescale Intercept','Rescale Slope','Shape Z, Y, X','Z','Y','X']
            values=['20200101','test','test','1','1','1','0','9','1','7','20','1','1','1','0','1',
                    '(10,7,20)','10','7','20']
            with (root/'1arrayinfo.csv').open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(fields);writer.writerow(values)
            (root/'detect/labels/1_010101_3.txt').write_text(
                '2 .3 .4 .4 .4 .9\n0 .625 .4 .25 .4 .9\n'
                '1 .275 .45 .05 .1 .9\n1 .625 .45 .05 .1 .9\n')
            output=root/'new-output'
            with patch.object(parameters,'assembly_options',return_value=dict(padding_mm=10)), self.assertRaisesRegex(ValueError,'Неоднозначная принадлежность'):
                parameters.main(root,show_ui=False,output_dir=output)
            self.assertFalse(output.exists())
            self.assertEqual(original.read_bytes(),b'old preview')

    def test_consolidation_merges_shared_components_but_keeps_disconnected_stone(self):
        volume=np.zeros((10,7,10),dtype='int16')
        volume[5,3,2:9]=500
        volume[1,3,7]=700
        before=volume.copy()
        groups=[{3:[box(x,5.5)]} for x in (2.5,5.5,8.5)]+[{3:[box(7.5,1.5)]}]
        groups,stones=consolidate_clusters(volume,groups,(1,1,1))
        self.assertEqual(len(groups),2)
        self.assertEqual(sorted(np.count_nonzero(s.values_zyx) for s in stones),[1,7])
        self.assertEqual(assembly_conflicts(list(enumerate(stones))),[])
        np.testing.assert_array_equal(volume,before)

    def test_branch_union_and_detection_seeds_reject_unrelated_halo_voxel(self):
        volume=np.zeros((10,7,10),dtype='int16')
        volume[5,1:6,5]=500
        volume[5,4,2:9]=500
        volume[3,3,3]=900
        before=volume.copy()
        result=assemble_stone(volume,{1:[box(5.5,5.5)],4:[box(5.5,5.5,7)]},(1,1,.5))
        self.assertEqual(np.count_nonzero(result.values_zyx),11)
        self.assertEqual(result.discarded_voxels,1)
        self.assertEqual(result.selected_components,1)
        np.testing.assert_array_equal(result.lower_xyz,[2,1,5])
        np.testing.assert_array_equal(result.upper_xyz,[9,6,6])
        np.testing.assert_array_equal(volume,before)

    def test_neighbors_in_same_search_roi_are_not_assigned_to_both_stones(self):
        volume=np.zeros((10,7,10),dtype='int16')
        volume[5,3,2]=500
        volume[5,3,7:9]=700
        first=assemble_stone(volume,{3:[box(2.5,5.5)]},(1,1,1),5)
        second=assemble_stone(volume,{3:[box(8,5.5,2)]},(1,1,1),5)
        self.assertEqual(np.count_nonzero(first.values_zyx),1)
        self.assertEqual(np.count_nonzero(second.values_zyx),2)
        self.assertEqual(assembly_conflicts([('a',first),('b',second)]),[])

    def test_shared_component_reports_conflict_instead_of_silent_double_counting(self):
        volume=np.full((3,3,3),500)
        cluster={1:[box(1.5,1.5,size=3)]}
        stone=assemble_stone(volume,cluster,(1,1,1))
        conflict=assembly_conflicts([('a',stone),('b',stone)])
        self.assertEqual(conflict,[dict(first='a',second='b',shared_voxels=27)])

    def test_touching_lobes_remain_one_stone(self):
        volume=np.zeros((10,5,10))
        volume[2:5,1:4,1:4]=500
        volume[2:5,1:4,5:8]=800
        volume[3,2,4]=300
        result=assemble_stone(volume,{2:[box(4.5,3.5,8,4)]},(1,1,1))
        self.assertEqual(result.selected_components,1)
        self.assertEqual(np.count_nonzero(result.values_zyx),55)

    def test_single_layer_boundary_clipping_and_strict_hu_threshold(self):
        volume=np.zeros((3,3,3));volume[0,0,0]=161;volume[0,0,1]=160
        result=assemble_stone(volume,{0:[box(.5,.5,size=3)]},(.5,.8,1.25))
        self.assertEqual(result.values_zyx.shape,(1,1,1))
        self.assertEqual(result.values_zyx.item(),161)
        np.testing.assert_array_equal(result.lower_xyz,[0,0,0])
        np.testing.assert_array_equal(result.upper_xyz,[1,1,1])
        self.assertTrue(result.search_boundary_touched)

    def test_invalid_inputs_and_absent_seed_are_explicit_errors(self):
        volume=np.zeros((3,3,3))
        cluster={1:[box(1.5,1.5,size=3)]}
        for spacing,padding in [((0,1,1),2),((1,1,1),-1),((1,1,1),float('nan'))]:
            with self.assertRaises(ValueError):
                assemble_stone(volume,cluster,spacing,padding)
        with self.assertRaisesRegex(ValueError,'HU >160'):
            assemble_stone(volume,cluster,(1,1,1))

    def test_new_results_never_reuse_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);legacy=root/'stones';legacy.mkdir()
            original=legacy/'old.png';original.write_bytes(b'original')
            a=new_result_directory(root);b=new_result_directory(root)
            self.assertNotEqual(a,b)
            self.assertTrue(a.is_relative_to(legacy/'assembly_v2'))
            with self.assertRaises(FileExistsError):
                new_result_directory(root,a)
            self.assertEqual(original.read_bytes(),b'original')

    def test_versioned_cache_and_options_work_without_legacy_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'detect/labels').mkdir(parents=True)
            (root/'detect/labels/a_1.txt').write_text('1 .5 .5 .1 .1 .9')
            (root/'info.csv').write_text('metadata')
            np.save(root/'123array.npy',np.zeros((2,2,2)))
            result_dir=new_result_directory(root)
            legacy=root/'stones/stones_result_cache.pkl';legacy.write_bytes(b'legacy')
            with patch.object(parameters,'assembly_options',return_value={'padding_mm':2.}):
                signature=parameters._detection_signature(root)
                self.assertIsNotNone(signature)
                self.assertIsNone(parameters.load_cached_result(root))
                payload=dict(assembly_version=ASSEMBLY_VERSION,detection_signature=signature,
                             stones_dir_path=str(result_dir))
                Path(parameters._cache_file_path(root)).write_bytes(pickle.dumps(payload))
                self.assertIsNotNone(parameters.load_cached_result(root))
                with patch.object(parameters,'ASSEMBLY_VERSION','future-version'):
                    self.assertIsNone(parameters.load_cached_result(root))
            with patch.object(parameters,'assembly_options',return_value={'padding_mm':3.}):
                self.assertIsNone(parameters.load_cached_result(root))
            self.assertEqual(legacy.read_bytes(),b'legacy')


if __name__=='__main__':
    unittest.main()
