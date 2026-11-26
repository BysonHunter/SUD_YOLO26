"""Optional new workspace; run_app.py remains the original interface."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--study',type=Path)
    parser.add_argument('--screenshot',type=Path)
    args=parser.parse_args()
    from PyQt6.QtWidgets import QApplication
    from sud_yolo26.ui_workspace.window import WorkspaceWindow
    app=QApplication.instance() or QApplication(sys.argv[:1])
    window=WorkspaceWindow()
    if args.study:window.open_study(args.study)
    if args.check:
        if window.job is not None:window.job.wait()
        app.processEvents()
        print('WORKSPACE_IMPORT_AND_WINDOW_OK')
        window.close();return 0
    window.showMaximized()
    if args.screenshot:
        if window.job is not None:window.job.wait()
        app.processEvents()
        args.screenshot.parent.mkdir(parents=True,exist_ok=True)
        window.grab().save(str(args.screenshot))
        window.close();return 0
    return app.exec()


if __name__=='__main__':raise SystemExit(main())
