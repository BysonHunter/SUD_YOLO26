"""Refresh derived 3D artifacts of completed jobs, without rerunning analysis.

Run inside the worker: python -m sudweb.refresh_visuals
Existing PDF reports, CT images, HU arrays and quantitative manifests are kept.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from .artifacts import kidney_model, stone_model, write_json


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Artifact path is outside its job directory')
    return path


def refresh_visuals(root):
    from matplotlib import cm, pyplot as plt
    from sud_yolo26.analysis.parameters import dens_stone_volume_from_hu, new_dens_stone_from_density
    from sud_yolo26.visualization.stone_geometry import cube_xyz, voxel_figure, checked_spacing

    root = Path(root).resolve()
    result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
    models = previews = 0
    for series in result['series']:
        for side, prefix in [('right', 'rk'), ('left', 'lk')]:
            stones = series.get('stones', {}).get(side, [])
            kidney = series.get('_kidney_models', {}).get(side)
            reference = kidney or next((s.get('_model') for s in stones if s.get('_model')), None)
            if not reference:
                continue
            output = inside(root, reference).parent
            assembly = json.loads((output / 'assembly.json').read_text(encoding='utf-8'))
            spacing = checked_spacing(assembly['spacing_xyz_mm'])
            if kidney:
                volume = np.load(output / f'{prefix.upper()}.npy', allow_pickle=False)
                write_json(inside(root, kidney), kidney_model(volume, spacing))
                models += 1
            for stone in stones:
                volume = np.load(output / f'st_{prefix}_{int(stone["index"])}.npy', allow_pickle=False)
                for key, cut in [('_model', False), ('_cut_model', True)]:
                    if stone.get(key):
                        write_json(inside(root, stone[key]), stone_model(volume, spacing, cut))
                        models += 1
                density = None
                for kind, cmap in [('hu', cm.afmhot), ('density', cm.Set1)]:
                    relative = stone.get('_previews', {}).get(kind)
                    if not relative:
                        continue
                    if kind == 'density' and density is None:
                        density = new_dens_stone_from_density(dens_stone_volume_from_hu(volume)[0])
                    values = volume if kind == 'hu' else density
                    fig = voxel_figure(cube_xyz(values), spacing, cmap=cmap)
                    target = inside(root, relative)
                    temporary = target.with_suffix('.refresh.png')
                    try:
                        fig.savefig(temporary, bbox_inches='tight', transparent=True, format='png')
                        temporary.replace(target)
                    finally:
                        plt.close(fig)
                        temporary.unlink(missing_ok=True)
                    previews += 1
    return dict(models=models, previews=previews)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-root', type=Path, help='One completed job directory; default: all completed jobs')
    args = parser.parse_args()
    if args.job_root:
        roots = [args.job_root]
    else:
        from sqlalchemy import select
        from .config import Settings
        from .db import Database, Job
        config = Settings.from_env()
        db = Database(config.database_url)
        with db.session() as session:
            roots = [config.job_dir(job.user_id, job.id)
                     for job in session.scalars(select(Job).where(Job.state == 'completed'))]
    for number, root in enumerate(roots, 1):
        counts = refresh_visuals(root)
        print(f'Job {number}/{len(roots)}: {counts["models"]} models, {counts["previews"]} previews refreshed', flush=True)


if __name__ == '__main__':
    main()
