"""Explicit compatibility rules, independent of Ultralytics and Qt."""
from dataclasses import dataclass

CLASS_NAMES = ('left_kidney', 'stone', 'right_kidney', 'left_kidney_pieloectasy',
               'right_kidney_pieloectasy', 'staghorn_stones')


@dataclass(frozen=True)
class Detection:
    class_id: int
    x: float
    y: float
    width: float
    height: float
    confidence: float

    def label(self):
        return f'{self.class_id} {self.x:.7f} {self.y:.7f} {self.width:.7f} {self.height:.7f} {self.confidence:.7f}'


def accepted_detections(detections, stone_confidence=0.6):
    stones, kidneys = [], {}
    for d in sorted(detections, key=lambda value: value.confidence):
        if d.class_id in (1, 5):
            threshold=(stone_confidence.get(d.class_id,stone_confidence.get(str(d.class_id),0.6))
                       if isinstance(stone_confidence,dict) else stone_confidence)
            if d.confidence >= float(threshold):
                stones.append(d)
            continue
        if d.class_id in (0, 3):
            inside = 0.55 < d.x < 0.704613 and 0.248879 < d.y < 0.66704
        elif d.class_id in (2, 4):
            inside = 0.2 < d.x < 0.45 and 0.2 < d.y < 0.804613
        else:
            continue
        previous = kidneys.get(d.class_id)
        if inside and (previous is None or d.width*d.height > previous.width*previous.height):
            kidneys[d.class_id] = d
    return stones + [kidneys[k] for k in (0, 2, 3, 4) if k in kidneys]
