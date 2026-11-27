"""Physical rendering of analysis arrays [coronal row, depth, column].

Saved analysis rows run superior -> inferior, unlike canonical LPS volume Z.
Rendering XYZ runs rightward on the image, into the image, then upward.
"""
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm, colors
from matplotlib.ticker import MaxNLocator
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import plotly.graph_objects as go


def checked_spacing(spacing_xyz):
    values = np.asarray(spacing_xyz, dtype=float)
    if values.shape != (3,) or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('Не указан корректный размер вокселя по трём осям (мм)')
    return values


def legacy_spacing_xyz(parameters):
    """Old CSV X/Y field names store DICOM row/column spacing, respectively."""
    sx, sy = float(parameters[13]), float(parameters[12])
    count = int(parameters[17])
    sz = np.nan
    try:
        span = abs(float(parameters[7])-float(parameters[6]))
        if count > 1 and np.isfinite(span) and span > 0:
            sz = span/(count-1)
    except (ValueError, TypeError):
        pass
    if not np.isfinite(sz) or sz <= 0:
        sz = abs(float(parameters[4]))  # centre-to-centre step, never SliceThickness
    return tuple(checked_spacing((sx, sy, sz)))


def cube_xyz(volume_zyx):
    """Analysis rows down -> display Z up; keep X and depth Y unchanged.

Accepts RK/LK/st_*.npy analysis arrays, NOT volume/hu_lps.npy (already Z up).
This view changes neither the saved HU nor any quantitative calculation.
"""
    return np.asarray(volume_zyx)[::-1, :, :].transpose(2, 1, 0)


def validate_cube(cube):
    cube = np.asarray(cube)
    if cube.ndim != 3 or 0 in cube.shape or not np.isfinite(cube).all():
        raise ValueError('Нет корректных объёмных данных для этого камня')
    if not np.any(cube > 0):
        raise ValueError('В сохранённом объёме нет вокселей камня')
    return cube


def voxel_edges(cube, spacing_xyz):
    spacing = checked_spacing(spacing_xyz)
    return np.meshgrid(*(np.arange(n+1)*s for n, s in zip(cube.shape, spacing)), indexing='ij')


def cutaway(cube):
    """Remove 15% from the high-index end of each axis, rounded to whole voxels."""
    return cube[tuple(slice(0, max(1, int(n*.85))) for n in cube.shape)]


def style_axes(ax, extent, elevation=0, azimuth=-90):
    ax.set(xlabel='X, мм', ylabel='Y, мм', zlabel='Z, мм',
           xlim=(0, extent[0]), ylim=(0, extent[1]), zlim=(0, extent[2]))
    # Matplotlib may normalize an ndarray in place; do not mutate the shared full extent.
    ax.set_box_aspect(tuple(float(v) for v in extent))
    ax.set_proj_type('ortho')
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_major_locator(MaxNLocator(nbins=4))
    ax.view_init(elevation, azimuth)


def density_layers(cube, reference=None, count=6):
    """Disjoint positive-value bands, with one opacity scale for both views.

    Yield (band volume, opacity). Zero remains background. A uniform stone
    stays opaque; transparency is relative to this stone, not a HU measurement.
    """
    cube = np.asarray(cube)
    reference = validate_cube(cube if reference is None else reference)
    positive = reference[reference > 0]
    lo, hi = float(positive.min()), float(positive.max())
    if hi == lo:
        if np.any(cube > 0):
            yield np.where(cube > 0, cube, 0), 1.
        return
    edges = np.linspace(lo, hi, count+1)
    for index in range(count):
        mask = (cube > 0) & (cube >= edges[index])
        mask &= cube <= hi if index == count-1 else cube < edges[index+1]
        if np.any(mask):
            yield np.where(mask, cube, 0), float(.25+.75*(index/(count-1))**1.25)


def draw_voxels(ax, cube, spacing_xyz, cmap, norm, extent=None, elevation=0, azimuth=-90,
                reference=None):
    cube = validate_cube(cube)
    spacing = checked_spacing(spacing_xyz)
    # Include internal band interfaces, then depth-sort all triangles together.
    # A transparent outer surface alone cannot reveal a core that was culled.
    faces, facecolors = [], []
    for band, opacity in density_layers(cube, reference):
        # Each density interface belongs to its denser side, once only.
        points, triangles, intensity = surface_mesh(band, spacing, occluded=cube > band.max())
        faces.append(points[triangles])
        rgba = cmap(norm(intensity[triangles].mean(axis=1)))
        rgba[:, 3] = opacity
        facecolors.append(rgba)
    if faces:
        ax.add_collection3d(Poly3DCollection(np.concatenate(faces),
            facecolors=np.concatenate(facecolors), edgecolors='none', zsort='average'))
    style_axes(ax, np.asarray(cube.shape)*spacing if extent is None else extent, elevation, azimuth)


def voxel_figure(cube, spacing_xyz, elevation=0, azimuth=-90, cmap=cm.afmhot, dpi=100):
    cube = validate_cube(cube)
    spacing = checked_spacing(spacing_xyz)
    extent = np.asarray(cube.shape)*spacing
    density = getattr(cmap, 'name', '') != 'afmhot'
    norm = colors.Normalize(1.7, 2.5, clip=True) if density else colors.Normalize(160, 1350, clip=True)
    fig = plt.figure(figsize=(10, 5), dpi=dpi, layout='constrained')
    axes = [fig.add_subplot(121, projection='3d'), fig.add_subplot(122, projection='3d')]
    for ax, data, title in zip(axes, (cube, cutaway(cube)),
                               ('Полный объём', 'Срез 15% по каждой оси')):
        if np.any(data > 0):
            draw_voxels(ax, data, spacing, cmap, norm, extent, elevation, azimuth, reference=cube)
        else:
            style_axes(ax, extent, elevation, azimuth)
        ax.set_title(title)
    fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=axes, shrink=.65,
                 label='Плотность, г/см³' if density else 'HU')
    return fig


def surface_mesh(cube, spacing_xyz, occluded=None):
    """Exposed voxel faces: exact cell boundaries, including single-layer stones."""
    cube = validate_cube(cube)
    spacing = checked_spacing(spacing_xyz)
    occupied = cube > 0
    neighbors = occupied if occluded is None else occupied | np.asarray(occluded, dtype=bool)
    offsets = [((0,0,0),(0,0,1),(0,1,1),(0,1,0)),
               ((1,0,0),(1,1,0),(1,1,1),(1,0,1)),
               ((0,0,0),(1,0,0),(1,0,1),(0,0,1)),
               ((0,1,0),(0,1,1),(1,1,1),(1,1,0)),
               ((0,0,0),(0,1,0),(1,1,0),(1,0,0)),
               ((0,0,1),(1,0,1),(1,1,1),(0,1,1))]
    vertices, intensities = [], []
    for axis in range(3):
        for direction in (-1, 1):
            neighbor = np.zeros_like(occupied)
            dest, source = [slice(None)]*3, [slice(None)]*3
            dest[axis] = slice(1, None) if direction == -1 else slice(None, -1)
            source[axis] = slice(None, -1) if direction == -1 else slice(1, None)
            neighbor[tuple(dest)] = neighbors[tuple(source)]
            cells = np.argwhere(occupied & ~neighbor)
            corners = np.asarray(offsets[2*axis+(direction == 1)])
            vertices.append(((cells[:, None, :]+corners)*spacing).reshape(-1, 3))
            intensities.append(np.repeat(cube[tuple(cells.T)], 4))
    points = np.concatenate(vertices)
    intensity = np.concatenate(intensities)
    base = np.arange(0, len(points), 4)
    triangles = np.concatenate([np.c_[base, base+1, base+2], np.c_[base, base+2, base+3]])
    return points, triangles, intensity


def interactive_figure(cube, spacing_xyz):
    cube = validate_cube(cube)
    spacing = checked_spacing(spacing_xyz)
    extent = np.asarray(cube.shape)*spacing
    fig = go.Figure()
    for band, opacity in density_layers(cube):
        points, triangles, intensity = surface_mesh(band, spacing, occluded=cube > band.max())
        fig.add_trace(go.Mesh3d(x=points[:,0], y=points[:,1], z=points[:,2],
            i=triangles[:,0], j=triangles[:,1], k=triangles[:,2], intensity=intensity,
            intensitymode='vertex', colorscale='Hot', cmin=160, cmax=1350,
            name=f'{intensity.min():.0f}–{intensity.max():.0f} HU · α={opacity:.2f}',
            showlegend=True, showscale=len(fig.data) == 0,
            flatshading=True, opacity=opacity, colorbar=dict(title='HU'),
            hovertemplate='X: %{x:.2f} мм<br>Y: %{y:.2f} мм<br>Z: %{z:.2f} мм<br>HU: %{intensity:.0f}<extra>%{fullData.name}</extra>',
            lighting=dict(ambient=.8, diffuse=.2, specular=0)))
    fig.update_layout(title='Воксельная модель камня',
        scene=dict(xaxis=dict(title='X, мм', range=[0, extent[0]]),
                   yaxis=dict(title='Y, мм', range=[0, extent[1]]),
                   zaxis=dict(title='Z, мм', range=[0, extent[2]]),
                   aspectmode='data', camera=dict(eye=dict(x=0, y=-2, z=0),
                       up=dict(x=0, y=0, z=1), projection=dict(type='orthographic'))))
    return fig


def projection_figure(volume_zyx, spacing_xyz, elevation=0, azimuth=-90, cmap=cm.afmhot):
    volume = validate_cube(volume_zyx)
    sx, sy, sz = checked_spacing(spacing_xyz)
    nz, ny, nx = volume.shape
    positive = np.where(volume > 0, volume, 0)
    # Choose the actual maximum-sum plane; no previous-iteration index offset.
    iz = int(np.argmax(positive.sum(axis=(1,2))))
    iy = int(np.argmax(positive.sum(axis=(0,2))))
    ix = int(np.argmax(positive.sum(axis=(0,1))))
    fig = plt.figure(figsize=(10, 9), layout='constrained')
    norm = colors.Normalize(160, 1350, clip=True)
    for position, data, extent, title, labels in [
        (221, volume[:,iy,:], (0,nx*sx,0,nz*sz), 'Корональная', ('X, мм','Z, мм')),
        (222, volume[:,:,ix], (0,ny*sy,0,nz*sz), 'Сагиттальная', ('Y, мм','Z, мм')),
        (223, volume[iz,:,:], (0,nx*sx,0,ny*sy), 'Аксиальная', ('X, мм','Y, мм'))]:
        ax = fig.add_subplot(position)
        im = ax.imshow(data, origin='upper', extent=extent, interpolation='nearest', cmap=cmap, norm=norm)
        ax.set(title=title, xlabel=labels[0], ylabel=labels[1])
        ax.set_aspect('equal')
        fig.colorbar(im, ax=ax, shrink=.7, label='HU')
    ax = fig.add_subplot(224, projection='3d')
    draw_voxels(ax, cube_xyz(volume), (sx,sy,sz), cmap, norm, elevation=elevation, azimuth=azimuth)
    ax.set_title('Воксельная модель')
    return fig
