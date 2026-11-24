"""Automatic identity and stack selection from actual DICOM headers."""
import hashlib
import re

from .dicom_source import assess, IndexedSeries, load_indexed_series
from .lps import native_geometry


def path_component(value, fallback):
    original = str(value).strip()
    if not original:
        return fallback
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in original).strip('. ')
    reserved = safe.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
    if not safe or reserved or len(safe)>48 or safe != original:
        safe = (safe[:32] or fallback) + '-' + hashlib.sha256(original.encode()).hexdigest()[:12]
    return safe


def dicom_identity(frames):
    first = frames[0].header
    patient_id = str(getattr(first, 'PatientID', '')).strip()
    study_uid = str(first.StudyInstanceUID)
    fallback = 'UNKNOWN-' + hashlib.sha256(study_uid.encode()).hexdigest()[:12]
    study_date = re.sub(r'[^0-9]', '', str(getattr(first, 'StudyDate', ''))) or 'Study'
    study = study_date
    return dict(patient_code=path_component(patient_id, fallback), study_code=study,
                patient_id=patient_id, study_instance_uid=study_uid,
                series_instance_uid=str(first.SeriesInstanceUID),
                series_number=str(getattr(first, 'SeriesNumber', '')),
                series_description=str(getattr(first, 'SeriesDescription', '')),
                identity_source='DICOM PatientID / StudyInstanceUID / SeriesInstanceUID')


def select_stacks(stacks):
    """Prefer native-labelled stacks per patient/study; otherwise keep all suitable.

    Never silently pick one of equally eligible reconstructions or combine phases.
    Header hints are recorded, without claiming clinically confirmed contrast status.
    """
    groups = {}
    for code, frames in stacks.items():
        status = assess(frames)
        if status['excluded']:
            continue
        geometry = None
        if not isinstance(frames,IndexedSeries):
            try:
                _, geometry = native_geometry(frames)
            except (ValueError, AttributeError, TypeError):
                continue
        ds = frames.header if isinstance(frames,IndexedSeries) else frames[0].header
        group = (str(getattr(ds, 'PatientID', '')), str(ds.StudyInstanceUID))
        groups.setdefault(group, []).append((code, frames, geometry, status))
    selected = []
    for candidates in groups.values():
        native = [c for c in candidates if c[3]['native_name_hint'] and not c[3]['bolus_tag_present']]
        selected.extend(native or candidates)
    return selected


def prepare_stacks(stacks, report, progress=None, explicit_code=None):
    candidates = [(explicit_code,stacks[explicit_code],None,assess(stacks[explicit_code]))] if explicit_code else select_stacks(stacks)
    result=[]
    for code,frames,geometry,status in candidates:
        if isinstance(frames,IndexedSeries):
            loaded=load_indexed_series(frames,report,progress)
            result.extend(select_stacks(loaded))
        elif geometry is None:
            _,geometry=native_geometry(frames)
            result.append((code,frames,geometry,status))
        else:result.append((code,frames,geometry,status))
    return result
