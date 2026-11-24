"""Portable command line entry point for CT inventory and image export."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from sud_yolo26.imaging.dicom_source import discover, assess, IndexedSeries
from sud_yolo26.imaging.lps import native_geometry
from sud_yolo26.imaging.pipeline import ExportConfig, export_stack, write_json
from sud_yolo26.imaging.selection import prepare_stacks


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='DICOM CT -> HU / LPS / axe PNG and legacy-rendered cor PNG')
    parser.add_argument('input', type=Path, help='DICOM directory or DICOMDIR')
    parser.add_argument('--inventory', type=Path, required=True, help='Local inventory JSON (contains source paths/UIDs)')
    parser.add_argument('--config', type=Path, help='Optional export parameters JSON; identity is read from DICOM')
    parser.add_argument('--stack', help='Optional explicit stack code; otherwise select automatically')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stacks, report = discover(args.input)
    summaries = {}
    for code, frames in stacks.items():
        item = assess(frames)
        if isinstance(frames,IndexedSeries):
            item['geometry_status']='deferred_until_selected'
            item['source']='DICOMDIR'
            summaries[code]=item
            continue
        try:
            _, item['native_geometry'] = native_geometry(frames)
            item['geometry_status'] = 'regular_affine'
        except (ValueError, AttributeError, TypeError) as exc:
            item['geometry_status'] = 'rejected'
            item['geometry_error'] = str(exc)
        summaries[code] = item
    args.inventory.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.inventory, dict(report, candidates=summaries))
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    if args.config and args.output is None:
        parser.error('--config requires --output')
    if args.output:
        if args.stack and args.stack not in stacks:
            parser.error('Unknown --stack')
        config = ExportConfig(**json.loads(args.config.read_text(encoding='utf-8-sig'))) if args.config else ExportConfig()
        last_phase = None
        def progress(index, total, phase):
            nonlocal last_phase
            if phase != last_phase or index == total or index % 200 == 0:
                print(f'{phase}: {index}/{total}', flush=True)
            last_phase = phase
        selected = [(c,f) for c,f,_,_ in prepare_stacks(stacks,report,progress,args.stack)]
        write_json(args.inventory,dict(report,candidates=summaries))
        if not selected:
            parser.error('No suitable CT stacks found')
        for code, frames in selected:
            result = export_stack(frames, args.output, code, config, report, progress)
            print(result)


if __name__ == '__main__':
    main()
