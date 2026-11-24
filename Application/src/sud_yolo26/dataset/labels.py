"""Runtime label-file utilities. Training data preparation remains in Train_model."""
from pathlib import Path


def delete_files(filename):
    image = Path(filename)
    label = image.parent / 'labels' / (image.stem + '.txt')
    if image.parent.name != 'detect':
        raise ValueError('Deletion is limited to an explicitly selected detection result')
    image.unlink()
    label.unlink(missing_ok=True)


def main():
    from PyQt6.QtWidgets import QMessageBox
    QMessageBox.information(None, 'SUD YOLO26', 'Подготовка обучающего датасета вынесена в каталог Train_model.')
