"""Stone ROI assembly from all cluster boxes, retaining detection-supported components."""
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
from scipy import ndimage as ndi

ASSEMBLY_VERSION = 'cluster-roi-components-v3'


@dataclass
class AssembledStone:
    values_zyx: np.ndarray
    lower_xyz: np.ndarray
    upper_xyz: np.ndarray
    search_lower_xyz: np.ndarray
    search_upper_xyz: np.ndarray
    selected_components: int
    discarded_voxels: int
    search_boundary_touched: bool

    def summary(self):
        return dict(bounds_xyz=[self.lower_xyz.tolist(), self.upper_xyz.tolist()],
            search_bounds_xyz=[self.search_lower_xyz.tolist(), self.search_upper_xyz.tolist()],
            voxels=int(np.count_nonzero(self.values_zyx)), selected_components=self.selected_components,
            discarded_voxels=self.discarded_voxels, search_boundary_touched=self.search_boundary_touched)


def assemble_stone(volume_zyx, cluster, spacing_xyz, padding_mm=2.):
    """Search union + physical halo; select components seeded by this cluster's boxes.

    A connected pair remains a single object. No dilation, splitting, interpolation,
    or modification of the input HU array. Returned ROI is trimmed to occupied bounds.
    """
    volume = np.asarray(volume_zyx)
    spacing = np.asarray(spacing_xyz, dtype=float)
    if volume.ndim != 3 or 0 in volume.shape:
        raise ValueError('Нет корректного HU-объёма')
    if spacing.shape != (3,) or not np.isfinite(spacing).all() or (spacing<=0).any():
        raise ValueError('Размер вокселя должен быть положительным по трём осям')
    if not np.isfinite(padding_mm) or padding_mm < 0:
        raise ValueError('Запас области камня должен быть неотрицательным, в мм')
    nodes = [(int(y),box) for y,boxes in cluster.items() for box in boxes]
    if not nodes:
        raise ValueError('В кластере нет детекций камня')
    for y, box in nodes:
        if y<0 or y>=volume.shape[1] or not np.isfinite([box.min_x,box.max_x,box.min_z,box.max_z]).all():
            raise ValueError('Детекция камня выходит за диапазон слоёв или содержит неверные координаты')
        if box.w<=0 or box.h<=0:
            raise ValueError('Размер рамки камня должен быть положительным')
    lower = np.array([min(b.min_x for _,b in nodes)*volume.shape[2], min(y for y,_ in nodes),
                      min(b.min_z for _,b in nodes)*volume.shape[0]])
    upper = np.array([max(b.max_x for _,b in nodes)*volume.shape[2], max(y for y,_ in nodes)+1,
                      max(b.max_z for _,b in nodes)*volume.shape[0]])
    padding = np.ceil(padding_mm/spacing).astype(int)
    lo = np.maximum(0,np.floor(lower).astype(int)-padding)
    hi = np.minimum(volume.shape[::-1],np.ceil(upper).astype(int)+padding)
    if np.any(hi<=lo):
        raise ValueError('Область камня не пересекается с HU-массивом')
    x,y,z = lo; X,Y,Z = hi
    raw = volume[z:Z,y:Y,x:X]
    if not np.isfinite(raw).all():
        raise ValueError('Область камня содержит некорректные HU')
    positive = raw > 160
    labels,_ = ndi.label(positive, structure=np.ones((3,3,3)))
    seed = np.zeros(raw.shape,dtype=bool)
    for layer,box in nodes:
        # Use the same integer box support as the established detection parser.
        xb,xe = max(x,int(box.min_x*volume.shape[2])),min(X,int(box.max_x*volume.shape[2]))
        zb,ze = max(z,int(box.min_z*volume.shape[0])),min(Z,int(box.max_z*volume.shape[0]))
        if xe>xb and ze>zb:
            seed[zb-z:ze-z,layer-y:layer-y+1,xb-x:xe-x] = True
    ids = np.unique(labels[seed & positive])
    ids = ids[ids>0]
    if not len(ids):
        raise ValueError('В рамках детекций этого камня нет вокселей HU >160')
    selected = np.isin(labels,ids) & positive
    boundary = any(np.take(selected,k,axis=axis).any() for axis in range(3) for k in (0,-1))
    occupied = np.argwhere(selected)
    tight_lo, tight_hi = occupied.min(axis=0),occupied.max(axis=0)+1
    crop = tuple(slice(int(a),int(b)) for a,b in zip(tight_lo,tight_hi))
    values = np.where(selected[crop],raw[crop],0)
    return AssembledStone(values,lo+tight_lo[::-1],lo+tight_hi[::-1],lo,hi,
        len(ids),int(positive.sum()-selected.sum()),bool(boundary))


def assembly_conflicts(stones):
    """Return shared positive voxels between named assemblies, in global coordinates."""
    conflicts = []
    for index,(name,first) in enumerate(stones):
        for other,second in stones[index+1:]:
            lo = np.maximum(first.lower_xyz,second.lower_xyz)
            hi = np.minimum(first.upper_xyz,second.upper_xyz)
            if np.any(hi<=lo):
                continue
            def shared_view(stone):
                a,b = (lo-stone.lower_xyz)[::-1],(hi-stone.lower_xyz)[::-1]
                return stone.values_zyx[tuple(slice(int(x),int(y)) for x,y in zip(a,b))]>0
            count = int(np.count_nonzero(shared_view(first)&shared_view(second)))
            if count:
                conflicts.append(dict(first=name,second=other,shared_voxels=count))
    return conflicts


def consolidate_clusters(volume_zyx,clusters,spacing_xyz,**options):
    """Merge groups sharing actual HU voxels, then rebuild from all their boxes.

    This repairs gaps in detector slice coverage without joining merely nearby
    stones. Iterate because the merged search ROI may reveal further overlap.
    Call independently for each kidney; cross-kidney conflicts stay errors.
    """
    clusters=list(clusters)
    while True:
        assembled=[assemble_stone(volume_zyx,c,spacing_xyz,**options) for c in clusters]
        conflicts=assembly_conflicts(list(enumerate(assembled)))
        if not conflicts:
            return clusters,assembled
        parent=list(range(len(clusters)))
        def find(i):
            while parent[i]!=i:
                parent[i]=parent[parent[i]]
                i=parent[i]
            return i
        for overlap in conflicts:
            a,b=find(overlap['first']),find(overlap['second'])
            parent[b]=a
        merged={}
        for i,cluster in enumerate(clusters):
            group=merged.setdefault(find(i),{})
            for layer,boxes in cluster.items():
                group.setdefault(layer,[]).extend(boxes)
        clusters=list(merged.values())


def new_result_directory(case_path, output_dir=None):
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        output_dir = Path(case_path)/'stones'/'assembly_v2'/f'{stamp}_{uuid4().hex[:8]}'
    result = Path(output_dir)
    result.mkdir(parents=True,exist_ok=False)
    return result
