"""Shared native VTK viewers. Inputs are XYZ; all coordinates are millimetres."""
import numpy as np
from scipy import ndimage as ndi

from sud_yolo26.application.paths import RESOURCES
from .stone_geometry import checked_spacing, validate_cube, surface_mesh, density_layers, cutaway


def voxel_surface(pv, cube, spacing_xyz, occluded=None):
    """Exact voxel faces, without smoothing, resampling or axis scaling."""
    points, triangles, intensity = surface_mesh(cube, spacing_xyz, occluded)
    faces = np.c_[np.full(len(triangles), 3), triangles]
    mesh = pv.PolyData(points, faces)
    mesh.cell_data['HU'] = intensity[triangles].mean(axis=1)
    return mesh


def _text(plotter, text, position='upper_left', size=14):
    plotter.add_text(text, position=position, font_size=size, color='#263849',
                     font_file=str(RESOURCES/'DejaVuSerifCondensed.ttf'))


def _scene(plotter, extent, title, transparent_layers=True):
    bounds = tuple(v for length in extent for v in (0., float(length)))
    plotter.set_background('#eef2f6')
    plotter.enable_parallel_projection()
    if transparent_layers:
        plotter.enable_depth_peeling(number_of_peels=100, occlusion_ratio=0)
    plotter.show_bounds(bounds=bounds, xtitle='X, mm', ytitle='Y, mm', ztitle='Z, mm',
                        color='#263849', font_size=11, fmt='%.1f', location='origin',
                        grid='back', n_xlabels=4, n_ylabels=4, n_zlabels=4)
    plotter.add_axes()
    _text(plotter, title)
    _text(plotter, 'Размер ROI: ' + ' × '.join(f'{v:.2f}' for v in extent) + ' мм',
          position='lower_left', size=11)
    # View the positive XYZ volume from its +X/-Y side, with Z upright.
    # The common zero of the three axes then sits at the lower left.
    center=np.asarray(extent,dtype=float)/2
    distance=max(float(np.linalg.norm(extent)),1.)
    plotter.camera_position=[tuple(center+distance*np.array([1.,-1.,.45])),
                             tuple(center),(0.,0.,1.)]
    plotter.reset_camera(bounds=bounds)
    plotter.camera.zoom(.8)  # reserve room for titles, ticks and controls


def compact_kidney_surface(pv, cube, spacing_xyz, max_faces=60000):
    """Display-only surface of HU >160, in mm; no per-voxel Python mesh.

    Binary Flying Edges keeps the strict HU mask and voxel-centre registration.
    Optional topology-preserving simplification affects rendering only.
    """
    cube = np.asarray(cube)
    spacing = checked_spacing(spacing_xyz)
    if cube.ndim != 3 or 0 in cube.shape or not np.isfinite(cube).all():
        raise ValueError('Нет корректного объёма почки')
    mask = cube > 160
    if not np.any(mask):
        raise ValueError('В области почки нет участков HU >160')
    padded = np.pad(mask.astype(np.uint8),1)
    grid = pv.ImageData(dimensions=padded.shape,spacing=tuple(spacing),
                        origin=tuple(-.5*spacing))
    grid.point_data['mask'] = padded.ravel(order='F')
    mesh = grid.contour([.5],scalars='mask',method='flying_edges',compute_scalars=False)
    original_faces = mesh.n_cells
    if original_faces > max_faces:
        mesh = mesh.decimate_pro(1-max_faces/original_faces, preserve_topology=True,
                                 splitting=False, boundary_vertex_deletion=False)
    mesh.field_data['original_triangles'] = np.array([original_faces])
    return mesh


def kidney_context_surface(pv,cube,spacing_xyz):
    """Lightweight approximate soft-tissue envelope within the kidney ROI.

    Smooth/downsample the display mask only, keeping sample locations in mm.
    Stone surfaces and the quantitative masks remain at the original resolution.
    """
    spacing=checked_spacing(spacing_xyz)
    # A 35 HU threshold cuts through ordinary unenhanced renal tissue.
    # Include lower-density soft tissue while excluding surrounding fat/air.
    # This is a display envelope, not an anatomical segmentation.
    mask=np.asarray(cube)>-30
    if not np.any(mask):
        raise ValueError('Нет данных для 3D визуализации почки')
    stride=np.maximum(1,np.ceil(1.5/spacing).astype(int))
    smooth=ndi.gaussian_filter(mask.astype(np.float32),sigma=1./spacing,mode='nearest')
    coarse=smooth[tuple(slice(None,None,int(s)) for s in stride)]
    # Preserve a very thin structure if smoothing would erase all its context.
    if coarse.max()<.5:
        coarse=mask.astype(np.float32)[tuple(slice(None,None,int(s)) for s in stride)]
    if not np.any(coarse):
        return compact_kidney_surface(pv,np.where(mask,161,0),spacing,max_faces=20000)
    step=spacing*stride
    # Reconstruct a coherent display envelope, rather than rendering every
    # disconnected threshold fragment. All operations are on this coarse copy.
    binary=coarse>=.5
    radius=3.
    reach=np.ceil(radius/step).astype(int)
    coordinates=np.meshgrid(*(np.arange(-n,n+1)*s for n,s in zip(reach,step)),indexing='ij')
    ball=sum(axis**2 for axis in coordinates)<=radius**2
    border=int(reach.max())+1
    closed=ndi.binary_closing(np.pad(binary,border),structure=ball)
    # Renal sinus and low-density internal areas should not become separate
    # inner sheets. Fill enclosed contours on each axial display section.
    for z in range(closed.shape[2]):
        closed[:,:,z]=ndi.binary_fill_holes(closed[:,:,z])
    closed=ndi.binary_fill_holes(closed)
    closed=closed[tuple(slice(border,-border) for _ in range(3))]
    labels,count=ndi.label(closed)
    if count:
        sizes=np.bincount(labels.ravel());sizes[0]=0
        closed=labels==sizes.argmax()
    else:
        return compact_kidney_surface(pv,np.where(mask,161,0),spacing,max_faces=20000)
    padded=np.pad(closed.astype(np.uint8),1)
    grid=pv.ImageData(dimensions=padded.shape,spacing=tuple(step),origin=tuple(.5*spacing-step))
    grid.point_data['mask']=padded.ravel(order='F')
    mesh=grid.contour([.5],scalars='mask',method='flying_edges',compute_scalars=False)
    if not mesh.n_cells:
        return compact_kidney_surface(pv,np.where(mask,161,0),spacing,max_faces=20000)
    if mesh.n_cells>20000:
        mesh=mesh.decimate_pro(1-20000/mesh.n_cells,preserve_topology=True,splitting=False,
                              boundary_vertex_deletion=False)
    extent=np.asarray(cube.shape)*spacing
    mesh.points=np.clip(mesh.points,0,extent)
    before=mesh.points.copy()
    boundary=(before<=0)|(before>=extent)
    mesh=mesh.smooth(n_iter=20,relaxation_factor=.1,boundary_smoothing=False)
    # Pin contacts with the cropped ROI; smoothing must not shrink these bounds.
    points=np.clip(mesh.points,0,extent)
    points[boundary]=before[boundary]
    mesh.points=points
    return mesh


def kidney_plotter(pv, cube, spacing_xyz, title='Почка — 3D', off_screen=False):
    """Light translucent kidney context plus full-resolution opaque HU >160 structures."""
    cube=np.asarray(cube)
    spacing = checked_spacing(spacing_xyz)
    if cube.ndim!=3 or 0 in cube.shape or not np.isfinite(cube).all():
        raise ValueError('Нет корректного объёма почки')
    context=kidney_context_surface(pv,cube,spacing)
    stones=compact_kidney_surface(pv,cube,spacing) if np.any(cube>160) else None
    plotter = pv.Plotter(window_size=(1200, 900), title=title, off_screen=off_screen)
    try:
        actor = plotter.add_mesh(context, color='#e79aa9', opacity=.35, smooth_shading=True,
            ambient=.35, diffuse=.7, specular=.2,culling='back',
            name='kidney')
        if stones is not None:
            plotter.add_mesh(stones,color='#f2a23a',opacity=1.,smooth_shading=True,
                ambient=.3,diffuse=.75,specular=.2,name='kidney-stones')
        # One translucent context around opaque stones needs only ordinary blending.
        # Keep expensive multi-pass depth peeling for the layered stone viewer alone.
        _scene(plotter, np.asarray(cube.shape)*spacing, title,transparent_layers=False)
        _text(plotter,'Розовый: почка · Оранжевый: HU >160',position='upper_right',size=11)
        plotter.add_slider_widget(lambda value: setattr(actor.prop, 'opacity', float(value)),
            (.1, .7), value=.35, title='Kidney opacity', pointa=(.58,.10), pointb=(.93,.10),
            color='#263849', interaction_event='always', fmt='%.2f')
        return plotter
    except Exception:
        plotter.close()
        raise


def stone_plotter(pv, cube, spacing_xyz, title='Камень — 3D', off_screen=False):
    """The same six density layers as the figures; synchronized full/cut views."""
    cube = validate_cube(cube)
    spacing = checked_spacing(spacing_xyz)
    extent = np.asarray(cube.shape)*spacing
    plotter = pv.Plotter(shape=(1,2), window_size=(1400, 850), title=title,
                         off_screen=off_screen)
    try:
        for column, (data, heading) in enumerate(((cube, 'Полный объём'),
                                                  (cutaway(cube), 'Срез 15% по каждой оси'))):
            plotter.subplot(0, column)
            for index, (band, opacity) in enumerate(density_layers(data, reference=cube)):
                mesh = voxel_surface(pv, band, spacing, occluded=data > band.max())
                plotter.add_mesh(mesh, scalars='HU', preference='cell', cmap='afmhot',
                    clim=(160,1350), opacity=opacity, lighting=False,
                    show_scalar_bar=column == 0 and index == 0,
                    scalar_bar_args=dict(title='HU', vertical=True, position_x=.86,
                                         position_y=.28, height=.42, width=.06),
                    name=f'layer-{index}')
            _scene(plotter, extent, heading)
        plotter.link_views()
        return plotter
    except Exception:
        plotter.close()
        raise
