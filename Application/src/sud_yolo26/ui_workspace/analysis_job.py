"""Private headless calculation entry point for the workspace process worker."""
import json
from pathlib import Path
import sys


def main():
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    import matplotlib
    matplotlib.use('Agg',force=True)
    from sud_yolo26.analysis.parameters import main as calculate
    result=calculate(Path(sys.argv[1]),show_ui=False)
    cache=Path(result['stones_dir_path'])/'result_cache.pkl'
    if not cache.is_file():raise RuntimeError('Не удалось сохранить результат расчёта')
    print('SUD_WORKSPACE_RESULT='+json.dumps(dict(cache=str(cache.resolve()))),flush=True)


if __name__=='__main__':main()
