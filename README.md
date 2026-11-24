# SUD YOLO26 — установка и запуск

Программа для подготовки изображений КТ из DICOM, обнаружения почек и камней
моделью YOLO26, расчёта параметров камней и трёхмерного просмотра.

## Требования

- Windows 10/11 x64 и Python 3.11 x64 с Python Launcher (`py`).
- Git или скачанный архив GitHub → Code → Download ZIP.
- Обученный файл `best.pt` семейства YOLO26 с шестью классами SUD. Веса COCO не подходят.

Данные пациентов, DICOM, датасеты, результаты и веса в репозиторий не включены.
Обработка выбранных данных выполняется локально. Результаты требуют проверки специалистом.

## Установка

Откройте PowerShell:

```powershell
git clone https://github.com/BysonHunter/SUD_YOLO26.git
cd SUD_YOLO26\Application
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

Создаётся окружение `.venv`, устанавливаются зависимости и проверяется главное окно.
По умолчанию устанавливается CPU-вариант PyTorch.

Для компьютера с NVIDIA и подходящим драйвером:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Cuda
```

Устанавливается PyTorch с CUDA 12.8. Для использования GPU замените `device: cpu`
на `device: '0'` в [настройках детектора](Application/config/detection.yaml).

Для сжатых DICOM при необходимости установите декодеры:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dicom-codecs.txt
```

## Установка обученной модели

Из каталога `Application` укажите путь к своим весам:

```powershell
.\.venv\Scripts\python.exe .\install_model.py 'D:\Models\best.pt'
```

Установщик проверит модель, создаст `weights/kidney_yolo26.pt` и манифест происхождения.
Исходный файл сохраняется. Если модель уже установлена, перед заменой сохраните её
под другим именем вместе с манифестом.

Ожидаемый порядок классов ID 0–5: `left_kidney`, `stone`, `right_kidney`,
`left_kidney_pieloectasy`, `right_kidney_pieloectasy`, `staghorn_stones`.

## Запуск

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

При первом запуске создаётся локальный `config/settings.yaml` из
[шаблона](Application/config/settings.example.yaml). Укажите пути к DICOM и выходному
каталогу в программе. Начальные пути: `Application/data/dicom` и `Application/data/output`.

1. Выберите серию DICOM и получите аксиальные и корональные изображения.
2. Выполните детектирование на корональных изображениях.
3. Откройте каталог `images/coronal/detect` нужной серии.
4. Нажмите «Параметры камней» → «Пересчитать параметры».
5. Просмотрите параметры, модели почки и камня; при необходимости сохраните PDF.

Сохраняйте весь каталог серии `КодПациента/дата/V001`: для расчёта нужны PNG, метки,
`geometry/geometry.json`, `selection.json` и `volume/hu_lps.npy`.
Старый формат с `*arrayinfo.csv` и `*array.npy` также поддерживается.

## Проверка установки

Из каталога `Application`:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe .\run_app.py --check
```

- Не найдены веса: выполните `install_model.py` с обученным `best.pt`.
- Ошибка 3D: проверьте установку `pyvista` и `vtk` в `.venv` и перезапустите программу.
- Нет геометрии или HU-массива: используйте полный каталог экспорта серии.
- CUDA недоступна: оставьте `device: cpu` в настройках детектора.
