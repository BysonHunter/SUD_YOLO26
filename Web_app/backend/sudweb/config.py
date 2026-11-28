from dataclasses import dataclass, field
import os
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WEB_ROOT.parent


@dataclass(frozen=True)
class RegistrationSettings:
    enabled: bool = True
    consent_text: str = 'Даю согласие на обработку моего имени пользователя и адреса электронной почты для создания и обслуживания учётной записи в SUD YOLO26.'
    policy_url: str = ''

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('SUD_REGISTRATION_ENABLED', 'true').lower() == 'true',
            consent_text=os.getenv('SUD_CONSENT_TEXT', cls.consent_text),
            policy_url=os.getenv('SUD_PRIVACY_POLICY_URL', ''))


@dataclass(frozen=True)
class MailSettings:
    host: str = 'smtp.yandex.ru'
    port: int = 465
    username: str = ''
    password: str = field(default='', repr=False)
    sender: str = ''

    @classmethod
    def from_env(cls):
        return cls(host=os.getenv('SUD_SMTP_HOST', 'smtp.yandex.ru'),
            port=int(os.getenv('SUD_SMTP_PORT', '465')),
            username=os.getenv('SUD_SMTP_USER', ''),
            password=os.getenv('SUD_SMTP_PASSWORD', ''),
            sender=os.getenv('SUD_MAIL_FROM', '') or os.getenv('SUD_SMTP_USER', ''))


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
    registration: RegistrationSettings = field(default_factory=RegistrationSettings)
    mail: MailSettings = field(default_factory=MailSettings)

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
            registration=RegistrationSettings.from_env(),
            mail=MailSettings.from_env(),
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

    def archive_dir(self, user_id, job_id):
        import uuid
        return self.data / 'archives' / str(uuid.UUID(user_id)) / str(uuid.UUID(job_id))
