# Установка и запуск SUD YOLO26

После установки переходите к [руководству пользователя](USER_GUIDE.md).

## 1. Требования

- Windows 10/11 x64, Python **3.11 x64** с Python Launcher (`py`) и PowerShell.
- Интернет для загрузки зависимостей; Git либо ZIP-архив репозитория.
- Обученный `best.pt` семейства YOLO26 с шестью классами SUD.
- Для интерактивного 3D — графический драйвер с поддержкой OpenGL.
- Для детектирования на GPU — NVIDIA, например RTX 3090, и драйвер,
  совместимый с CUDA 12.8. На CPU NVIDIA не требуется.

Проверьте Python:

```powershell
py -3.11 --version
py -3.11 -c "import struct; print(struct.calcsize('P') * 8)"
```

Ожидаются `Python 3.11.x` и `64`. Если `py` не найден, установите Python 3.11
с Python Launcher и заново откройте PowerShell.
HU-массив большой серии может занимать гигабайты. Дополнительно нужны место
для изображений и результатов и оперативная память для обработки массива.
Потребление зависит от размеров конкретной КТ.

## 2. Скачать программу

```powershell
git clone https://github.com/BysonHunter/SUD_YOLO26.git
cd SUD_YOLO26\Application
```

Без Git: выберите **Code → Download ZIP**, распакуйте архив в доступную для записи
папку и откройте PowerShell в подкаталоге `Application`.
**Все следующие команды установки выполняются из `Application`.**

## 3. Установить зависимости

При первоначальной установке выберите один вариант.

### CPU

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

### NVIDIA GPU, включая GeForce RTX 3090

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Cuda
```

Скрипт создаёт `.venv`, устанавливает PyTorch 2.7.1, torchvision 0.22.1 и зависимости,
проверяет их совместимость и создание окна прежнего интерфейса. GPU-вариант
использует сборку PyTorch CUDA 12.8. Вручную активировать окружение не нужно.
Дождитесь завершения; при ошибке сохраните её текст.

Для сжатых DICOM при необходимости установите дополнительные декодеры:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-dicom-codecs.txt
```

### Проверить GPU и выбрать устройство

```powershell
.\.venv\Scripts\python.exe -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.version.cuda); print('GPU available:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

Для GPU ожидается `GPU available: True`. В
[config/detection.yaml](Application/config/detection.yaml) замените:

```yaml
device: cpu
```

на:

```yaml
device: '0'
```

Сохраните файл и перезапустите приложение. Флаг `-Cuda` устанавливает зависимости,
но не переключает `device`. Для CPU оставьте `cpu`.
GPU ускоряет детектор; чтение DICOM и расчёт параметров не становятся полностью GPU-процедурами.

## 4. Установить обученные веса

Веса не входят в репозиторий. Скопируйте свой checkpoint и укажите реальный путь:

```powershell
.\.venv\Scripts\python.exe .\install_model.py 'D:\Models\best.pt'
```

Установщик проверит архитектуру и классы, создаст
`Application/weights/kidney_yolo26.pt` и `model_manifest.json` с SHA-256 и источником.
Исходный checkpoint сохраняется.

| ID | Класс |
| --- | --- |
| 0 | `left_kidney` |
| 1 | `stone` |
| 2 | `right_kidney` |
| 3 | `left_kidney_pieloectasy` |
| 4 | `right_kidney_pieloectasy` |
| 5 | `staghorn_stones` |

Веса COCO и старый checkpoint YOLOv5 не заменяют обученную модель YOLO26.
Установщик не перезаписывает существующую модель. Перед заменой перенесите
установленный checkpoint и манифест в резервную копию вне папки `weights`,
затем повторите команду.

## 5. Запустить новый интерфейс

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_workspace.ps1
```

Откроется «SUD YOLO26 — рабочее пространство». При первом обращении к настройкам
создаётся локальный `config/settings.yaml` из
[шаблона](Application/config/settings.example.yaml).

Нажмите **«Настройки»** внизу слева и проверьте пути к весам, DICOM и результатам.
Начальные каталоги — `Application/data/dicom` и `Application/data/output`.
Новый интерфейс использует собственное оформление; общие поля языка и темы
не переводят и не перекрашивают все его элементы.

Сразу открыть ранее подготовленную серию:

```powershell
.\.venv\Scripts\python.exe .\run_workspace.py --study 'D:\CT\Patient\Study\V001'
```

Прежний интерфейс сохранён и запускается отдельно:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

## 6. Проверить установку

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe .\run_workspace.py --check
.\.venv\Scripts\python.exe .\run_app.py --check
```

Ожидаются отсутствие конфликтов зависимостей и сообщения
`WORKSPACE_IMPORT_AND_WINDOW_OK` и `APPLICATION_IMPORT_AND_WINDOW_OK`.
Проверки создают окна, но не выполняют детектирование, не проверяют качество
модели и не заменяют запуск 3D. Затем обработайте свою серию по разделу 3
[руководства](USER_GUIDE.md): подготовка → детектирование → расчёт → 3D → PDF.

## 7. Обновить программу

Закройте приложение, сохраните резервную копию весов, настроек и исследований.
В корне клонированного репозитория:

```powershell
git status --short
git pull --ff-only
cd Application
```

При локальных изменениях сначала разберите их; не используйте принудительный
сброс. Например, выбор GPU меняет отслеживаемый `config/detection.yaml` и может
потребовать согласования при обновлении. Если обновились зависимости, повторите
установку своего варианта CPU/GPU, затем проверки раздела 6.

При использовании ZIP распакуйте новую версию отдельно, установите зависимости
и веса, затем откройте существующие серии. Старую папку сохраните до проверки.

## 8. Решение проблем

| Проблема | Действия |
| --- | --- |
| PowerShell запрещает `.ps1` | Используйте команду с `-ExecutionPolicy Bypass`; глобальную политику менять не нужно. |
| Используется другой Python | Запускайте через `start_workspace.ps1` или `.venv\Scripts\python.exe`. |
| Нет модели | Выполните `install_model.py` и проверьте путь в настройках. |
| CUDA недоступна | Проверьте драйвер, GPU-вариант установки и команду проверки; временно верните `device: cpu`. |
| Нет PyVista/VTK | Проверьте `pip check`, повторите установку в `.venv` и перезапустите программу. |
| Ошибка OpenGL или пустой 3D | Проверьте драйвер и обычный графический сеанс. `QT_QPA_PLATFORM=offscreen` для интерактивного 3D не подходит. |
| Ошибка декодирования DICOM | Установите дополнительные декодеры. |
| Нет прав записи или места | Выберите доступный каталог и освободите место; администратор обычно не нужен. |

Для выпуска нового интерфейса проверены существующее окружение, оба окна запуска
и 75 локальных тестов. Чистая установка и GPU на другом компьютере требуют проверки
на целевой машине.
