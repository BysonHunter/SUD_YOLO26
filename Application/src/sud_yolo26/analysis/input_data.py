"""Resolve legacy and geometry-backed coronal analysis inputs without exporting copies."""
import json
from pathlib import Path
import numpy as np


def geometry_root(image_folder):
    folder=Path(image_folder).resolve()
    if folder.name=='detect':
        folder=folder.parent
    if folder.name=='coronal' and folder.parent.name=='images':
        root=folder.parent.parent
        if (root/'geometry/geometry.json').is_file():
            return root
    if folder.name=='axial' and folder.parent.name=='images':
        raise ValueError('Расчёт камней выполняется по корональным изображениям. Выберите images/coronal/detect.')
    return None


def load_geometry_input(image_folder):
    root=geometry_root(image_folder)
    if root is None:
        return None
    path=root/'geometry/geometry.json'
    geometry=json.loads(path.read_text(encoding='utf-8'))
    canonical=geometry['canonical_volume']
    if geometry.get('export',{}).get('coronal_rendering')!='shared_window_legacy_coronal_resize':
        raise ValueError('Неизвестная геометрия корональных изображений для расчёта камней')
    volume_path=(root/canonical['path']).resolve()
    if not volume_path.is_relative_to(root):
        raise ValueError('Путь HU-массива выходит за каталог серии')
    volume=np.load(volume_path,mmap_mode='r',allow_pickle=False)
    if list(volume.shape)!=canonical['shape_zyx']:
        raise ValueError('Размер HU-массива не соответствует geometry.json')
    spacing=np.asarray(canonical['spacing_xyz_mm'],dtype=float)
    if spacing.shape!=(3,) or not np.isfinite(spacing).all() or np.any(spacing<=0):
        raise ValueError('Некорректный физический шаг HU-массива')
    selection_path=root/'selection.json'
    selection=json.loads(selection_path.read_text(encoding='utf-8'))
    identity=selection['identity']
    frames=geometry.get('source_frames',[])
    frame=frames[0] if frames else {}
    nz,ny,nx=volume.shape
    sx,sy,sz=spacing
    # Existing analysis expects image rows to run superior -> inferior, exactly
    # as coronal_projection uses canonical[::-1, layer, :]. No HU resampling.
    values=volume[::-1,:,:]
    params=[identity['study_code'],identity.get('series_description',''),
            identity.get('patient_name',identity['patient_id']),identity['patient_id'],
            sz,identity.get('series_number',''),0,(nz-1)*sz,
            frame.get('slice_thickness_mm') or sz,ny,nx,1,sy,sx,0,1,
            str(tuple(volume.shape)),nz,ny,nx]
    files=[path,selection_path,volume_path]
    return dict(volume=values,params=list(map(str,params)),files=files,root=root)


def signature_files(image_folder):
    root=geometry_root(image_folder)
    if root is None:
        folder=Path(image_folder)
        return sorted(folder.glob('*.csv'))+sorted(folder.glob('*array.npy'))
    path=root/'geometry/geometry.json'
    data=json.loads(path.read_text(encoding='utf-8'))
    volume=(root/data['canonical_volume']['path']).resolve()
    if not volume.is_relative_to(root):
        raise ValueError('Путь HU-массива выходит за каталог серии')
    return [path,root/'selection.json',volume]
