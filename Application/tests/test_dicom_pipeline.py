import dataclasses
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset, Dataset
from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, EnhancedCTImageStorage
from pydicom.sequence import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sud_yolo26.imaging.dicom_source import discover, effective_header, frame_pixels, assess, sha256
from sud_yolo26.imaging.lps import native_geometry, canonical_geometry, plane_geometry, png_to_lps, lps_to_volume_xyz, yolo_box_to_lps
from sud_yolo26.imaging.pipeline import ExportConfig, export_stack, window_png
from sud_yolo26.imaging.selection import select_stacks, prepare_stacks, dicom_identity, path_component
from sud_yolo26.imaging.projections import acquisition_date, coronal_projection, select_window, apply_window
from sud_yolo26.imaging.geometry import render_coronal


def make_file(path, k, *, tilted=False, enhanced=False):
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = EnhancedCTImageStorage if enhanced else CTImageStorage
    meta.MediaStorageSOPInstanceUID = f'1.2.826.0.1.3680043.10.99.{k+1}'
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b'\0'*128)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID; ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.PatientID = 'PRIVATE-ID'; ds.PatientName = 'PRIVATE^NAME'
    ds.StudyInstanceUID = '1.2.3.1'; ds.SeriesInstanceUID = '1.2.3.2'; ds.FrameOfReferenceUID = '1.2.3.3'
    ds.StudyDate='20201212';ds.StudyTime='120000';ds.StudyID='STUDY1'
    ds.Modality = 'CT'; ds.SeriesDescription = 'Native'; ds.SeriesNumber = 2
    ds.Rows = 4; ds.Columns = 6; ds.PixelSpacing = [2., 1.]
    ds.ImageOrientationPatient = [1,0,0,0,1,0]; ds.ImagePositionPatient = [.2*k if tilted else 0,0,k*.5]
    ds.SliceThickness = 1; ds.InstanceNumber = 100-k
    ds.RescaleSlope = .5; ds.RescaleIntercept = -100; ds.RescaleType = 'HU'
    ds.PhotometricInterpretation = 'MONOCHROME2'; ds.SamplesPerPixel = 1
    ds.BitsAllocated = 16; ds.BitsStored = 16; ds.HighBit = 15; ds.PixelRepresentation = 1
    ds.PixelPaddingValue = -2000; ds.WindowCenter = 40; ds.WindowWidth = 350
    raw = np.arange(24, dtype=np.int16).reshape(4,6)+100*k
    raw[0,0] = -2000
    if enhanced:
        ds.NumberOfFrames = 3
        shared = Dataset()
        for seq, tags in [('PlaneOrientationSequence', ['ImageOrientationPatient']),
                          ('PixelMeasuresSequence', ['PixelSpacing', 'SliceThickness'])]:
            item = Dataset()
            for tag in tags:
                setattr(item, tag, getattr(ds,tag))
            setattr(shared, seq, Sequence([item]))
        ds.SharedFunctionalGroupsSequence = Sequence([shared])
        groups = []
        for index in range(3):
            group = Dataset(); position = Dataset(); position.ImagePositionPatient = [0,0,index*.5]
            group.PlanePositionSequence = Sequence([position])
            transform = Dataset(); transform.RescaleSlope = index+1; transform.RescaleIntercept = -10; transform.RescaleType = 'HU'
            group.PixelValueTransformationSequence = Sequence([transform]); groups.append(group)
        ds.PerFrameFunctionalGroupsSequence = Sequence(groups)
        raw = np.stack([raw+i*100 for i in range(3)])
    ds.PixelData = raw.tobytes()
    ds.save_as(path, enforce_file_format=True)
    return ds, raw


class DicomPipelineTests(unittest.TestCase):
    def fixture(self, folder, tilted=False):
        for k, name in enumerate(['z.png', 'a', 'n']):
            make_file(folder / name, k, tilted=tilted)
        stacks, report = discover(folder)
        code = next(iter(stacks))
        return code, stacks[code], report

    def test_full_export_roundtrip_checksums_versions_and_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); source=p/'input'; source.mkdir()
            code, frames, report = self.fixture(source)
            config = ExportConfig('PTEST', 'E001', 'Synthetic phantom, no contrast', compatibility=True)
            folder = export_stack(frames, p/'out', code, config, report)
            h = np.load(folder/'volume/hu_lps.npy')
            self.assertEqual(h.shape, (3,4,6))
            self.assertEqual(h[2,1,1], (200+7)*.5-100)
            self.assertEqual(np.load(folder/'volume/valid_lps.npy')[:,0,0].sum(), 0)
            geometry = json.loads((folder/'geometry/geometry.json').read_text())
            self.assertEqual(geometry['resampling']['method'], 'identity_copy')
            planes=list((folder/'geometry/planes').glob('*.json'))
            self.assertEqual(len(planes), 7)
            self.assertTrue((folder/'images/coronal').exists())
            for path in planes:
                sidecar=json.loads(path.read_text())
                self.assertTrue((path.parent/sidecar['geometry_path']).exists())
                image=cv2.imdecode(np.frombuffer((path.parent/sidecar['image_path']).read_bytes(),np.uint8),0)
                self.assertEqual(image.shape,(sidecar['height'],sidecar['width']))
                point=png_to_lps(sidecar,[2,1])
                xyz=lps_to_volume_xyz(geometry['canonical_volume'],point)
                source=np.array(sidecar.get('png_to_plane_pixel_centers',np.eye(3))) @ [2,1,1]
                expected=[2,1,sidecar['canonical_slice_index']] if sidecar['plane']=='AX' else [source[0],sidecar['slice_index'],2-source[1]]
                np.testing.assert_allclose(xyz,expected)
                corners=yolo_box_to_lps(sidecar,[.5,.5,1,1])
                np.testing.assert_allclose(corners[0],png_to_lps(sidecar,[-.5,-.5]))
            for line in (folder/'checksums.sha256').read_text().splitlines():
                digest,name=line.split('  ',1)
                self.assertEqual(sha256(folder/name),digest)
            self.assertFalse((folder/'compatibility').exists())
            for y in range(h.shape[1]):
                path=folder/f'images/coronal/PRIVATE-ID_20201212_cor_{y}.png'
                image=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),0)
                mapped=apply_window(h[::-1,y,:],np.load(folder/'volume/valid_lps.npy')[::-1,y,:],select_window(frames[0].header))
                expected=np.clip(cv2.resize(mapped.astype(np.int16),(200,200),interpolation=cv2.INTER_CUBIC),0,255).astype(np.uint8)
                np.testing.assert_array_equal(image,expected)
            self.assertTrue((folder/'images/axial/PRIVATE-ID_20201212_axe_2.png').exists())
            self.assertEqual(folder.parent.name,'20201212')
            self.assertEqual(folder.parents[1].name,'PRIVATE-ID')
            self.assertNotIn('PRIVATE^NAME', str(folder))
            selection=json.loads((folder/'selection.json').read_text())
            self.assertEqual(selection['identity']['patient_id'],'PRIVATE-ID')
            self.assertEqual(selection['contrast_status'],'UNKNOWN')
            second=export_stack(frames,p/'out',code,config,report)
            self.assertEqual(second.name,'V002')
            self.assertEqual(sha256(second/'volume/hu_lps.npy'),sha256(folder/'volume/hu_lps.npy'))
            self.assertEqual(json.loads((second/'geometry/geometry.json').read_text())['identity']['geometry_sha256'],geometry['identity']['geometry_sha256'])
            for frame in frames:frame.header.SeriesInstanceUID='1.2.3.77'
            third=export_stack(frames,p/'out','S'+'f'*16,config,report)
            self.assertEqual(third.name,'V003')
            self.assertEqual(len(third.relative_to(p/'out').parts),3)

    def test_enhanced_effective_per_frame_transform_and_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); ds,raw=make_file(p/'enhanced',0,enhanced=True)
            stacks,report=discover(p); frames=next(iter(stacks.values()))
            self.assertEqual(len(frames),3)
            self.assertEqual(float(frames[0].header.RescaleSlope),1)
            self.assertEqual(float(frames[2].header.RescaleSlope),3)
            for i,f in enumerate(frames):
                hu,valid=frame_pixels(f)
                np.testing.assert_array_equal(hu,raw[i].astype(np.float32)*(i+1)-10)
            self.assertEqual(float(ds.RescaleSlope),.5)

    def test_padding_units_selection_and_duplicate_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); code,frames,report=self.fixture(p)
            self.assertEqual(assess(frames)['contrast_status'],'UNKNOWN')
            hu,mask=frame_pixels(frames[0]); self.assertFalse(mask[0,0])
            self.assertEqual(hu[0,0],-1100)
            for config in [ExportConfig(axial_step=0), ExportConfig(window_code='../escape')]:
                with self.assertRaises(ValueError):export_stack(frames,p/'out',code,config,report)
            (p/'copy').write_bytes((p/'a').read_bytes())
            _, report=discover(p); self.assertEqual(len(report['duplicate_copies']),1)
            ds=pydicom.dcmread(p/'copy');ds.SeriesDescription='different';ds.save_as(p/'copy',enforce_file_format=True)
            _,report=discover(p);self.assertEqual(len(report['conflicts']),1)

    def test_automatic_identity_and_series_selection_without_dialogs(self):
        from PyQt6.QtWidgets import QApplication
        from sud_yolo26.imaging import importer
        from sud_yolo26.application import state
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);code,frames,report=self.fixture(p)
            for frame in frames:frame.header.PatientID='5544'
            self.assertEqual(dicom_identity(frames)['patient_code'],'5544')
            app=QApplication.instance() or QApplication([])
            with patch.object(importer,'discover',return_value=({code:frames},report)), \
                 patch.object(importer,'export_stack',return_value=p/'result') as export, \
                 patch('PyQt6.QtWidgets.QInputDialog.getText',side_effect=AssertionError('Unexpected text input')), \
                 patch('PyQt6.QtWidgets.QInputDialog.getItem',side_effect=AssertionError('Unexpected series input')), \
                 patch.object(state,'global_messages',[]):
                result=importer.main(p,p/'out')
                self.assertEqual(result,p/'result')
                self.assertEqual(export.call_count,1)
            self.assertEqual(path_component('5544','X'),'5544')
            self.assertNotIn('/',path_component('../escape','X'))
            self.assertNotEqual(path_component('A/B','X'),path_component('A?B','X'))

    def test_automatic_selection_prefers_native_and_keeps_ambiguous_series_separate(self):
        import copy
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);code,frames,_=self.fixture(p)
            arterial=copy.deepcopy(frames)
            for frame in arterial:
                frame.header.SeriesInstanceUID='1.2.3.99';frame.header.SeriesDescription='Arterial';frame.header.ContrastBolusAgent='Agent'
            self.assertEqual([item[0] for item in select_stacks({'native':frames,'arterial':arterial})],['native'])
            for frame in frames:frame.header.SeriesDescription='Standard'
            self.assertEqual(len(select_stacks({'standard':frames,'arterial':arterial})),2)

    def test_regular_tilt_resampling_and_irregular_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'input';source.mkdir()
            code,frames,report=self.fixture(source,tilted=True)
            ordered,native=native_geometry(frames)
            self.assertAlmostEqual(native['index_xyz_to_lps'][0][2],.2)
            folder=export_stack(frames,p/'out',code,ExportConfig('PTEST','E001','Synthetic phantom'),report)
            g=json.loads((folder/'geometry/geometry.json').read_text())
            self.assertEqual(g['resampling']['interpolation_order'],1)
            self.assertTrue((np.load(folder/'volume/valid_lps.npy')==0).any())
            ordered[-1].header.ImagePositionPatient=[.4,0,2.]
            with self.assertRaises(ValueError):native_geometry(ordered)

    def test_window_has_fixed_bounds_and_invalid_background(self):
        np.testing.assert_array_equal(window_png(np.array([[-1,0,.5,1,2]],float),np.array([[1,1,1,1,0]]),0,1),[[0,0,128,255,0]])

    def test_legacy_instance_sort_is_independent_of_file_order(self):
        from itertools import permutations
        with tempfile.TemporaryDirectory() as tmp:
            _,frames,_=self.fixture(Path(tmp))
            for frame in frames:
                k=int(float(frame.header.ImagePositionPatient[2])*2)
                frame.header.InstanceNumber=3-k;frame.header.SliceLocation=100-k
            for shuffled in permutations(frames):
                ordered,geometry=native_geometry(list(shuffled))
                self.assertEqual([int(f.header.InstanceNumber) for f in ordered],[1,2,3])
                self.assertEqual(geometry['sorting']['method'],'InstanceNumber_ascending')
                self.assertEqual(geometry['index_xyz_to_lps'][2][2],-.5)
                self.assertEqual(geometry['normal_step_mm'],.5)

    def test_legacy_slice_location_sort_and_physical_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,frames,_=self.fixture(Path(tmp))
            for frame in frames:
                k=int(float(frame.header.ImagePositionPatient[2])*2)
                frame.header.InstanceNumber=k+1;frame.header.SliceLocation=100-k
            ordered,geometry=native_geometry(frames[::-1])
            self.assertEqual(geometry['sorting']['method'],'SliceLocation_descending')
            self.assertEqual([float(f.header.SliceLocation) for f in ordered],[100,99,98])
            for frame in frames:
                k=int(float(frame.header.ImagePositionPatient[2])*2)
                frame.header.SliceLocation=[1,3,2][k];frame.header.InstanceNumber=1
            ordered,geometry=native_geometry(frames)
            self.assertEqual(geometry['sorting']['method'],'ImagePositionPatient_normal_ascending')
            self.assertEqual([float(f.header.ImagePositionPatient[2]) for f in ordered],[0,.5,1])

    def test_sorted_hu_volume_matches_slice_metadata_and_canonical_orientation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'input';source.mkdir()
            for k in range(3):
                ds,raw=make_file(source/str(k),k)
                ds.InstanceNumber=3-k;ds.SliceLocation=100-k
                ds.save_as(source/str(k),enforce_file_format=True)
            stacks,report=discover(source);code=next(iter(stacks));frames=stacks[code]
            folder=export_stack(frames[::-1],p/'out',code,ExportConfig(),report)
            native=np.load(folder/'volume/hu_native.npy');canonical=np.load(folder/'volume/hu_lps.npy')
            np.testing.assert_array_equal(native,canonical[::-1])
            self.assertEqual(native[:,1,1].tolist(),[3.5,-46.5,-96.5])
            geometry=json.loads((folder/'geometry/geometry.json').read_text())
            self.assertEqual(geometry['native_volume']['sorting']['method'],'InstanceNumber_ascending')
            self.assertEqual(geometry['resampling']['method'],'axis_permutation_flip_copy')
            provenance=json.loads((folder/'provenance/source_frames.json').read_text())
            self.assertEqual([row['instance_number'] for row in provenance],['1','2','3'])
            sidecar=json.loads((folder/'geometry/planes/PRIVATE-ID_20201212_axe_0.json').read_text())
            self.assertEqual(sidecar['slice_index'],0)
            self.assertEqual(sidecar['canonical_slice_index'],2)
            self.assertEqual(sidecar['native_layer_index'],0)
            image=cv2.imdecode(np.frombuffer((folder/'images/axial/PRIVATE-ID_20201212_axe_0.png').read_bytes(),np.uint8),0)
            np.testing.assert_array_equal(image,apply_window(native[0],np.load(folder/'volume/valid_native.npy')[0],select_window(frames[0].header)))

    def test_projection_names_use_acquisition_date_and_real_layer_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'input';source.mkdir()
            for k in range(3):
                ds,_=make_file(source/str(k),k)
                ds.AcquisitionDate='20201211';ds.save_as(source/str(k),enforce_file_format=True)
            stacks,report=discover(source);code=next(iter(stacks));frames=stacks[code]
            folder=export_stack(frames,p/'out',code,ExportConfig(axial_step=2,coronal_image_count=2),report)
            self.assertEqual(sorted(f.name for f in (folder/'images/axial').glob('*.png')),
                             ['PRIVATE-ID_20201211_axe_0.png','PRIVATE-ID_20201211_axe_2.png'])
            self.assertEqual(sorted(f.name for f in (folder/'images/coronal').glob('*.png')),
                             ['PRIVATE-ID_20201211_cor_1.png','PRIVATE-ID_20201211_cor_2.png'])
            self.assertEqual(folder.parent.name,'20201212')
            self.assertFalse((folder/'compatibility').exists())

    def test_coronal_and_axial_use_same_window_independent_of_dicom_voi(self):
        volume=np.broadcast_to(np.linspace(-200,300,200,dtype=np.float32),(200,4,200)).copy()
        valid=np.ones(volume.shape,np.uint8);valid[100,2,199]=0
        canonical=dict(shape_zyx=[200,4,200],spacing_xyz_mm=[1,2,1],origin_lps_mm=[0,0,0])
        header=Dataset();header.WindowCenter=1000;header.WindowWidth=10
        cor,sidecar=coronal_projection(volume,canonical,header,2,valid,-100,200,'WTEST')
        expected=window_png(volume[::-1,2,:],valid[::-1,2,:],-100,200)
        np.testing.assert_array_equal(cor,expected)
        self.assertEqual(sidecar['window_bounds_hu'],[-100,200])
        self.assertEqual(sidecar['window_function'],'linear_clip_bounds_round_half_up')
        self.assertEqual(sidecar['window_profile'],'WTEST')

    def test_dicom_window_matches_pydicom_and_is_shared_between_planes(self):
        values=np.array([[-300,-135,-134.5,0,40,100,214,215,500]],np.float32)
        valid=np.ones(values.shape,np.uint8)
        header=Dataset();header.BitsStored=8;header.PixelRepresentation=0;header.PhotometricInterpretation='MONOCHROME2'
        header.WindowCenter=[40,100];header.WindowWidth=[350,500]
        for function,width in [('LINEAR',350),('LINEAR',1),('LINEAR_EXACT',350),('SIGMOID',350)]:
            header.VOILUTFunction=function;header.WindowWidth=[width,500]
            window=select_window(header)
            expected=np.floor(pydicom.pixels.apply_windowing(values,header,index=0)+.5).astype(np.uint8)
            np.testing.assert_array_equal(apply_window(values,valid,window),expected)
            self.assertEqual(window['window_source'],'DICOM')
            self.assertEqual(window['window_center'],40)
        volume=np.broadcast_to(np.linspace(-300,500,200,dtype=np.float32),(200,4,200)).copy()
        header.VOILUTFunction='LINEAR';header.WindowWidth=[350,500]
        canonical=dict(shape_zyx=[200,4,200],spacing_xyz_mm=[1,2,1],origin_lps_mm=[0,0,0])
        cor,sidecar=coronal_projection(volume,canonical,header,2)
        np.testing.assert_array_equal(cor,apply_window(volume[::-1,2,:],np.ones((200,200),np.uint8),select_window(header)))
        self.assertEqual(sidecar['window_source'],'DICOM')

    def test_reflected_axes_copy_without_interpolating_hu(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'input';source.mkdir();raws=[]
            for k in range(3):
                path=source/str(k);ds,raw=make_file(path,k);raws.append(raw)
                ds.ImageOrientationPatient=[-1,0,0,0,1,0];ds.ImagePositionPatient=[5,0,k*.5]
                ds.save_as(path,enforce_file_format=True)
            stacks,report=discover(source);code=next(iter(stacks))
            folder=export_stack(stacks[code],p/'out',code,ExportConfig('PTEST','E001','Synthetic'),report)
            geometry=json.loads((folder/'geometry/geometry.json').read_text())
            self.assertEqual(geometry['resampling']['method'],'axis_permutation_flip_copy')
            expected=np.stack(raws).astype(np.float32)[:,:,::-1]*.5-100
            np.testing.assert_array_equal(np.load(folder/'volume/hu_lps.npy'),expected)

    def test_dicomdir_inventory_reads_only_index_and_selected_series(self):
        from pydicom.fileset import FileSet, DIRECTORY_RECORDERS
        from sud_yolo26.imaging.dicom_source import IndexedSeries
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'source';source.mkdir();fs=FileSet()
            for k in range(6):
                path=source/f'{k}';ds,_=make_file(path,k)
                if k>=3:
                    ds.SeriesInstanceUID='1.2.3.99';ds.SeriesDescription='Arterial';ds.SeriesNumber=6
                    ds.ImagePositionPatient=[0,0,(k-3)*.5];ds.save_as(path,enforce_file_format=True)
                recorder=DIRECTORY_RECORDERS['SERIES']
                def series_record(dataset):
                    record=recorder(dataset);record.SeriesDescription=dataset.SeriesDescription
                    return record
                with patch.dict(DIRECTORY_RECORDERS,{'SERIES':series_record}):fs.add(path)
            root=p/'indexed';fs.write(root)
            original=pydicom.dcmread
            with patch('sud_yolo26.imaging.dicom_source.pydicom.dcmread',wraps=original) as reader, \
                 patch.object(Path,'rglob',side_effect=AssertionError('Unexpected recursive scan')):
                stacks,report=discover(root)
                self.assertEqual(reader.call_count,1)
                self.assertEqual(report['image_headers_read'],0)
                self.assertTrue(all(isinstance(s,IndexedSeries) for s in stacks.values()))
                chosen=prepare_stacks(stacks,report)
                self.assertEqual(len(chosen),1)
                self.assertEqual(chosen[0][3]['description'],'Native')
                opened=[Path(call.args[0]) for call in reader.call_args_list[1:]]
                native=next(s for s in stacks.values() if str(s.header.SeriesDescription)=='Native')
                self.assertEqual(set(opened),set(native.references))
                self.assertEqual(report['image_headers_read'],3)

    def test_dicomdir_missing_and_unsafe_references_do_not_trigger_scan(self):
        from pydicom.fileset import FileSet
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'source';source.mkdir();fs=FileSet()
            for k in range(3):
                path=source/str(k);make_file(path,k);fs.add(path)
            root=p/'indexed';fs.write(root)
            stacks,report=discover(root)
            selected=next(iter(stacks.values()));missing=next(iter(selected.references));missing.unlink()
            prepare_stacks(stacks,report)
            self.assertEqual(len(report['missing_references']),1)
            ds=pydicom.dcmread(root/'DICOMDIR')
            image=next(r for r in ds.DirectoryRecordSequence if hasattr(r,'ReferencedFileID'))
            image.ReferencedFileID=['..','OUTSIDE']
            ds.save_as(root/'DICOMDIR',enforce_file_format=True)
            _,report=discover(root)
            self.assertEqual(len(report['unsafe_references']),1)

    def test_oblique_affine_interpolation_matches_physical_linear_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); source=p/'in';source.mkdir()
            angle=np.deg2rad(25);r=np.array([np.cos(angle),np.sin(angle),0]);c=np.array([-np.sin(angle),np.cos(angle),0])
            for k in range(3):
                path=source/f'f{k}';ds,_=make_file(path,k)
                ds.ImageOrientationPatient=np.r_[r,c].tolist()
                ds.PixelPaddingValue=-30000; ds.RescaleSlope=.001;ds.RescaleIntercept=0
                yy,xx=np.indices((4,6));points=xx[...,None]*r+yy[...,None]*2*c+np.array([0,0,k*.5])
                raw=np.rint((points[...,0]+2*points[...,1]+3*points[...,2])*1000).astype(np.int16)
                ds.PixelData=raw.tobytes();ds.save_as(path,enforce_file_format=True)
            stacks,report=discover(source);code=next(iter(stacks));frames=stacks[code]
            folder=export_stack(frames,p/'out',code,ExportConfig('PTEST','E001','Synthetic'),report)
            geometry=json.loads((folder/'geometry/geometry.json').read_text());canonical=geometry['canonical_volume']
            output=np.load(folder/'volume/hu_lps.npy');mask=np.load(folder/'volume/valid_lps.npy')
            inverse=np.linalg.inv(geometry['native_volume']['index_xyz_to_lps'])
            errors=[]
            for k,j,i in np.argwhere(mask):
                point=np.array(canonical['origin_lps_mm'])+np.array([i,j,k])*canonical['spacing_xyz_mm']
                native=inverse@np.r_[point,1]
                if np.all(native[:3]>=0) and np.all(native[:3]<=[5,3,2]):
                    errors.append(abs(output[k,j,i]-(point@[1,2,3])))
            self.assertGreater(len(errors),10)
            self.assertLess(max(errors),.001)

    def test_qt_import_button_uses_new_project_adapter(self):
        from PyQt6.QtWidgets import QApplication
        from sud_yolo26.ui.main_window import KidneyMainWindow
        app=QApplication.instance() or QApplication([])
        window=KidneyMainWindow()
        with patch.object(window,'_select_directory',side_effect=['input','output']), patch('sud_yolo26.ui.main_window.gi.main') as importer:
            window.on_open_ct()
            importer.assert_called_once_with('input','output')
        window.close()

    def test_failed_export_preserves_status_without_complete_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);source=p/'input';source.mkdir();code,frames,report=self.fixture(source)
            def cancel(*args):raise InterruptedError('test cancellation')
            with self.assertRaises(InterruptedError):
                export_stack(frames,p/'out',code,ExportConfig('PTEST','E001','Synthetic'),report,cancel)
            statuses=list((p/'out').rglob('status.json'))
            self.assertEqual(len(statuses),1)
            self.assertEqual(json.loads(statuses[0].read_text())['status'],'failed')
            self.assertTrue(statuses[0].parent.name.endswith('.incomplete'))


if __name__=='__main__':unittest.main()
