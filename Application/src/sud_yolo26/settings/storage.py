from pathlib import Path
import yaml

from sud_yolo26.application.paths import APP_ROOT, CONFIG_DIR


def get_config_path():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path=CONFIG_DIR/'settings.yaml'
    if not path.exists():
        example=CONFIG_DIR/'settings.example.yaml'
        path.write_text(example.read_text(encoding='utf-8'),encoding='utf-8')
    return path


def read_yaml(file_path):
    return yaml.safe_load(Path(file_path).read_text(encoding='utf-8'))


def read_settings():
    d = read_yaml(get_config_path())
    def resolve(value):
        p = Path(value)
        return str(p if p.is_absolute() else APP_ROOT / p)
    return (d['language'], resolve(d['weights']), resolve(d['dicom_input']), resolve(d['output']),
            d['theme'], str(d['image_count']), d['image_format'])


def save_settings(lang_of_interface, path_to_yolo_weights, default_input_dicom_folder,
                  default_output_folder, current_color_theme, img_count, img_format):
    def portable(value):
        p = Path(value).resolve()
        return p.relative_to(APP_ROOT).as_posix() if p.is_relative_to(APP_ROOT) else p.as_posix()
    d = dict(language=lang_of_interface, weights=portable(path_to_yolo_weights),
             dicom_input=portable(default_input_dicom_folder), output=portable(default_output_folder),
             theme=current_color_theme, image_count=int(img_count), image_format=img_format)
    get_config_path().write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding='utf-8')
