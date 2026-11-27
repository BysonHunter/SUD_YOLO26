"""Retain source ZIPs before removing derived study files."""
import hashlib
import os
from pathlib import Path
import shutil


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


def archive_study(config, job):
    root = config.job_dir(job.user_id, job.id)
    folder = config.archive_dir(job.user_id, job.id)
    source, target = root / 'input.zip', folder / 'input.zip'
    if job.upload_bytes and job.upload_envelope is None and not source.is_file() and not target.is_file():
        raise OSError('Accepted source ZIP is missing; derived files are retained')
    if source.is_file():
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = folder / 'input.part'
        try:
            with source.open('rb') as inp, temporary.open('wb') as out:
                shutil.copyfileobj(inp, out, 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            if checksum(source) != checksum(temporary):
                raise OSError('Archive verification failed')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    # Uploads interrupted before authentication have no accepted input.zip.
    # A retry after partial cleanup can use the already retained archive.
    if root.exists():
        shutil.rmtree(root)
    return target.is_file()
