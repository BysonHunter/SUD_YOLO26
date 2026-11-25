"""Cooperative workers for the new interface, using existing computation services."""
import traceback
from PyQt6.QtCore import QThread,pyqtSignal


class Cancelled(Exception):
    pass


class AnalysisError(RuntimeError):
    def __init__(self,message,details):
        super().__init__(message)
        self.details=details


class Job(QThread):
    progress=pyqtSignal(int,int,str)
    succeeded=pyqtSignal(object)
    failed=pyqtSignal(str,str)
    cancelled=pyqtSignal()

    def __init__(self,operation,parent=None):
        super().__init__(parent)
        self.operation=operation

    def report(self,index,total,message):
        if self.isInterruptionRequested():
            raise Cancelled()
        self.progress.emit(index,total,message)

    def run(self):
        try:
            value=self.operation(self.report)
            self.succeeded.emit(value)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc),getattr(exc,'details',traceback.format_exc()))


def import_dicom(source,output,count,report):
    from sud_yolo26.imaging.dicom_source import discover
    from sud_yolo26.imaging.selection import prepare_stacks
    from sud_yolo26.imaging.pipeline import ExportConfig,export_stack
    stacks,inventory=discover(source,report)
    candidates=prepare_stacks(stacks,inventory,report)
    if not candidates:
        raise ValueError('Не найдена пригодная CT-серия. Проверьте выбранный каталог DICOM.')
    results=[]
    for code,frames,geometry,status in candidates:
        report(0,0,'Подготовка серии '+str(status['series_number']))
        results.append(export_stack(frames,output,code,ExportConfig(coronal_image_count=count),inventory,report))
    return results


def detect(folder,weights,report):
    from sud_yolo26.detection.service import detect_stones
    # The backend publishes detect only on success. Leave failed staging alone:
    # another running instance may also own a .detect-* directory in this study.
    return detect_stones(folder,yolo_weights=weights,progress=report)


def calculate(case,report,use_cache=True):
    """Isolate legacy pyplot and global analysis state in a headless process."""
    import json
    import os
    import pickle
    from pathlib import Path
    import subprocess
    import sys
    from sud_yolo26.analysis.parameters import load_cached_result
    if use_cache:
        cached=load_cached_result(case)
        if cached is not None:return cached
    report(0,0,'Сборка камней, расчёт параметров и PDF')
    environment=dict(os.environ,MPLBACKEND='Agg',PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    process=subprocess.run([sys.executable,str(Path(__file__).with_name('analysis_job.py')),str(case)],
        capture_output=True,text=True,encoding='utf-8',errors='replace',env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if process.returncode:
        lines=[line for line in process.stderr.splitlines() if line.strip()]
        raise AnalysisError(lines[-1] if lines else 'Не удалось завершить расчёт',process.stderr)
    records=[line[len('SUD_WORKSPACE_RESULT='):] for line in process.stdout.splitlines()
             if line.startswith('SUD_WORKSPACE_RESULT=')]
    if not records:raise AnalysisError('Процесс расчёта не вернул результат',process.stdout+process.stderr)
    with Path(json.loads(records[-1])['cache']).open('rb') as stream:
        return pickle.load(stream)
