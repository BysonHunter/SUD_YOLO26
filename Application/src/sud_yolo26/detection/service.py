"""YOLO26 inference and atomic, versioned persistence of SUD-compatible results."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid

import cv2
import yaml

from sud_yolo26.application.paths import APP_ROOT, CONFIG_DIR
from .postprocess import CLASS_NAMES, Detection, accepted_detections


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def load_model(weights):
    path = Path(weights).resolve()
    if not path.is_file():
        raise FileNotFoundError(f'Установите обученные веса YOLO26: {path}. COCO-веса из Train_model/pretrained не подходят.')
    import os
    os.environ.setdefault('YOLO_CONFIG_DIR', str(APP_ROOT / 'data' / 'ultralytics'))
    from ultralytics import YOLO, settings
    settings.update({'sync': False, 'wandb': False, 'mlflow': False, 'comet': False, 'clearml': False})
    model = YOLO(str(path), task='detect')
    names = [model.names[i] for i in range(len(model.names))]
    if names != list(CLASS_NAMES):
        raise ValueError(f'Неверный порядок классов: {names}; нужны шесть классов комплекса SUD.')
    # YOLO26 uses the DFL-free head. Do not accept a six-class checkpoint of another family.
    head = model.model.model[-1]
    architecture = Path(str(model.model.yaml.get('yaml_file', ''))).stem
    if int(getattr(head, 'reg_max', -1)) != 1 or not architecture.startswith('yolo26'):
        raise ValueError('Требуется checkpoint YOLO26 с DFL-free end-to-end головой, а не YOLOv5/8/11.')
    return model


def detect_stones(detect_folder, save_conf=True, yolo_weights=None, *, model=None, progress=None):
    if not save_conf:
        raise ValueError('Для расчётов SUD обязательны шесть полей, включая confidence. Обучающие метки создаются отдельно.')
    if yolo_weights is None:
        from sud_yolo26.settings.service import read_settings
        yolo_weights = read_settings()[1]
    source = Path(detect_folder).resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)
    images = sorted(p for p in source.iterdir() if p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'})
    if not images:
        raise ValueError('В выбранном каталоге нет изображений.')
    cfg = yaml.safe_load((CONFIG_DIR/'detection.yaml').read_text(encoding='utf-8'))
    stone_confidence = cfg.pop('stone_confidence')
    model = model if model is not None else load_model(yolo_weights)
    stage = source / ('.detect-' + uuid.uuid4().hex)
    (stage/'labels').mkdir(parents=True)
    from_context = dict(weights=str(Path(yolo_weights).resolve()), weights_sha256=sha256(yolo_weights),
                        backend='ultralytics.YOLO26', image_count=len(images), options=cfg,
                        stone_confidence=stone_confidence, class_names=CLASS_NAMES,
                        quality_metrics='DP/OR/GOV/DOV not calculated; confidence is not DP')
    for index, path in enumerate(images, 1):
        result = model.predict(source=str(path), save=False, verbose=False, **cfg)[0]
        im = result.orig_img.copy()
        height, width = im.shape[:2]
        items = []
        for row in result.boxes.data.cpu().numpy():
            x1, y1, x2, y2, confidence, cls = row.tolist()
            x1, x2 = sorted((max(0, min(width, round(x1))), max(0, min(width, round(x2)))))
            y1, y2 = sorted((max(0, min(height, round(y1))), max(0, min(height, round(y2)))))
            if x2 <= x1 or y2 <= y1:
                continue
            items.append(Detection(int(cls), (x1+x2)/(2*width), (y1+y2)/(2*height),
                                   (x2-x1)/width, (y2-y1)/height, float(confidence)))
        rows = accepted_detections(items, stone_confidence)
        if rows:
            (stage/'labels'/f'{path.stem}.txt').write_text('\n'.join(d.label() for d in rows)+'\n', encoding='utf-8')
            for d in rows:
                box = (int((d.x-d.width/2)*width), int((d.y-d.height/2)*height),
                       int((d.x+d.width/2)*width), int((d.y+d.height/2)*height))
                cv2.rectangle(im, box[:2], box[2:], (0, 210, 255), 2)
                cv2.putText(im, f'{CLASS_NAMES[d.class_id]} {d.confidence:.2f}', box[:2], cv2.FONT_HERSHEY_SIMPLEX, .4, (0,210,255), 1)
            ok, encoded = cv2.imencode(path.suffix, im)
            if not ok:
                raise RuntimeError(f'Не удалось записать изображение {path.name}')
            encoded.tofile(stage/path.name)
        if progress:
            progress(index, len(images), path.name)
    from_context['label_count'] = len(list((stage/'labels').glob('*.txt')))
    from_context['completed_at'] = datetime.now().isoformat()
    (stage/'detection_manifest.json').write_text(json.dumps(from_context, ensure_ascii=False, indent=2), encoding='utf-8')
    final = source/'detect'
    if final.exists():
        final.rename(source/('detect.backup_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')))
    stage.rename(final)
    return final
