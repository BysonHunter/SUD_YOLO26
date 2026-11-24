"""Regular CT affine reconstruction and exact PNG/patient coordinate mappings."""
from itertools import product
import numpy as np
from scipy.ndimage import affine_transform
from .ordering import sort_frames


def native_geometry(frames, orientation_tolerance=1e-4, position_tolerance_mm=.01):
    if len(frames) < 2:
        raise ValueError('At least two spatially distinct CT frames are required.')
    first = frames[0].header
    if any(str(getattr(f.header, 'AnatomicalOrientationType', 'BIPED')) != 'BIPED' for f in frames):
        raise ValueError('Only BIPED patient coordinates are supported.')
    for key in ('StudyInstanceUID', 'SeriesInstanceUID', 'FrameOfReferenceUID'):
        if len({str(getattr(f.header, key, '')) for f in frames}) != 1:
            raise ValueError(f'Mixed {key} in one spatial stack.')
    iop = np.asarray(first.ImageOrientationPatient, dtype=float)
    spacing = np.asarray(first.PixelSpacing, dtype=float)
    if iop.shape != (6,) or spacing.shape != (2,) or not np.isfinite(np.r_[iop, spacing]).all() or np.any(spacing <= 0):
        raise ValueError('Invalid orientation or pixel spacing.')
    r, c = iop[:3], iop[3:]
    if not np.allclose([r @ r, c @ c, r @ c], [1, 1, 0], atol=orientation_tolerance, rtol=0):
        raise ValueError('IOP vectors must be orthonormal.')
    normal = np.cross(r, c)
    for f in frames:
        ds = f.header
        if (int(ds.Rows), int(ds.Columns)) != (int(first.Rows), int(first.Columns)):
            raise ValueError('Variable frame matrix needs separate reconstruction.')
        if not np.allclose(ds.ImageOrientationPatient, iop, atol=orientation_tolerance, rtol=0):
            raise ValueError('Variable orientation needs separate reconstruction.')
        if not np.allclose(ds.PixelSpacing, spacing, atol=orientation_tolerance, rtol=0):
            raise ValueError('Variable pixel spacing needs separate reconstruction.')
        if len(ds.ImagePositionPatient) != 3 or not np.isfinite(ds.ImagePositionPatient).all():
            raise ValueError('Invalid ImagePositionPatient.')
    frames,sorting = sort_frames(frames,normal,position_tolerance_mm)
    positions = np.array([f.header.ImagePositionPatient for f in frames], float)
    deltas = np.diff(positions, axis=0)
    gaps = deltas @ normal
    if not (np.all(gaps > position_tolerance_mm) or np.all(gaps < -position_tolerance_mm)):
        raise ValueError('Duplicate or indistinguishable slice positions; split phases/reconstructions.')
    delta = np.median(deltas, axis=0)
    sorting['normal_direction']='ascending' if np.median(gaps)>0 else 'descending'
    error = float(max(np.max(np.abs(deltas - delta)),
                      np.max(np.abs(positions - (positions[0] + np.arange(len(frames))[:, None] * delta)))))
    if error > position_tolerance_mm:
        raise ValueError(f'Irregular spacing / missing slices: deviation {error:.6g} mm.')
    affine = np.eye(4)
    affine[:3, :3] = np.column_stack((r * spacing[1], c * spacing[0], delta))
    affine[:3, 3] = positions[0]
    return frames, dict(shape_zyx=[len(frames), int(first.Rows), int(first.Columns)],
                        index_xyz_to_lps=affine.tolist(), regularity_error_mm=error,
                        normal_step_mm=float(abs(np.median(gaps))), sorting=sorting,
                        slice_thickness_mm=float(getattr(first, 'SliceThickness', abs(np.median(gaps)))),
                        units_assumed_hu=any(not hasattr(f.header, 'RescaleType') for f in frames))


def canonical_geometry(native, spacing_xyz=None):
    affine = np.array(native['index_xyz_to_lps'])
    shape_xyz = np.array(native['shape_zyx'][::-1])
    corners = np.array(list(product(*[(-.5, n - .5) for n in shape_xyz])))
    physical = corners @ affine[:3, :3].T + affine[:3, 3]
    low, high = physical.min(axis=0), physical.max(axis=0)
    spacing = np.linalg.norm(affine[:3, :3], axis=0) if spacing_xyz is None else np.array(spacing_xyz, float)
    if spacing.shape != (3,) or not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError('Target spacing must contain three positive finite values.')
    shape = np.ceil((high - low) / spacing - 1e-7).astype(int)
    target = np.eye(4)
    target[:3, :3] = np.diag(spacing)
    target[:3, 3] = low + spacing / 2
    return dict(shape_zyx=shape[::-1].tolist(), origin_lps_mm=target[:3, 3].tolist(),
                spacing_xyz_mm=spacing.tolist(), direction=np.eye(3).tolist(),
                index_xyz_to_lps=target.tolist(), support_bounds_lps_mm=[low.tolist(), high.tolist()])


def resample(native_hu, native_valid, native, canonical, out_hu, out_valid, progress=None):
    """One trilinear interpolation; validity requires all contributing samples valid.

    Half-voxel support uses nearest extension only inside the source voxel support.
    No interpolation across rejected irregular stacks is attempted.
    """
    transform = np.linalg.inv(native['index_xyz_to_lps']) @ np.array(canonical['index_xyz_to_lps'])
    matrix, offset = transform[:3, :3][::-1, ::-1], transform[:3, 3][::-1]
    rounded = np.rint(matrix).astype(int)
    permutation = np.argmax(np.abs(rounded), axis=0)
    copy_grid = (np.allclose(matrix, rounded, atol=1e-6)
                 and np.all(np.sum(np.abs(rounded), axis=0)==1)
                 and np.all(np.sum(np.abs(rounded), axis=1)==1))
    expected_offset = np.array([native_hu.shape[a]-1 if rounded[a].sum()<0 else 0 for a in range(3)])
    copy_grid = copy_grid and np.allclose(offset, expected_offset, atol=1e-6)
    oriented_hu, oriented_valid = native_hu, native_valid
    if copy_grid:
        oriented_hu, oriented_valid = np.transpose(native_hu, permutation), np.transpose(native_valid, permutation)
        for output_axis, input_axis in enumerate(permutation):
            if rounded[input_axis, output_axis] < 0:
                oriented_hu=np.flip(oriented_hu,output_axis)
                oriented_valid=np.flip(oriented_valid,output_axis)
        copy_grid = oriented_hu.shape == out_hu.shape
    identity = np.allclose(matrix,np.eye(3),atol=1e-6) and np.allclose(offset,0,atol=1e-6)
    # Aligned native grids, including axis permutations/flips, never interpolate HU.
    yy, xx = np.indices(out_hu.shape[1:], dtype=np.float64)
    for z in range(out_hu.shape[0]):
        if copy_grid:
            out_hu[z] = oriented_hu[z]
            out_valid[z] = oriented_valid[z]
        else:
            local_offset = offset + matrix[:, 0] * z
            sampling = np.column_stack((np.zeros(3), matrix[:, 1:]))
            out_hu[z] = affine_transform(native_hu, sampling, local_offset,
                                         output_shape=(1, *out_hu.shape[1:]), order=1,
                                         mode='nearest', prefilter=False)[0]
            weights = affine_transform(native_valid, sampling, local_offset,
                                       output_shape=(1, *out_hu.shape[1:]), output=np.float32,
                                       order=1, mode='nearest', prefilter=False)[0]
            inside = np.ones(xx.shape, bool)
            for axis, size in enumerate(native_hu.shape):
                coordinate = local_offset[axis] + matrix[axis, 1] * yy + matrix[axis, 2] * xx
                inside &= (coordinate >= -.5) & (coordinate <= size - .5)
            out_valid[z] = inside & (weights >= 1 - 1e-6)
            out_hu[z][~out_valid[z].astype(bool)] = -1024
        if progress:
            progress(z + 1, out_hu.shape[0], 'canonical LPS')
    out_hu.flush()
    out_valid.flush()
    return ('identity_copy' if identity else 'axis_permutation_flip_copy') if copy_grid else 'scipy.ndimage.affine_transform_linear'


def plane_geometry(canonical, plane, index):
    nz, ny, nx = canonical['shape_zyx']
    sx, sy, sz = canonical['spacing_xyz_mm']
    x, y, z = canonical['origin_lps_mm']
    if plane == 'AX' and 0 <= index < nz:
        origin, row, height, position = [x, y, z + index * sz], [0, sy, 0], ny, z + index * sz
    elif plane == 'COR' and 0 <= index < ny:
        origin, row, height, position = [x, y + index * sy, z + (nz - 1) * sz], [0, 0, -sz], nz, y + index * sy
    else:
        raise ValueError('Invalid plane or slice index.')
    return dict(plane=plane, slice_index=index, width=nx, height=height,
                pixel_center_origin_lps_mm=origin, column_step_lps_mm=[sx, 0, 0],
                row_step_lps_mm=row, physical_plane_position_mm=position,
                pixel_coordinate_convention='integer_centers_zero_based', resize_applied=False,
                canonical_shape_zyx=canonical['shape_zyx'],
                canonical_origin_lps_mm=canonical['origin_lps_mm'],
                canonical_spacing_xyz_mm=canonical['spacing_xyz_mm'])


def png_to_lps(sidecar, uv):
    uv = np.asarray(uv, float)
    return (np.asarray(sidecar['pixel_center_origin_lps_mm'])
            + uv[..., 0, None] * np.asarray(sidecar['column_step_lps_mm'])
            + uv[..., 1, None] * np.asarray(sidecar['row_step_lps_mm']))


def lps_to_volume_xyz(canonical, points):
    return (np.asarray(points) - canonical['origin_lps_mm']) / canonical['spacing_xyz_mm']


def yolo_box_to_lps(sidecar, box):
    """Four continuous corners, YOLO edge convention (first center is 0.5)."""
    cx, cy, w, h = np.asarray(box, float)
    if not np.isfinite([cx, cy, w, h]).all() or w < 0 or h < 0:
        raise ValueError('Invalid normalized bounding box.')
    corners = np.array([[cx-w/2, cy-h/2], [cx+w/2, cy-h/2],
                        [cx+w/2, cy+h/2], [cx-w/2, cy+h/2]])
    uv = corners * [sidecar['width'], sidecar['height']] - .5
    return png_to_lps(sidecar, uv)
