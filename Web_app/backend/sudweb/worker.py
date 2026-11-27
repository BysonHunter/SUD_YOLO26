"""Durable DB queue supervisor. One fresh subprocess per job, no shared ML state."""

import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from sqlalchemy import update
from .config import Settings
from .db import Database, Job
from .api import bytes_used

log = logging.getLogger(__name__)


def stop(process):
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def execute(config, db, record):
    root = config.job_dir(record["user_id"], record["id"])
    environment = dict(
        os.environ,
        MPLBACKEND="Agg",
        QT_QPA_PLATFORM="offscreen",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        YOLO_CONFIG_DIR=str(root / "runtime/yolo"),
        MPLCONFIGDIR=str(root / "runtime/matplotlib"),
    )
    started, next_quota = time.monotonic(), 0
    state, error = "failed", "Обработка не завершена. Обратитесь к администратору."
    with (root / "worker.log").open("ab") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "sudweb.pipeline", str(root)],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            while True:
                with db.session() as s:
                    job = s.get(Job, record["id"])
                    if (
                        job is None
                        or job.run_token != record["token"]
                        or job.state != "running"
                    ):
                        stop(process)
                        return
                    if job.cancelled:
                        state, error = "cancelled", None
                        stop(process)
                        break
                    job.lease_until, job.updated = (
                        time.time() + config.lease_seconds,
                        time.time(),
                    )
                    try:
                        progress = json.loads(
                            (root / "progress.json").read_text(encoding="utf-8")
                        )
                        job.stage = str(progress["stage"])[:100]
                        job.progress = max(0, min(99, int(progress["percent"])))
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                if time.monotonic() - started > config.job_timeout:
                    error = "Превышено время обработки задания."
                    stop(process)
                    break
                if time.monotonic() >= next_quota:
                    next_quota = time.monotonic() + 15
                    if (
                        bytes_used(root) > 16 * 1024**3
                        or bytes_used(root.parents[1]) > config.max_user_bytes
                    ):
                        error = "Превышен лимит хранения результатов."
                        stop(process)
                        break
                code = process.poll()
                if code is not None:
                    if code == 0 and (root / "result.json").is_file():
                        state, error = "completed", None
                    else:
                        try:
                            error = json.loads(
                                (root / "error.json").read_text(encoding="utf-8")
                            )["message"]
                        except (OSError, ValueError, KeyError):
                            pass
                    break
                time.sleep(1)
        finally:
            stop(process)
    with db.session() as s:
        s.execute(
            update(Job)
            .where(
                Job.id == record["id"],
                Job.run_token == record["token"],
                Job.state == "running",
            )
            .values(
                state=state,
                error=error,
                updated=time.time(),
                lease_until=0,
                stage={
                    "completed": "Готово",
                    "failed": "Ошибка",
                    "cancelled": "Отменено",
                }[state],
                progress=100 if state == "completed" else 0,
            )
        )


def main():
    logging.basicConfig(level=logging.INFO)

    def terminate(*_):
        raise SystemExit(0)  # execute() finally terminates the current child.

    signal.signal(signal.SIGTERM, terminate)
    config = Settings.from_env()
    db = Database(config.database_url)
    once = "--once" in sys.argv
    while True:
        db.expire_leases()
        job = db.claim(config.lease_seconds)
        if job:
            try:
                execute(config, db, job)
            except Exception:
                log.exception("Worker supervisor failure for job %s", job["id"])
                # Lease expiry records an interrupted job. Never reuse its output directory.
        elif not once:
            time.sleep(2)
        if once:
            return


if __name__ == "__main__":
    main()
