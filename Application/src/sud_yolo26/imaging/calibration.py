"""Resolve millimetres per PNG pixel; never infer millimetres from PNG DPI."""
from dataclasses import dataclass
from pathlib import Path
import csv
import hashlib
import json
import math
import re


@dataclass(frozen=True)
class ImageCalibration:
    x_mm: float
    y_mm: float
    source: str


def _positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid physical pixel spacing')
    return value


def from_sidecar(path, width, height, image=None):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if (int(data['width']), int(data['height'])) != (width, height):
        raise ValueError('PNG dimensions differ from its geometry')
    column, row = data['column_step_lps_mm'], data['row_step_lps_mm']
    if len(column) != 3 or len(row) != 3:
        raise ValueError('Expected three-component LPS vectors')
    sx = _positive(math.hypot(*column))
    sy = _positive(math.hypot(*row))
    if abs(sum(a*b for a, b in zip(column, row))) > 1e-5*sx*sy:
        raise ValueError('Nonorthogonal image axes require an affine viewer')
    if image is not None and data.get('image_path'):
        original = (Path(path).parent / data['image_path']).resolve()
        image = Path(image).resolve()
        if original != image and not (image.parent.name == 'detect'
                and image.parent.parent == original.parent and image.name == original.name):
            raise ValueError('Geometry refers to another image')
        if data.get('image_sha256') and original.is_file():
            if hashlib.sha256(original.read_bytes()).hexdigest() != data['image_sha256']:
                raise ValueError('Original PNG hash differs from its geometry')
    return ImageCalibration(sx, sy, str(path))


def legacy_calibration(image, width, height):
    """Only the known full-frame legacy coronal exporter, with verified output size."""
    match = re.fullmatch(r'([A-Za-z0-9]+)_\d{6}_\d+(?:_s)?', image.stem)
    if not match:
        return None
    folder = image.parent.parent if image.parent.name == 'detect' else image.parent
    candidates = list(folder.glob('*arrayinfo.csv'))
    if len(candidates) != 1:
        return None
    with candidates[0].open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1 or str(rows[0]['Patient ID']).strip() != match[1]:
        return None
    info = rows[0]
    columns, count = int(info['Columns']), int(info['Z'])
    source_rows = int(info['Rows'])
    # In the old CSV, X was DICOM PixelSpacing[0], Y was PixelSpacing[1].
    row_spacing = _positive(info['Pixel Spacing X'])
    column_spacing = _positive(info['Pixel Spacing Y'])
    span = abs(float(info['End Slice Location'])-float(info['Start Slice Location']))
    if count < 2 or not math.isfinite(span) or span <= 0:
        return None
    expected = (max(200, source_rows), max(200, int(source_rows*(span/(source_rows*row_spacing)))))
    if (width, height) != expected:
        return None
    return ImageCalibration(_positive(columns*column_spacing/width),
                            _positive(count*(span/(count-1))/height),
                            f'Legacy full-frame resize: {candidates[0]}')


def image_calibration(image, width, height):
    image = Path(image)
    candidates = [image.with_suffix('.json')]
    candidates += [p/'geometry'/'planes'/f'{image.stem}.json' for p in list(image.parents)[:5]]
    for path in candidates:
        if path.is_file():
            # Invalid geometry must not silently fall back to another source.
            return from_sidecar(path, width, height, image)
    return legacy_calibration(image, width, height)
