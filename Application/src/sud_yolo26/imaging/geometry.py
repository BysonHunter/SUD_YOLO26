"""Validated axial CT geometry. Oblique/enhanced CT requires a separate resampling stage."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import numpy as np
import pydicom
import cv2


def discover_series(folder):
    groups = defaultdict(list)
    for path in sorted(Path(folder).rglob('*')):
        if not path.is_file() or path.suffix.lower() in {'.png','.jpg','.npy','.csv','.txt','.zip','.ini'} or path.name.upper() == 'DICOMDIR':
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            if getattr(ds, 'Modality', '') == 'CT' and hasattr(ds, 'Rows') and hasattr(ds, 'SeriesInstanceUID'):
                groups[str(ds.SeriesInstanceUID)].append((path, ds))
        except (pydicom.errors.InvalidDicomError, OSError):
            continue
    return dict(groups)


def ordered_headers(headers):
    if len(headers) < 2:
        raise ValueError('Для объёмной реконструкции нужны минимум два DICOM-среза одной серии.')
    first = headers[0][1]
    if int(getattr(first, 'NumberOfFrames', 1)) != 1:
        raise ValueError('Enhanced/multiframe CT пока не поддерживается.')
    shape = (int(first.Rows), int(first.Columns))
    pixel_spacing = np.array(first.PixelSpacing, dtype=float)
    if not np.all(np.isfinite(pixel_spacing)) or np.any(pixel_spacing <= 0):
        raise ValueError('Некорректный PixelSpacing.')
    expected = np.array([1.,0.,0.,0.,1.,0.])
    for _, ds in headers:
        if (int(ds.Rows), int(ds.Columns)) != shape or str(ds.SeriesInstanceUID) != str(first.SeriesInstanceUID):
            raise ValueError('Смешаны разные серии или матрицы изображения.')
        if int(getattr(ds, 'NumberOfFrames', 1)) != 1:
            raise ValueError('Multiframe CT пока не поддерживается.')
        if not hasattr(ds, 'ImagePositionPatient') or not np.allclose(ds.ImageOrientationPatient, expected, atol=1e-4):
            raise ValueError('Поддерживается только регулярная аксиальная серия LPS без наклона/отражения; нужен отдельный resampling.')
        if not np.allclose(ds.PixelSpacing, pixel_spacing, atol=1e-5):
            raise ValueError('PixelSpacing меняется в пределах серии.')
        if not np.allclose(np.array(ds.ImagePositionPatient)[:2], np.array(first.ImagePositionPatient)[:2], atol=1e-3):
            raise ValueError('Сдвиг плоскостей/gantry tilt требует resampling.')
        if getattr(ds, 'PhotometricInterpretation', '') != 'MONOCHROME2':
            raise ValueError('Поддерживается только MONOCHROME2.')
    ordered = sorted(headers, key=lambda x: float(x[1].ImagePositionPatient[2]), reverse=True)
    z = np.array([float(ds.ImagePositionPatient[2]) for _, ds in ordered])
    gaps = -np.diff(z)
    if np.any(gaps <= 1e-5) or not np.allclose(gaps, np.median(gaps), atol=max(1e-3, np.median(gaps)*0.01)):
        raise ValueError('Повторные позиции, пропуски или нерегулярный шаг Z; объединять такие срезы нельзя.')
    return ordered, {'shape_zyx': [len(ordered), *shape], 'spacing_zyx_mm': [float(np.median(gaps)), *pixel_spacing.tolist()],
                     'origin_lps': [float(x) for x in ordered[0][1].ImagePositionPatient],
                     'z_order': 'superior_to_inferior', 'display_x': 'patient_right_to_left',
                     'source_slice_thickness_mm': float(getattr(first, 'SliceThickness', np.median(gaps)))}


def pixels_hu(ds):
    raw = ds.pixel_array.astype(np.float32)
    slope, intercept = float(getattr(ds, 'RescaleSlope', 1)), float(getattr(ds, 'RescaleIntercept', 0))
    if not np.isfinite(slope) or not np.isfinite(intercept) or slope == 0:
        raise ValueError('Некорректный RescaleSlope/Intercept.')
    return raw * slope + intercept


def read_volume(ordered, progress=None):
    volume = np.empty((len(ordered), int(ordered[0][1].Rows), int(ordered[0][1].Columns)), dtype=np.float32)
    for i, (path, _) in enumerate(ordered):
        volume[i] = pixels_hu(pydicom.dcmread(path))
        if progress:
            progress(i+1, len(ordered), path.name)
    return volume


def scalar(value):
    if isinstance(value, (list, tuple, pydicom.multival.MultiValue)):
        value = value[0]
    return float(value)


def render_coronal(coronal, spacing_zyx, center, width, mode='legacy'):
    center, width = scalar(center), scalar(width)
    if width <= 0:
        raise ValueError('WindowWidth должен быть положительным.')
    z_spacing, _, x_spacing = spacing_zyx
    size = (max(200, coronal.shape[1]), max(200, int((coronal.shape[0]-1)*z_spacing/x_spacing)))
    if mode == 'legacy':
        low, high = center-width/2, center+width/2
        mapped = (coronal-low)/(width/256)-1
        mapped = np.where(coronal < low, 0, np.where(coronal > high, 255, mapped)).astype(np.int16)
        image = cv2.resize(mapped, size, interpolation=cv2.INTER_CUBIC)
        return np.clip(image, 0, 255).astype(np.uint8)
    if mode != 'dicom' or width <= 1:
        raise ValueError('Для стандартного LINEAR window требуется mode=dicom и WindowWidth > 1.')
    mapped = np.clip(((coronal-(center-.5))/(width-1)+.5)*255, 0, 255)
    return cv2.resize(mapped.astype(np.uint8), size, interpolation=cv2.INTER_CUBIC)
