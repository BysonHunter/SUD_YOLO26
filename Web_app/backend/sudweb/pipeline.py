"""Run one uploaded study in a disposable process, reusing desktop services."""

import json
import os
from pathlib import Path
import sys
import traceback
import time

from .archive import unpack_zip
from .config import Settings, PROJECT_ROOT
from .artifacts import write_json, series_artifacts


def run(root):
    root = Path(root).resolve()
    config = Settings.from_env()
    sys.path.insert(0, str(PROJECT_ROOT / "Application/src"))
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("YOLO_CONFIG_DIR", str(root / "runtime/yolo"))
    os.environ.setdefault("MPLCONFIGDIR", str(root / "runtime/matplotlib"))
    last_progress = [0, ""]

    def progress(percent, stage):
        now = time.monotonic()
        if stage != last_progress[1] or now - last_progress[0] > 0.25:
            write_json(root / "progress.json", dict(percent=int(percent), stage=stage))
            last_progress[:] = [now, stage]

    progress(1, "Распаковка архива")
    unpack_zip(
        root / "input.zip",
        root / "input",
        byte_limit=config.unpack_limit,
        file_limit=config.archive_files,
        progress=lambda i, n, *_: progress(1 + 4 * i / max(n, 1), "Распаковка архива"),
    )
    from sud_yolo26.imaging.dicom_source import discover
    from sud_yolo26.imaging.selection import prepare_stacks
    from sud_yolo26.imaging.pipeline import export_stack, ExportConfig
    from sud_yolo26.application.paths import CONFIG_DIR
    import yaml
    import fpdf.fpdf as fpdf_runtime

    fpdf_runtime.FPDF_CACHE_MODE = 1
    progress(5, "Чтение индекса DICOM и выбор серий")
    stacks, inventory = discover(root / "input")
    prepared = prepare_stacks(stacks, inventory)
    if not prepared:
        raise ValueError(
            "В архиве нет подходящих серий КТ. Проверьте DICOMDIR и состав архива."
        )
    if len(prepared) > int(os.getenv("SUD_MAX_SERIES", "8")):
        raise ValueError(
            "Слишком много подходящих серий в архиве. Загрузите одно исследование."
        )
    from sud_yolo26.detection import service as detector
    from sud_yolo26.settings import service as settings
    from sud_yolo26.analysis import parameters

    weights = Path(
        os.getenv(
            "SUD_WEIGHTS", str(PROJECT_ROOT / "Application/weights/kidney_yolo26.pt")
        )
    )
    runtime = root / "runtime/config"
    runtime.mkdir(parents=True, exist_ok=True)
    detection = yaml.safe_load(
        (CONFIG_DIR / "detection.yaml").read_text(encoding="utf-8")
    )
    detection["device"] = os.getenv("SUD_DEVICE", "cpu")
    (runtime / "detection.yaml").write_text(yaml.safe_dump(detection), encoding="utf-8")
    detector.CONFIG_DIR = runtime
    settings.read_settings = lambda: (
        "Русский",
        str(weights),
        str(root / "input"),
        str(root / "export"),
        "SystemDefault",
        "200",
        "png",
    )
    model = detector.load_model(weights)
    results = []
    warnings = []
    for index, (code, frames, geometry, status) in enumerate(prepared):
        base = 8 + index * 90 / len(prepared)
        span = 90 / len(prepared)
        label = f"Серия {index+1}/{len(prepared)}"
        progress(base, label + ": подготовка проекций")
        # Shared exporter performs geometric layer sorting and honours DICOM windows.
        export = export_stack(
            frames,
            root / "export",
            code,
            ExportConfig(max_voxels=int(os.getenv("SUD_MAX_VOXELS", "300000000"))),
            inventory,
            progress=lambda i, n, *_: progress(
                base + span * 0.35 * i / max(n, 1), label + ": подготовка проекций"
            ),
        )
        coronal = export / "images/coronal"
        detector.detect_stones(
            coronal,
            yolo_weights=weights,
            model=model,
            progress=lambda i, n, *_: progress(
                base + span * (0.4 + 0.3 * i / max(n, 1)), label + ": YOLO26"
            ),
        )
        progress(base + span * 0.72, label + ": сборка и расчёт камней")
        issue = []
        try:
            result = parameters.main(coronal, show_ui=False)
        except (KeyError, AttributeError) as exc:
            # Legacy analysis requires kidney detections. Never invent a missing ROI or a negative result.
            traceback.print_exc()
            issue.append(
                "Расчёт параметров недоступен: недостаточно согласованных детекций почек. "
                "Доступны исходные изображения и детекции; отсутствие расчёта не означает отсутствие камней."
            )
            result = None
        progress(base + span * 0.85, label + ": подготовка 3D и отчёта")
        results.append(series_artifacts(root, export, result, issue))
        warnings.extend(issue)
    progress(99, "Сохранение результатов")
    write_json(
        root / "result.json", dict(schema_version=1, series=results, warnings=warnings)
    )


def main():
    root = Path(sys.argv[1]).resolve()
    try:
        run(root)
    except Exception as exc:
        traceback.print_exc()
        # Do not expose source paths, DICOM names or arbitrary parser exceptions over HTTP.
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            and not any(c in str(exc) for c in ("/", "\\"))
            else "Не удалось обработать исследование. Администратор может проверить журнал задания."
        )
        write_json(root / "error.json", dict(message=message[:500]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
