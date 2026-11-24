"""Headless, versioned CT preparation according to the 2026-11-19 specification."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

import cv2
import numpy as np
import pydicom
import scipy

from .dicom_source import assess, frame_pixels, sha256
from .lps import native_geometry, canonical_geometry, resample, plane_geometry, png_to_lps, lps_to_volume_xyz
from .projections import acquisition_date, image_name, coronal_indices, coronal_projection, window_png, select_window, apply_window
from .selection import dicom_identity


@dataclass(frozen=True)
class ExportConfig:
    # Legacy config fields accepted for compatibility; DICOM identity always wins.
    patient_code: str = ''
    study_code: str = ''
    confirmation_reason: str = ''
    contrast_status: str = 'UNKNOWN'
    spacing_xyz_mm: tuple | None = None
    window_low_hu: float | None = None
    window_high_hu: float | None = None
    window_code: str = 'WST'
    axial_step: int = 1
    coronal_step: int = 1
    orientation_tolerance: float = 1e-4
    position_tolerance_mm: float = .01
    max_voxels: int = 600_000_000
    approve_unindexed: bool = False
    allow_burned_in: bool = False
    compatibility: bool = False  # Deprecated key accepted; no compatibility export.
    compatibility_image_count: int | None = None  # Older config alias; no separate export.
    coronal_image_count: int = 200


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def hash_json(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()




def write_png(path, image):
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise OSError(f'PNG encoding failed: {path}')
    Path(path).write_bytes(encoded.tobytes())




def validate_config(config):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,47}', config.window_code):
        raise ValueError('window_code must start with a Latin letter and contain only letters, digits or hyphens.')
    if min(config.axial_step, config.coronal_step) < 1:
        raise ValueError('Plane steps must be positive integers.')
    if config.orientation_tolerance <= 0 or config.position_tolerance_mm <= 0:
        raise ValueError('Geometry tolerances must be positive.')
    select_window(pydicom.Dataset(),config.window_low_hu,config.window_high_hu,config.window_code)


def export_stack(frames, output_root, stack_code, config, inventory, progress=None):
    identity = dicom_identity(frames)
    candidate = assess(frames)
    config = replace(config, patient_code=identity['patient_code'], study_code=identity['study_code'],
                     contrast_status=candidate['contrast_status'], confirmation_reason='Automatic selection from DICOM headers; no confirmation requested.')
    validate_config(config)
    for previous in Path(output_root).glob('*/*/V*/export_config.json'):
        stored = json.loads(previous.read_text(encoding='utf-8'))
        if stored['window_code'] == config.window_code and stored['window_low_hu'] is not None and config.window_low_hu is not None and (stored['window_low_hu'], stored['window_high_hu']) != (config.window_low_hu, config.window_high_hu):
            raise ValueError('Window code already has different HU bounds in this dataset; use a new code.')
    if not re.fullmatch(r'S[0-9a-f]{16}', stack_code):
        raise ValueError('Invalid stack code; use a discovered inventory key.')
    if inventory['conflicts'] or inventory['index_conflicts'] or inventory['unsafe_references']:
        raise ValueError('Resolve conflicting instances / unsafe DICOMDIR references before export.')
    if inventory['has_dicomdir'] and (inventory['missing_references'] or any(not f.indexed for f in frames)) and not config.approve_unindexed:
        raise ValueError('Incomplete DICOMDIR: review inventory and explicitly approve reconciliation.')
    candidate = assess(frames)
    if candidate['excluded']:
        raise ValueError(candidate['reason'])
    if any(str(getattr(f.header, 'BurnedInAnnotation', 'NO')).upper() == 'YES' for f in frames) and not config.allow_burned_in:
        raise ValueError('BurnedInAnnotation=YES: review/de-identify images before export.')
    ordered, native = native_geometry(frames, config.orientation_tolerance, config.position_tolerance_mm)
    shared_window=select_window(ordered[0].header,config.window_low_hu,config.window_high_hu,config.window_code)
    canonical = canonical_geometry(native, config.spacing_xyz_mm)
    if max(int(np.prod(native['shape_zyx'])), int(np.prod(canonical['shape_zyx']))) > config.max_voxels:
        raise ValueError('Volume exceeds configured voxel limit; review target grid and disk capacity.')
    parent = Path(output_root) / config.patient_code / config.study_code
    parent.mkdir(parents=True, exist_ok=True)
    for previous in parent.glob('V*/provenance/inventory.json'):
        stored = json.loads(previous.read_text(encoding='utf-8'))
        old_hash = stored.get('stack_identity_sha256', {}).get(stack_code)
        new_hash = inventory.get('stack_identity_sha256', {}).get(stack_code)
        if old_hash and new_hash and old_hash != new_hash:
            raise ValueError('Stack identifier collision: select a distinct output root.')
    estimate = 5*int(np.prod(native['shape_zyx'])) + 5*int(np.prod(canonical['shape_zyx']))
    if shutil.disk_usage(parent).free < estimate + 256*1024**2:
        raise OSError(f'Insufficient free space: arrays need approximately {estimate/1024**3:.2f} GiB plus PNGs.')
    version = 1
    while (parent / f'V{version:03}').exists() or (parent / f'.V{version:03}.incomplete').exists():
        version += 1
    final = parent / f'V{version:03}'
    staging = parent / f'.V{version:03}.incomplete'
    staging.mkdir()
    arrays = []
    try:
        for name in ('volume', 'geometry/planes', 'images/axial', 'images/coronal', 'provenance'):
            (staging / name).mkdir(parents=True)
        write_json(staging / 'status.json', {'status': 'incomplete'})
        write_json(staging / 'export_config.json', asdict(config))
        write_json(staging / 'window.json',shared_window)
        selection = dict(candidate, contrast_status=config.contrast_status,
                         selection_method='DICOM metadata; automatic native preference, otherwise all suitable stacks',
                         identity=identity, stack_code=stack_code)
        write_json(staging / 'selection.json', selection)
        # This local-only file deliberately contains source UIDs/paths and is not an anonymized dataset.
        write_json(staging / 'provenance/inventory.json', inventory)
        source_frames = []
        for i, f in enumerate(ordered):
            ds = f.header
            source_frames.append(dict(project_frame_id=f'F{i:06}', path=str(f.path),
                sop_instance_uid=str(ds.SOPInstanceUID), study_uid=str(ds.StudyInstanceUID),
                series_uid=str(ds.SeriesInstanceUID), frame_of_reference_uid=str(getattr(ds, 'FrameOfReferenceUID', '')),
                frame_index_zero_based=f.index, native_layer_index=i,
                instance_number=str(getattr(ds,'InstanceNumber','')),
                slice_location=str(getattr(ds,'SliceLocation','')),source_sha256=f.source_sha256))
        write_json(staging / 'provenance/source_frames.json', source_frames)
        def allocate(name, dtype, shape):
            result = np.lib.format.open_memmap(staging / 'volume' / name, mode='w+', dtype=dtype, shape=tuple(shape))
            arrays.append(result)
            return result
        hu = allocate('hu_native.npy', np.float32, native['shape_zyx'])
        valid = allocate('valid_native.npy', np.uint8, native['shape_zyx'])
        frame_geometry = []
        for i, frame in enumerate(ordered):
            hu[i], valid[i] = frame_pixels(frame)
            ds = frame.header
            frame_geometry.append(dict(project_frame_id=f'F{i:06}', frame_index_zero_based=frame.index,
                rows=int(ds.Rows), columns=int(ds.Columns), pixel_spacing_mm=list(map(float, ds.PixelSpacing)),
                iop=list(map(float, ds.ImageOrientationPatient)), ipp_lps_mm=list(map(float, ds.ImagePositionPatient)),
                instance_number=str(getattr(ds,'InstanceNumber','')),slice_location=str(getattr(ds,'SliceLocation','')),
                slice_thickness_mm=float(getattr(ds, 'SliceThickness', native['normal_step_mm'])),
                rescale_slope=float(ds.RescaleSlope), rescale_intercept=float(ds.RescaleIntercept),
                rescale_type=str(getattr(ds, 'RescaleType', 'HU_ASSUMED_CLASSIC_CT')),
                padding_value=float(ds.PixelPaddingValue) if hasattr(ds, 'PixelPaddingValue') else None,
                padding_range_limit=float(ds.PixelPaddingRangeLimit) if hasattr(ds, 'PixelPaddingRangeLimit') else None,
                valid_voxels=int(valid[i].sum())))
            if progress:
                progress(i+1, len(ordered), 'decode HU')
        hu.flush(); valid.flush()
        target = allocate('hu_lps.npy', np.float32, canonical['shape_zyx'])
        mask = allocate('valid_lps.npy', np.uint8, canonical['shape_zyx'])
        method = resample(hu, valid, native, canonical, target, mask, progress)
        native.update(path='volume/hu_native.npy', valid_path='volume/valid_native.npy', axis_order='kji', representation='regular_affine')
        canonical.update(path='volume/hu_lps.npy', valid_path='volume/valid_lps.npy', axis_order='zyx')
        # Hash only geometric/transform content, independent of folder/export version.
        geometry = dict(schema_version='1.0', volume_path_base='export_root',
                        selection={'path_relative_to_geometry_json':'../selection.json'},
                        export={'config_path_relative_to_geometry_json':'../export_config.json',
                                'planes':['AX','COR'], 'coronal_rendering':'shared_window_legacy_coronal_resize',
                                'coronal_resize_applied':True},
                        source_frames=frame_geometry, native_volume=native,
                        canonical_volume=canonical, resampling=dict(method=method, scipy_version=scipy.__version__,
                        interpolation_order=0 if method.endswith('copy') else 1, fill_hu=-1024,
                        validity='all trilinear contributors valid; within half-voxel source support',
                        reversible_values=method.endswith('copy'), orientation_tolerance=config.orientation_tolerance,
                        position_tolerance_mm=config.position_tolerance_mm))
        digest = hash_json(geometry)
        prefix_length = 16
        existing_ids = {}
        for previous in Path(output_root).glob('*/*/V*/geometry/geometry.json'):
            identity = json.loads(previous.read_text(encoding='utf-8'))['identity']
            existing_ids[identity['geometry_id']] = identity['geometry_sha256']
        while 'G'+digest[:prefix_length] in existing_ids and existing_ids['G'+digest[:prefix_length]] != digest:
            prefix_length += 4
        geometry_id = 'G' + digest[:prefix_length]
        geometry['identity'] = dict(patient_code=config.patient_code, study_code=config.study_code,
                                    series_code=stack_code, geometry_id=geometry_id, geometry_sha256=digest,
                                    export_version=f'V{version:03}')
        write_json(staging / 'geometry/geometry.json', geometry)
        geometry_file_hash = sha256(staging / 'geometry/geometry.json')
        rows = []
        max_error = 0.0
        canonical_to_native=np.linalg.inv(native['index_xyz_to_lps']) @ np.asarray(canonical['index_xyz_to_lps'])
        native_to_canonical=np.linalg.inv(canonical_to_native)
        axial_uses_native_order=(method.endswith('copy') and target.shape[0]==len(ordered)
            and np.allclose(canonical_to_native[2,:2],0,atol=1e-6)
            and np.isclose(abs(canonical_to_native[2,2]),1,atol=1e-6))
        cor_count=config.compatibility_image_count if config.compatibility_image_count is not None else config.coronal_image_count
        for plane, indices, subdir in [('AX',range(0,target.shape[0],config.axial_step),'axial'),
                ('COR',coronal_indices(target.shape,cor_count,config.coronal_step),'coronal')]:
            for index in indices:
                canonical_index=index
                if plane=='AX' and axial_uses_native_order:
                    canonical_index=int(round((native_to_canonical @ [0,0,index,1])[2]))
                header=ordered[0].header
                if plane=='AX' and method.endswith('copy') and np.allclose(canonical_to_native[2,:2],0,atol=1e-6):
                    native_index=int(round((canonical_to_native @ [0,0,canonical_index,1])[2]))
                    if 0<=native_index<len(ordered):header=ordered[native_index].header
                date=acquisition_date(header)
                if plane=='COR':
                    image,sidecar=coronal_projection(target,canonical,header,index,mask,
                        window=shared_window)
                else:
                    sidecar=plane_geometry(canonical,plane,canonical_index)
                    image=apply_window(target[canonical_index],mask[canonical_index],shared_window)
                    sidecar.update(shared_window,invalid_pixel_count=int(np.count_nonzero(mask[canonical_index]==0)),
                        slice_index=index,canonical_slice_index=canonical_index,
                        native_layer_index=index if axial_uses_native_order else None,
                        layer_index_space='sorted_native_axis_k' if axial_uses_native_order else 'canonical_axis_z')
                position = sidecar['physical_plane_position_mm']
                name = image_name(config.patient_code,date,plane,index)
                semantic_name = name
                image_path = f'images/{subdir}/{name}.png'
                sidecar_path = f'geometry/planes/{name}.json'
                write_png(staging / image_path,image)
                image_hash = sha256(staging / image_path)
                sidecar.update(schema_version='1.0', semantic_image_id=semantic_name,
                    geometry_id=geometry_id, geometry_sha256=digest,
                    geometry_file_sha256=geometry_file_hash, geometry_path='../geometry.json',
                    patient_code=config.patient_code, study_code=config.study_code, series_code=stack_code,
                    image_path=f'../../{image_path}', image_sha256=image_hash, acquisition_date=date,
                    background=0, valid_volume_path='../../volume/valid_lps.npy')
                write_json(staging / sidecar_path, sidecar)
                uv = np.array([[0, 0], [sidecar['width']-1, sidecar['height']-1]], float)
                xyz = lps_to_volume_xyz(canonical, png_to_lps(sidecar, uv))
                source_uv=np.c_[uv,np.ones(2)] @ np.array(sidecar.get('png_to_plane_pixel_centers',np.eye(3))).T
                expected = np.column_stack((source_uv[:,0],source_uv[:,1],np.full(2,canonical_index))) if plane=='AX' else np.column_stack((source_uv[:,0],np.full(2,index),target.shape[0]-1-source_uv[:,1]))
                max_error = max(max_error, float(np.abs(xyz-expected).max()))
                rows.append(dict(image_id=name, image_path=image_path, sidecar_path=sidecar_path,
                    patient_code=config.patient_code, study_code=config.study_code, series_code=stack_code,
                    geometry_id=geometry_id, plane=plane, slice_index=index,
                    canonical_slice_index=canonical_index if plane=='AX' else '',window_profile=sidecar['window_profile'],
                    width=sidecar['width'], height=sidecar['height'], physical_plane_position_mm=position,
                    image_sha256=image_hash, acquisition_date=date, qc_status='complete', annotation_version='', split=''))
                if progress:
                    progress(index+1, target.shape[0 if plane=='AX' else 1], f'PNG {plane}')
        if max_error > 1e-6:
            raise ValueError('PNG geometry round-trip failed.')
        with (staging / 'manifest.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0])
            writer.writeheader(); writer.writerows(rows)
        write_json(staging / 'qc_report.json', dict(status='complete', image_count=len(rows),
            png_round_trip_max_error_voxels=max_error, native=native, canonical=canonical,
            invalid_canonical_voxels=int(np.count_nonzero(mask==0)),
            missing_units_assumed_hu=native['units_assumed_hu'],
            burned_in_annotation_review='operator approval' if config.allow_burned_in else 'YES rejected; absent/NO is not proof of anonymization',
            pydicom_version=pydicom.__version__, numpy_version=np.__version__, scipy_version=scipy.__version__,
            python_version=sys.version, privacy='Local provenance contains UIDs/paths. No automatic de-identification or clinical validation.'))
        for array in arrays:
            array.flush(); array._mmap.close()
        arrays.clear()
        write_json(staging / 'status.json', dict(status='complete', geometry_id=geometry_id))
        files = sorted(p for p in staging.rglob('*') if p.is_file())
        sums = [(sha256(p), p.relative_to(staging).as_posix()) for p in files]
        (staging / 'checksums.sha256').write_text(''.join(f'{h}  {p}\n' for h,p in sums), encoding='utf-8')
        for digest, path in sums:
            if sha256(staging / path) != digest:
                raise OSError(f'Checksum verification failed: {path}')
        staging.rename(final)
        return final
    except BaseException as exc:
        for array in arrays:
            try:
                array.flush(); array._mmap.close()
            except (OSError, ValueError):
                pass
        write_json(staging / 'status.json', dict(status='failed', error=type(exc).__name__, message=str(exc)))
        raise
