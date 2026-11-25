"""Lightweight study navigation, without reading DICOM pixels at startup."""
from dataclasses import dataclass
import json
from pathlib import Path
from sud_yolo26.application.files import natural_filename_key

IMAGE_SUFFIXES={'.png','.jpg','.jpeg','.bmp','.tif','.tiff'}


@dataclass(frozen=True)
class Study:
    root: Path
    coronal: Path
    axial: Path | None
    patient: str
    date: str
    series: str

    @property
    def detection(self):
        return self.coronal/'detect'

    @classmethod
    def open(cls,path):
        path=Path(path).resolve()
        if path.name=='detect':
            path=path.parent
        root=path
        if path.name in ('coronal','axial') and path.parent.name=='images':
            root=path.parent.parent
        if (root/'geometry/geometry.json').is_file():
            selection=json.loads((root/'selection.json').read_text(encoding='utf-8'))
            identity=selection['identity']
            return cls(root,root/'images/coronal',root/'images/axial',
                       str(identity['patient_id']),str(identity['study_code']),
                       str(identity.get('series_description') or root.name))
        if list(path.glob('*arrayinfo.csv')) and list(path.glob('*array.npy')):
            import csv
            with next(path.glob('*arrayinfo.csv')).open(encoding='utf-8-sig',newline='') as stream:
                row=next(csv.DictReader(stream))
            return cls(path,path,None,row['Patient ID'],row['Study Date'],row.get('Series Description',''))
        raise ValueError('Выберите каталог серии V001, images/coronal, detect или прежний каталог с CSV и HU-массивом.')


def find_studies(path):
    try:
        return [Study.open(path)]
    except ValueError:
        candidates=sorted(Path(path).rglob('selection.json'))
        studies=[]
        for file in candidates:
            if (file.parent/'geometry/geometry.json').is_file():
                studies.append(Study.open(file.parent))
        if not studies:
            raise ValueError('Готовые исследования не найдены. Откройте DICOM или выберите полный каталог экспорта.')
        return studies


def image_paths(folder):
    if folder is None or not Path(folder).is_dir():
        return []
    return sorted((p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES),key=natural_filename_key)


def layer_number(path):
    try:
        return int(Path(path).stem.rsplit('_',1)[-1])
    except ValueError:
        return None
