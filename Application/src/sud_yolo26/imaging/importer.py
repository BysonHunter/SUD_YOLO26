"""Qt adapter for the headless DICOM preparation pipeline."""
from sud_yolo26.application import state
from sud_yolo26.settings import service as settings
from .dicom_source import discover
from .selection import prepare_stacks
from .pipeline import ExportConfig, export_stack


class ImportCancelled(Exception):
    pass


def main(dicom_path, images_path):
    from PyQt6.QtWidgets import QProgressDialog, QApplication
    progress = QProgressDialog('Инвентаризация DICOM', 'Отмена', 0, 0)
    progress.setModal(True)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.show()
    def update(index, total, phase):
        progress.setMaximum(total + 1)
        progress.setValue(index)
        progress.setLabelText(f'{phase}: {index}/{total}')
        QApplication.processEvents()
        if progress.wasCanceled():
            raise ImportCancelled()
    try:
        stacks, inventory = discover(dicom_path, update)
        candidates = prepare_stacks(stacks,inventory,update)
        if not candidates:
            raise ValueError('Не найдена пригодная регулярная CT-серия. Используйте prepare_dicom.py для отчёта по всем сериям.')
        _, _, _, _, _, requested, _ = settings.read_settings()
        results = []
        for code, frames, geometry, status in candidates:
            config = ExportConfig(coronal_image_count=int(requested))
            result = export_stack(frames, images_path, code, config, inventory, update)
            state.global_messages.append(f'Серия {status["series_number"]} {status["description"]}: {result}')
            state.global_messages.append(f'Аксиальные: {result / "images/axial"}; корональные: {result / "images/coronal"}.')
            results.append(result)
        return results[0] if len(results)==1 else results
    except ImportCancelled:
        state.global_messages.append('Импорт DICOM отменён. Завершённые результаты сохранены.')
        return None
    finally:
        progress.close()
