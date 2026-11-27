from dataclasses import dataclass
import os
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WEB_ROOT.parent


@dataclass(frozen=True)
class Settings:
    data: Path
    database_url: str
    secure_cookie: bool = True
    public_origin: str = ""
    upload_limit: int = 2 * 1024**3
    unpack_limit: int = 8 * 1024**3
    archive_files: int = 20000
    max_jobs_per_user: int = 10
    max_total_jobs_per_user: int = 30
    max_user_bytes: int = 40 * 1024**3
    job_timeout: int = 3600
    session_seconds: int = 8 * 3600
    lease_seconds: int = 90

    @classmethod
    def from_env(cls):
        data = Path(os.getenv("SUD_DATA_DIR", str(WEB_ROOT / "var"))).resolve()
        return cls(
            data=data,
            database_url=os.getenv("DATABASE_URL", f'sqlite:///{data / "web.sqlite3"}'),
            secure_cookie=os.getenv("SUD_SECURE_COOKIE", "true").lower() == "true",
            public_origin=os.getenv("SUD_PUBLIC_ORIGIN", "").rstrip("/"),
            upload_limit=int(os.getenv("SUD_UPLOAD_LIMIT", str(2 * 1024**3))),
            unpack_limit=int(os.getenv("SUD_UNPACK_LIMIT", str(8 * 1024**3))),
            archive_files=int(os.getenv("SUD_ARCHIVE_FILES", "20000")),
            max_jobs_per_user=int(os.getenv("SUD_MAX_ACTIVE_JOBS", "10")),
            max_total_jobs_per_user=int(os.getenv("SUD_MAX_SAVED_JOBS", "30")),
            max_user_bytes=int(os.getenv("SUD_MAX_USER_BYTES", str(40 * 1024**3))),
            job_timeout=int(os.getenv("SUD_JOB_TIMEOUT", "3600")),
        )

    def job_dir(self, user_id, job_id):
        # IDs come from database rows, never from a requested filesystem path.
        import uuid

        return (
            self.data
            / "users"
            / str(uuid.UUID(user_id))
            / "jobs"
            / str(uuid.UUID(job_id))
        )
