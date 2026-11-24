"""Explicit legacy CT ordering, verified against physical frame positions."""
import numpy as np


def sort_frames(frames, normal, tolerance_mm=.01):
    """Keep the old InstanceNumber/SliceLocation rule without input-order dependence.

    A legacy candidate is accepted only when its physical positions are strictly
    monotonic. Otherwise ImagePositionPatient determines the order.
    """
    def numbers(keyword):
        try:
            values=np.array([float(getattr(f.header,keyword)) for f in frames])
            return values if np.isfinite(values).all() else None
        except (AttributeError,TypeError,ValueError):
            return None
    def physical_position(frame):
        return float(np.dot(normal,np.asarray(frame.header.ImagePositionPatient,float)))
    def monotonic(candidate):
        gaps=np.diff([physical_position(f) for f in candidate])
        return bool(np.all(gaps>tolerance_mm) or np.all(gaps < -tolerance_mm))
    locations,instances=numbers('SliceLocation'),numbers('InstanceNumber')
    reason='Legacy sorting tags absent or unusable.'
    if locations is not None:
        if instances is not None and len(np.unique(instances))==len(frames):
            candidate=sorted(frames,key=lambda f:float(f.header.InstanceNumber))
            if float(candidate[0].header.SliceLocation)<float(candidate[-1].header.SliceLocation) and monotonic(candidate):
                return candidate,dict(method='InstanceNumber_ascending',
                    reason='Legacy rule: SliceLocation increases with InstanceNumber; physical order verified.')
        if len(np.unique(locations))==len(frames):
            candidate=sorted(frames,key=lambda f:float(f.header.SliceLocation),reverse=True)
            if monotonic(candidate):
                return candidate,dict(method='SliceLocation_descending',
                    reason='Legacy fallback: decreasing SliceLocation; physical order verified.')
        reason='InstanceNumber/SliceLocation do not define a consistent physical order.'
    candidate=sorted(frames,key=physical_position)
    return candidate,dict(method='ImagePositionPatient_normal_ascending',reason=reason)
