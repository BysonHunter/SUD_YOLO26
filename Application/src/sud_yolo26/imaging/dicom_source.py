"""DICOM 3.x discovery and effective per-frame metadata; no GUI dependencies."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import copy
import hashlib
from pathlib import Path

import numpy as np
import pydicom


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


@dataclass
class Frame:
    path: Path
    index: int
    header: pydicom.Dataset
    source_sha256: str
    indexed: bool = False


@dataclass
class IndexedSeries:
    root: Path
    header: pydicom.Dataset
    references: dict

    def __len__(self):
        return len(self.references)


def read_directory(index_path, report):
    """Read the directory hierarchy only: no image opens, hashes or recursive scan."""
    root = index_path.parent
    index = pydicom.dcmread(index_path)
    records = list(getattr(index, 'DirectoryRecordSequence', []))
    offsets = {int(r.seq_item_tell): r for r in records}
    groups, visited = {}, set()
    def walk(offset, inherited):
        while offset:
            if offset in visited or offset not in offsets:
                report['rejected'].append(dict(path='DICOMDIR', reason='Invalid/cyclic directory record offset'))
                return
            visited.add(offset)
            record = offsets[offset]
            if int(getattr(record,'RecordInUseFlag',0xFFFF))==0:
                offset=int(getattr(record,'OffsetOfTheNextDirectoryRecord',0))
                continue
            metadata = dict(inherited)
            for key in ('PatientID','StudyDate','StudyInstanceUID','SeriesInstanceUID','SeriesNumber',
                        'SeriesDescription','Modality','ProtocolName','ContrastBolusAgent','ImageType'):
                if hasattr(record,key):metadata[key]=getattr(record,key)
            if hasattr(record,'ReferencedFileID'):
                parts = record.ReferencedFileID
                parts = str(parts).replace('\\','/').split('/') if isinstance(parts,str) else list(parts)
                if any(p in ('','..','.') or '/' in p or '\\' in p or ':' in p for p in parts):
                    report['unsafe_references'].append(str(parts))
                elif all(metadata.get(k) for k in ('StudyInstanceUID','SeriesInstanceUID')) and metadata.get('Modality')=='CT':
                    key = tuple(str(metadata.get(k,'')) for k in ('PatientID','StudyInstanceUID','SeriesInstanceUID'))
                    group = groups.setdefault(key,(metadata,{}))
                    group[1][root.joinpath(*parts)] = str(getattr(record,'ReferencedSOPInstanceUIDInFile',''))
            child = int(getattr(record,'OffsetOfReferencedLowerLevelDirectoryEntity',0))
            if child:walk(child,metadata)
            offset = int(getattr(record,'OffsetOfTheNextDirectoryRecord',0))
    walk(int(getattr(index,'OffsetOfTheFirstDirectoryRecordOfTheRootDirectoryEntity',0)),{})
    if not groups and not report['unsafe_references']:
        raise ValueError('DICOMDIR has no readable CT series hierarchy; image scan was not started.')
    stacks, identities = {}, {}
    for key,(metadata,references) in groups.items():
        digest=hashlib.sha256(repr(key).encode()).hexdigest();code='S'+digest[:16]
        if code in stacks:raise ValueError('Directory series identifier collision.')
        header=pydicom.Dataset()
        for name,value in metadata.items():setattr(header,name,value)
        stacks[code]=IndexedSeries(root,header,references);identities[code]=digest
    report.update(root=str(root),has_dicomdir=True,discovery_mode='dicomdir_only',
                  image_headers_read=0,instance_count=sum(len(s) for s in stacks.values()),
                  frame_count=None,stack_count=len(stacks),stack_identity_sha256=identities)
    return stacks,report


def load_indexed_series(series, report, progress=None):
    stacks, loaded = discover(series.root,progress,_paths=series.references,_referenced=series.references)
    for frames in stacks.values():
        for frame in frames:
            for key in ('PatientID','StudyDate','SeriesDescription','SeriesNumber'):
                if not getattr(frame.header,key,None) and hasattr(series.header,key):
                    setattr(frame.header,key,getattr(series.header,key))
            if any(str(getattr(frame.header,key,'')) != str(getattr(series.header,key,''))
                   for key in ('PatientID','StudyInstanceUID','SeriesInstanceUID')):
                loaded['index_conflicts'].append(str(frame.path.relative_to(series.root)))
    for key in ('missing_references','unsafe_references','index_conflicts','duplicate_copies','conflicts','skipped','rejected'):
        report[key].extend(loaded[key])
    report['stack_identity_sha256'].update(loaded['stack_identity_sha256'])
    report['image_headers_read']=report.get('image_headers_read',0)+loaded['image_headers_read']
    return stacks


def effective_header(ds, index):
    """Resolve Enhanced CT overrides (per-frame > shared > instance)."""
    result = pydicom.Dataset()
    result.file_meta = copy.deepcopy(ds.file_meta)
    for element in ds:
        if element.keyword not in ('SharedFunctionalGroupsSequence', 'PerFrameFunctionalGroupsSequence', 'PixelData'):
            result.add(copy.deepcopy(element))
    if int(getattr(ds, 'NumberOfFrames', 1)) > 1:
        shared = getattr(ds, 'SharedFunctionalGroupsSequence', [])
        per_frame = getattr(ds, 'PerFrameFunctionalGroupsSequence', [])
        if len(per_frame) != int(ds.NumberOfFrames):
            raise ValueError('Enhanced CT requires PerFrameFunctionalGroupsSequence for every frame.')
        for group in ([shared[0]] if shared else []) + [per_frame[index]]:
            for sequence in ('PlanePositionSequence', 'PlaneOrientationSequence',
                             'PixelMeasuresSequence', 'PixelValueTransformationSequence',
                             'FrameContentSequence', 'CTExposureSequence',
                             'CTImageFrameTypeSequence'):
                if hasattr(group, sequence):
                    for element in getattr(group, sequence)[0]:
                        result[element.tag] = element
    return result


def discover(input_path, progress=None, *, _paths=None, _referenced=None):
    """Inventory actual files and DICOMDIR references without trusting extensions."""
    source = Path(input_path).resolve()
    if source.is_file() and source.name.upper() != 'DICOMDIR':
        raise ValueError('A file input must be DICOMDIR; for images select their directory.')
    root = source.parent if source.is_file() else source
    if not root.is_dir():
        raise ValueError('DICOM input directory does not exist.')
    index_path = source if source.is_file() else root / 'DICOMDIR'
    report = dict(schema_version='1.0', missing_references=[], unsafe_references=[],
                  index_conflicts=[], duplicate_copies=[], conflicts=[], skipped=[], rejected=[],
                  image_headers_read=0,discovery_mode='selected_series' if _paths is not None else 'recursive_scan')
    if index_path.is_file() and _paths is None:
        return read_directory(index_path,report)
    referenced = _referenced or {}
    paths = sorted(_paths if _paths is not None else root.rglob('*'))
    instances = {}
    frames = []
    for number, path in enumerate(paths):
        if not path.is_file():
            if path in referenced:report['missing_references'].append(str(path.relative_to(root)))
            continue
        if path.name.upper() == 'DICOMDIR':
            continue
        path = path.resolve()
        if not path.is_relative_to(root):
            report['unsafe_references'].append(str(path))
            continue
        relative = str(path.relative_to(root))
        try:
            report['image_headers_read']+=1
            ds = pydicom.dcmread(path, stop_before_pixels=True)
        except (pydicom.errors.InvalidDicomError, OSError, ValueError) as exc:
            report['skipped'].append(dict(path=relative, reason=type(exc).__name__))
            continue
        if getattr(ds, 'Modality', '') != 'CT' or not hasattr(ds, 'Rows'):
            report['skipped'].append(dict(path=relative, reason='not a CT image'))
            continue
        if str(getattr(ds, 'SOPClassUID', '')) not in ('1.2.840.10008.5.1.4.1.1.2',
                '1.2.840.10008.5.1.4.1.1.2.1', '1.2.840.10008.5.1.4.1.1.2.2'):
            report['rejected'].append(dict(path=relative, reason='unsupported CT SOPClassUID'))
            continue
        required = ('StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID')
        if any(not getattr(ds, key, None) for key in required):
            report['rejected'].append(dict(path=relative, reason='missing identity UID'))
            continue
        uid = str(ds.SOPInstanceUID)
        digest = sha256(path)
        if path in referenced and referenced[path] and referenced[path] != uid:
            report['index_conflicts'].append(relative)
        if uid in instances:
            previous, old_digest = instances[uid]
            key = 'duplicate_copies' if digest == old_digest else 'conflicts'
            report[key].append(dict(first=previous, other=relative, sop_uid=uid))
            continue
        instances[uid] = (relative, digest)
        try:
            instance_frames = [Frame(path, i, effective_header(ds, i), digest, path in referenced)
                               for i in range(int(getattr(ds, 'NumberOfFrames', 1)))]
            frames.extend(instance_frames)
        except (ValueError, AttributeError, IndexError) as exc:
            report['rejected'].append(dict(path=relative, reason=str(exc)))
        if progress:
            progress(number + 1, len(paths), 'inventory')
    groups = defaultdict(list)
    for frame in frames:
        ds = frame.header
        # Spatial dimension indices are sorted geometrically; nonspatial dimensions
        # split stacks, including temporal phases and dual-energy reconstructions.
        dimensions = []
        values = getattr(ds, 'DimensionIndexValues', [])
        if isinstance(values, int):
            values = [values]
        for dimension, value in zip(getattr(ds, 'DimensionIndexSequence', []), values):
            if int(dimension.DimensionIndexPointer) not in (0x00209057, 0x00200032):
                dimensions.append(int(value))
        spatial_signature = (int(ds.Rows), int(ds.Columns),
                             tuple(round(float(x), 4) for x in getattr(ds, 'ImageOrientationPatient', [])),
                             tuple(round(float(x), 5) for x in getattr(ds, 'PixelSpacing', [])))
        key = (str(getattr(ds, 'PatientID', '')), str(ds.StudyInstanceUID),
               str(ds.SeriesInstanceUID), str(getattr(ds, 'FrameOfReferenceUID', '')),
               str(getattr(ds, 'TemporalPositionIndex', '')), str(getattr(ds, 'KVP', '')),
               str(getattr(ds, 'ConvolutionKernel', '')), str(getattr(ds, 'ImageType', '')),
               str(getattr(ds, 'FrameType', '')), tuple(dimensions), spatial_signature)
        groups[key].append(frame)
    stacks = {}
    for key, group in groups.items():
        code = 'S' + hashlib.sha256(repr(key).encode()).hexdigest()[:16]
        if code in stacks:
            raise ValueError('Stack identifier collision.')
        stacks[code] = group
    report.update(stack_identity_sha256={'S' + hashlib.sha256(repr(key).encode()).hexdigest()[:16]:
                                        hashlib.sha256(repr(key).encode()).hexdigest() for key in groups},
                  root=str(root), has_dicomdir=index_path.is_file(),
                  instance_count=len(instances), frame_count=len(frames), stack_count=len(stacks))
    return stacks, report


def assess(frames):
    ds = frames.header if isinstance(frames,IndexedSeries) else frames[0].header
    text = ' '.join(str(getattr(ds, k, '')) for k in
                    ('SeriesDescription', 'ProtocolName', 'ImageType', 'FrameType')).lower()
    excluded = any(word in text for word in ('localizer', 'scout', 'topogram', 'virtual', 'multi_energy', 'vnc'))
    native = any(word in text for word in ('native', 'noncontrast', 'non-contrast', 'non contrast', 'натив'))
    contrast = bool(str(getattr(ds,'ContrastBolusAgent','')).strip()) if isinstance(frames,IndexedSeries) else any(str(getattr(f.header, 'ContrastBolusAgent', '')).strip() for f in frames)
    # Only evidence hints: no header heuristic confirms clinical contrast status.
    return dict(description=str(getattr(ds, 'SeriesDescription', '')),
                series_number=str(getattr(ds, 'SeriesNumber', '')), frames=len(frames),
                contrast_status='CONFLICT' if native and contrast else 'UNKNOWN',
                native_name_hint=native, bolus_tag_present=contrast, excluded=excluded,
                reason='Special reconstruction/localizer' if excluded else 'Automatic selection from DICOM metadata')


def frame_pixels(frame):
    """Decode one frame via pydicom 3.x; determine padding before modality transform."""
    ds = frame.header
    try:
        raw = pydicom.pixels.pixel_array(frame.path, index=frame.index)
    except Exception as exc:
        syntax = str(ds.file_meta.TransferSyntaxUID)
        raise ValueError(f'Cannot decode {frame.path.name}, frame {frame.index}, TransferSyntaxUID={syntax}: {exc}') from exc
    if str(getattr(ds, 'PhotometricInterpretation', '')) not in ('MONOCHROME1', 'MONOCHROME2'):
        raise ValueError('Only monochrome CT images are supported.')
    valid = np.ones(raw.shape, dtype=bool)
    if hasattr(ds, 'PixelPaddingValue'):
        low = float(ds.PixelPaddingValue)
        high = float(getattr(ds, 'PixelPaddingRangeLimit', low))
        low, high = sorted((low, high))
        valid &= (raw < low) | (raw > high)
    if hasattr(ds, 'ModalityLUTSequence') or hasattr(ds, 'RealWorldValueMappingSequence'):
        raise ValueError('Modality LUT / real-world mapping needs a separately validated conversion.')
    units = str(getattr(ds, 'RescaleType', 'HU')).strip().upper()
    if units != 'HU':
        raise ValueError(f'Expected HU, found RescaleType={units!r}.')
    if not hasattr(ds, 'RescaleSlope') or not hasattr(ds, 'RescaleIntercept'):
        raise ValueError('Missing effective RescaleSlope/RescaleIntercept; cannot assert HU.')
    slope, intercept = float(ds.RescaleSlope), float(ds.RescaleIntercept)
    if not np.isfinite([slope, intercept]).all() or slope == 0:
        raise ValueError('Invalid HU rescale transform.')
    hu = raw.astype(np.float32) * slope + intercept
    valid &= np.isfinite(hu)
    return hu, valid
