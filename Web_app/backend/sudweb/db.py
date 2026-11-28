from contextlib import contextmanager
import time
import uuid

from sqlalchemy import (
    create_engine,
    String,
    Text,
    Float,
    Boolean,
    Integer,
    BigInteger,
    ForeignKey,
    select,
    update,
    func,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def new_id():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, unique=True)
    consent_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    consent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_policy_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_reset_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')


class PasswordReset(Base):
    __tablename__ = 'password_resets'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    expires: Mapped[float] = mapped_column(Float)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[float] = mapped_column(Float)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    since: Mapped[float] = mapped_column(Float)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(180))
    state: Mapped[str] = mapped_column(String(20), default="uploading", index=True)
    stage: Mapped[str] = mapped_column(String(100), default="Загрузка архива")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    updated: Mapped[float] = mapped_column(Float, default=time.time)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    run_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_by_user_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    archived_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    upload_envelope: Mapped[str | None] = mapped_column(Text, nullable=True)


class Database:
    def __init__(self, url):
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args=(
                {"check_same_thread": False, "timeout": 30}
                if url.startswith("sqlite")
                else {}
            ),
        )
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def pragmas(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def init(self):
        Base.metadata.create_all(self.engine)
        # Upgrade installations created before account administration was added.
        # No table recreation or data copying; compose runs init before the API.
        with self.engine.begin() as connection:
            if 'deleted_at' not in {c['name'] for c in inspect(connection).get_columns('users')}:
                connection.execute(text('ALTER TABLE users ADD COLUMN deleted_at FLOAT'))
            user_columns = {c['name'] for c in inspect(connection).get_columns('users')}
            for name, kind in [('email','VARCHAR(254)'),
                ('password_reset_required', 'BOOLEAN NOT NULL DEFAULT false'),
                ('consent_at','FLOAT'),
                ('consent_text','TEXT'), ('consent_policy_url','TEXT'), ('consent_version','VARCHAR(64)')]:
                if name not in user_columns:
                    connection.execute(text(f'ALTER TABLE users ADD COLUMN {name} {kind}'))
            connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users (email)'))
            columns = {c['name'] for c in inspect(connection).get_columns('jobs')}
            for name, kind in [('deleted_by_user_at', 'FLOAT'), ('archived_at', 'FLOAT'),
                               ('source_job_id', 'VARCHAR(36)'), ('upload_envelope', 'TEXT')]:
                if name not in columns:
                    connection.execute(text(f'ALTER TABLE jobs ADD COLUMN {name} {kind}'))

    @contextmanager
    def session(self):
        with self.Session() as session:
            with session.begin():
                yield session

    def claim(self, lease_seconds):
        """DB queue: atomic compare-and-swap; PostgreSQL also skips locked rows."""
        with self.session() as s:
            query = (
                select(Job)
                .where(Job.state == "queued", Job.cancelled == False)
                .order_by(Job.created)
            )
            if self.engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            job = s.scalar(query.limit(1))
            if job is None:
                return None
            token = new_id()
            changed = s.execute(
                update(Job)
                .where(Job.id == job.id, Job.state == "queued")
                .values(
                    state="running",
                    stage="Подготовка",
                    updated=time.time(),
                    run_token=token,
                    lease_until=time.time() + lease_seconds,
                )
            )
            if changed.rowcount != 1:
                return None
            return dict(id=job.id, user_id=job.user_id, token=token)

    def expire_leases(self):
        with self.session() as s:
            now = time.time()
            s.execute(
                update(Job)
                .where(Job.state == "running", Job.lease_until < now)
                .values(
                    state="failed",
                    error="Обработчик остановился. Загрузите архив повторно.",
                    updated=now,
                )
            )
            s.execute(
                update(Job)
                .where(Job.state == "uploading", Job.updated < now - 3600)
                .values(state="failed", error="Загрузка была прервана.", updated=now)
            )
