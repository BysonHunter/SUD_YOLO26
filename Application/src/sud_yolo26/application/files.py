"""File ordering shared by the image viewers."""
import re
from pathlib import Path


def natural_filename_key(filename):
    name = Path(filename).name
    parts = tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                  for part in re.split(r'(\d+)', name))
    return parts, name.casefold(), name


def image_filenames(folder, image_format):
    """Read once so displayed names and image paths have identical ordering."""
    suffix = str(image_format).lower()
    return sorted((p.name for p in Path(folder).iterdir()
                   if p.is_file() and p.name.lower().endswith(suffix)),
                  key=natural_filename_key)
