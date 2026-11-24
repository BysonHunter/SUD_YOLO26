from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))


def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--check', action='store_true')
    p.add_argument('--screenshot', type=Path)
    args=p.parse_args()
    from PyQt6.QtWidgets import QApplication
    from sud_yolo26.ui.main_window import KidneyMainWindow
    app=QApplication.instance() or QApplication(sys.argv[:1])
    from sud_yolo26.application.theme import configure_font
    configure_font(app)
    window=KidneyMainWindow()
    window.setWindowTitle('SUD YOLO26')
    if args.check:
        print('APPLICATION_IMPORT_AND_WINDOW_OK; trained checkpoint is checked only when inference starts')
        return 0
    window.show()
    if args.screenshot:
        app.processEvents()
        args.screenshot.parent.mkdir(parents=True,exist_ok=True)
        window.grab().save(str(args.screenshot))
        return 0
    return app.exec()


if __name__=='__main__':
    raise SystemExit(main())
