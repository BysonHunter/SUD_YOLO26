"""Install a trained six-class YOLO26 checkpoint; never substitute COCO weights."""
from pathlib import Path
import argparse
import json
import shutil
import sys

APP=Path(__file__).resolve().parent
sys.path.insert(0,str(APP/'src'))


def main():
    from sud_yolo26.detection.service import load_model, sha256
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('checkpoint',type=Path)
    args=p.parse_args()
    source=args.checkpoint.resolve()
    model=load_model(source)
    target=APP/'weights/kidney_yolo26.pt'
    if target.exists():
        raise SystemExit('An installed model already exists. Keep/rename it before installing another checkpoint.')
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)
    (target.parent/'model_manifest.json').write_text(json.dumps({'source':str(source),'sha256':sha256(target),
        'class_names':model.names,'architecture':model.model.yaml.get('yaml_file'),
        'quality_status':'No clinical validation or dissertation DP/OR/GOV/DOV calibration supplied'},
        ensure_ascii=False,indent=2),encoding='utf-8')
    print('INSTALLED',target)


if __name__=='__main__':main()
