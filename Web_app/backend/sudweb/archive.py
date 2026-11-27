"""Bounded ZIP extraction preserving the directory layout needed by DICOMDIR."""

from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


def unpack_zip(archive, destination, byte_limit, file_limit, progress=lambda *_: None):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    seen, written = set(), 0
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if not members or len(members) > file_limit:
            raise ValueError("Пустой ZIP или превышено число файлов архива.")
        if sum(m.file_size for m in members) > byte_limit:
            raise ValueError("Распакованный архив превышает допустимый размер.")
        for index, entry in enumerate(members):
            raw = entry.filename.replace("\\", "/")
            path = PurePosixPath(raw)
            if (
                path.is_absolute()
                or not path.parts
                or any(p in (".", "..") for p in path.parts)
                or any(
                    ":" in p or "\x00" in p or p.endswith((" ", "."))
                    for p in path.parts
                )
                or any(
                    re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
                    for p in path.parts
                )
            ):
                raise ValueError("Недопустимый путь внутри ZIP.")
            key = path.as_posix().rstrip("/").casefold()
            mode = (entry.external_attr >> 16) & 0xFFFF
            if (
                key in seen
                or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
            ):
                raise ValueError("Повторный путь или специальный файл внутри ZIP.")
            seen.add(key)
            if entry.flag_bits & 1:
                raise ValueError("ZIP с паролем не поддерживается.")
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError("Путь ZIP выходит за каталог задания.")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(entry) as inp, target.open("xb") as out:
                while chunk := inp.read(1024 * 1024):
                    written += len(chunk)
                    if written > byte_limit:
                        raise ValueError("Фактический размер ZIP превышает лимит.")
                    out.write(chunk)
            progress(index + 1, len(members), "Распаковка ZIP")
    return written
