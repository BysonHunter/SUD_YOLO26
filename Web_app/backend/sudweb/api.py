"""HTTP layer: no DICOM, PyTorch, Qt or analysis work in API processes."""

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import logging
from pathlib import Path
import secrets
import shutil
import time
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, update, delete
from sqlalchemy.exc import IntegrityError
from starlette.middleware.gzip import GZipMiddleware

from .config import Settings, WEB_ROOT
from .db import Database, User, LoginSession, LoginAttempt, Job, new_id
from .security import digest, password_matches, password_hash
from .manage import create_user, UserExists

log = logging.getLogger(__name__)
COOKIE = "sud_session"
TERMINAL = {"completed", "failed", "cancelled"}


def origin_allowed(origin, expected, request_origin):
    """Accept same-origin requests and equivalent local URLs on the same port."""
    if origin == expected:
        return True
    try:
        urls = [urlsplit(value) for value in (origin, expected, request_origin)]
        loopback = {"localhost", "127.0.0.1", "::1"}
        if any(
            url.hostname not in loopback
            or url.scheme not in {"http", "https"}
            or url.username is not None
            or url.password is not None
            or url.path
            or url.query
            or url.fragment
            for url in urls
        ):
            return False
        return (
            len(
                {
                    (url.scheme, url.port or (443 if url.scheme == "https" else 80))
                    for url in urls
                }
            )
            == 1
        )
    except ValueError:
        return False


def job_json(job):
    return {
        key: getattr(job, key)
        for key in (
            "id",
            "filename",
            "state",
            "stage",
            "progress",
            "created",
            "updated",
            "error",
        )
    }


def private_file(root, relative):
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise HTTPException(404, "Файл результата недоступен.")
    return candidate


def public_data(value):
    if isinstance(value, dict):
        return {k: public_data(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [public_data(v) for v in value]
    return value


def bytes_used(root):
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            pass  # Another request may have deleted a finished job.
    return total


async def account_payload(request, fields):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4096:
            raise HTTPException(413, 'Слишком большой запрос.')
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, 'Некорректные данные учётной записи.')
    if (not isinstance(payload, dict) or set(payload) != fields
            or not all(isinstance(payload[k], str) for k in payload)):
        raise HTTPException(400, 'Проверьте заполнение полей учётной записи.')
    return payload


def create_app(settings=None, database=None):
    config = settings or Settings.from_env()
    config.data.mkdir(parents=True, exist_ok=True)
    db = database or Database(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        # Schema and users are explicitly initialized by `python -m sudweb.manage init`.
        with db.engine.connect() as connection:
            connection.execute(select(User.id).limit(1))
        yield

    app = FastAPI(
        title="SUD YOLO26 Web",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config, app.state.db = config, db
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.middleware("http")
    async def boundaries(request, call_next):
        limit = (
            config.upload_limit
            if request.url.path == "/api/jobs" and request.method == "POST"
            else 65536
        )
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Некорректная длина запроса."}, 400)
        if length > limit:
            return JSONResponse({"detail": "Превышен допустимый размер запроса."}, 413)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected = config.public_origin or str(request.base_url).rstrip("/")
            if origin and not origin_allowed(
                origin, expected, str(request.base_url).rstrip("/")
            ):
                return JSONResponse({"detail": "Недопустимый источник запроса."}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def identity(request: Request):
        token = request.cookies.get(COOKIE, "")
        with db.session() as s:
            session = s.get(LoginSession, digest(token)) if token else None
            user = (
                s.get(User, session.user_id)
                if session and session.expires > time.time()
                else None
            )
            if user is None or not user.enabled or user.deleted_at is not None:
                raise HTTPException(401, "Войдите в систему.")
            if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), session.csrf
            ):
                raise HTTPException(
                    403, "Недействительный токен запроса. Обновите страницу."
                )
            return dict(id=user.id, username=user.username, csrf=session.csrf)

    def administrator(user=Depends(identity)):
        if user['username'] != 'admin':
            raise HTTPException(403, 'Доступно только администратору.')
        return user

    def login_response(session, user):
        token, csrf = secrets.token_urlsafe(32), secrets.token_hex(24)
        session.add(LoginSession(token_hash=digest(token), user_id=user.id,
            csrf=csrf, expires=time.time() + config.session_seconds))
        response = JSONResponse(dict(username=user.username, csrf=csrf,
            is_admin=user.username == 'admin', upload_limit=config.upload_limit))
        response.set_cookie(COOKIE, token, httponly=True, secure=config.secure_cookie,
                            samesite='lax', max_age=config.session_seconds, path='/')
        return response

    def owned(job_id, user):
        with db.session() as s:
            job = s.get(Job, job_id)
            if job is None or job.user_id != user["id"]:
                raise HTTPException(404, "Задание не найдено.")
            return job

    def result(job_id, user):
        job = owned(job_id, user)
        if job.state != "completed":
            raise HTTPException(409, "Обработка ещё не завершена.")
        root = config.job_dir(user["id"], job.id)
        path = private_file(root, "result.json")
        if path.stat().st_size > 16 * 1024**2:
            raise HTTPException(
                409, "Результат превышает лимит просмотра. Обратитесь к администратору."
            )
        return root, json.loads(path.read_text(encoding="utf-8"))

    def series_result(job_id, series_index, user):
        root, data = result(job_id, user)
        if not 0 <= series_index < len(data["series"]):
            raise HTTPException(404, "Серия не найдена.")
        return root, data["series"][series_index]

    @app.get("/healthz")
    def health():
        with db.engine.connect() as connection:
            connection.execute(select(1))
        return {"status": "ok"}

    @app.post("/api/login")
    async def login(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise HTTPException(413, "Слишком большой запрос.")
        try:
            payload = json.loads(raw)
            username, password = str(payload["username"]).strip().lower(), str(
                payload["password"]
            )
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "Укажите логин и пароль.")
        if len(username) > 80 or len(password) > 128:
            raise HTTPException(400, "Слишком длинные учётные данные.")
        # Rate-limit both the account and the direct peer IP, without logging credentials.
        keys = [
            digest("user:" + username),
            digest("ip:" + (request.client.host if request.client else "unknown")),
        ]
        blocked = False
        with db.session() as s:
            now = time.time()
            for key in keys:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                insert = (
                    pg_insert
                    if db.engine.dialect.name == "postgresql"
                    else sqlite_insert
                )
                s.execute(
                    insert(LoginAttempt)
                    .values(key=key, count=0, since=now)
                    .on_conflict_do_nothing(index_elements=["key"])
                )
                attempt = s.scalar(
                    select(LoginAttempt)
                    .where(LoginAttempt.key == key)
                    .with_for_update()
                )
                if now - attempt.since > 900:
                    attempt.since, attempt.count = now, 1
                elif attempt.count >= (15 if key == keys[0] else 120):
                    blocked = True
                else:
                    s.execute(
                        update(LoginAttempt)
                        .where(LoginAttempt.key == key)
                        .values(count=LoginAttempt.count + 1)
                    )
        if blocked:
            raise HTTPException(429, "Слишком много попыток. Повторите через 15 минут.")
        with db.session() as s:
            user = s.scalar(select(User).where(User.username == username).with_for_update())
            # Constant-cost check also for an unknown account.
            stored = user.password_hash if user else app.state.dummy_password
            valid = password_matches(password, stored)
            if not user or not user.enabled or user.deleted_at is not None or not valid:
                raise HTTPException(401, "Неверный логин или пароль.")
            s.execute(
                update(LoginAttempt).where(LoginAttempt.key == keys[0]).values(count=0)
            )
            return login_response(s, user)

    app.state.dummy_password = password_hash(secrets.token_hex(20))

    @app.get("/api/me")
    def me(user=Depends(identity)):
        return {
            "username": user["username"],
            "csrf": user["csrf"],
            "upload_limit": config.upload_limit,
            "is_admin": user['username'] == 'admin',
        }

    @app.get('/api/admin/users')
    def users(admin=Depends(administrator)):
        with db.session() as s:
            rows = s.execute(select(User, func.count(Job.id))
                .outerjoin(Job, Job.user_id == User.id)
                .where(User.deleted_at.is_(None))
                .group_by(User.id).order_by(User.username))
            return [dict(id=user.id, username=user.username, enabled=user.enabled,
                         is_admin=user.username == 'admin', studies=count)
                    for user, count in rows]

    @app.post('/api/admin/users', status_code=201)
    async def add_user(request: Request, admin=Depends(administrator)):
        payload = await account_payload(request, {'username', 'password'})
        if len(payload['username']) > 80 or len(payload['password']) > 128:
            raise HTTPException(400, 'Логин — до 80 символов, пароль — от 12 до 128 символов.')
        try:
            return await asyncio.to_thread(create_user, db, payload['username'], payload['password'])
        except UserExists as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except IntegrityError:
            # Concurrent requests can pass the initial lookup together.
            raise HTTPException(409, 'Такой пользователь уже существует.')

    @app.delete('/api/admin/users/{user_id}')
    def remove_user(user_id: str, admin=Depends(administrator)):
        with db.session() as s:
            user = s.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None or user.deleted_at is not None:
                raise HTTPException(404, 'Пользователь не найден.')
            if user.id == admin['id'] or user.username == 'admin':
                raise HTTPException(409, 'Учётную запись администратора удалить нельзя.')
            # Keep the owner UUID and studies, so account removal never deletes
            # patient data or allows a new account to inherit the old studies.
            user.deleted_at, user.enabled = time.time(), False
            s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        return dict(ok=True, studies_preserved=True)

    def update_password(user, payload):
        if any(len(value) > 128 for value in payload.values()):
            raise HTTPException(400, 'Пароль должен содержать от 12 до 128 символов.')
        with db.session() as s:
            account = s.scalar(select(User).where(User.id == user['id']).with_for_update())
            if account is None or not account.enabled or account.deleted_at is not None:
                raise HTTPException(401, 'Войдите в систему.')
            if not password_matches(payload['current_password'], account.password_hash):
                raise HTTPException(400, 'Текущий пароль указан неверно.')
            if payload['new_password'] == payload['current_password']:
                raise HTTPException(400, 'Новый пароль должен отличаться от текущего.')
            try:
                account.password_hash = password_hash(payload['new_password'])
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            # Rotate this session and revoke every other device in one transaction.
            s.execute(delete(LoginSession).where(LoginSession.user_id == account.id))
            return login_response(s, account)

    @app.post('/api/me/password')
    async def change_password(request: Request, user=Depends(identity)):
        payload = await account_payload(request, {'current_password', 'new_password'})
        return await asyncio.to_thread(update_password, user, payload)

    @app.post("/api/logout")
    def logout(request: Request, user=Depends(identity)):
        with db.session() as s:
            token = s.get(LoginSession, digest(request.cookies[COOKIE]))
            if token:
                s.delete(token)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/jobs")
    def jobs(user=Depends(identity)):
        with db.session() as s:
            return [
                job_json(j)
                for j in s.scalars(
                    select(Job)
                    .where(Job.user_id == user["id"])
                    .order_by(Job.created.desc())
                    .limit(100)
                )
            ]

    @app.post("/api/jobs", status_code=201)
    async def upload(request: Request, user=Depends(identity)):
        if request.headers.get("content-type", "").split(";")[0] != "application/zip":
            raise HTTPException(415, "Загрузите ZIP-архив как application/zip.")
        filename = Path(
            unquote(request.headers.get("x-file-name", "dicom.zip")).replace("\\", "/")
        ).name[:180]
        if not filename.lower().endswith(".zip"):
            raise HTTPException(415, "Поддерживаются ZIP-архивы.")
        user_root = config.data / "users" / user["id"]
        if await asyncio.to_thread(bytes_used, user_root) >= config.max_user_bytes:
            raise HTTPException(
                409, "Лимит хранения исчерпан. Удалите ненужные задания."
            )
        job_id = new_id()
        with db.session() as s:
            # Serialize quota reservations for this user on PostgreSQL.
            account = s.scalar(select(User).where(User.id == user["id"]).with_for_update())
            if account is None or not account.enabled or account.deleted_at is not None:
                raise HTTPException(401, 'Войдите в систему.')
            total = s.scalar(
                select(func.count()).select_from(Job).where(Job.user_id == user["id"])
            )
            active = s.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.user_id == user["id"],
                    Job.state.in_(["uploading", "queued", "running"]),
                )
            )
            if (
                total >= config.max_total_jobs_per_user
                or active >= config.max_jobs_per_user
            ):
                raise HTTPException(
                    409,
                    "Превышен лимит заданий. Дождитесь обработки или удалите старые.",
                )
            s.add(Job(id=job_id, user_id=user["id"], filename=filename))
        root = config.job_dir(user["id"], job_id)
        root.mkdir(parents=True)
        count, last_heartbeat = 0, time.monotonic()
        try:
            with (root / "upload.part").open("xb") as output:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > config.upload_limit:
                        raise HTTPException(413, "Превышен допустимый размер ZIP.")
                    await asyncio.to_thread(output.write, chunk)
                    if time.monotonic() - last_heartbeat > 15:
                        with db.session() as s:
                            s.execute(
                                update(Job)
                                .where(Job.id == job_id)
                                .values(updated=time.time())
                            )
                        last_heartbeat = time.monotonic()
            if count < 4:
                raise HTTPException(400, "Пустой архив.")
            (root / "upload.part").rename(root / "input.zip")
            with db.session() as s:
                job = s.get(Job, job_id)
                job.state, job.stage, job.upload_bytes, job.updated = (
                    "queued",
                    "В очереди",
                    count,
                    time.time(),
                )
            return {"id": job_id, "state": "queued"}
        except BaseException:
            (root / "upload.part").unlink(missing_ok=True)
            with db.session() as s:
                job = s.get(Job, job_id)
                job.state, job.error, job.updated = (
                    "failed",
                    "Загрузка прервана или превышен лимит.",
                    time.time(),
                )
            raise

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, user=Depends(identity)):
        return job_json(owned(job_id, user))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, user=Depends(identity)):
        owned(job_id, user)
        with db.session() as s:
            job = s.get(Job, job_id)
            if job.state in ("queued", "running"):
                job.cancelled = True
                if job.state == "queued":
                    job.state, job.stage = "cancelled", "Отменено"
            return {"ok": True}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, user=Depends(identity)):
        owned(job_id, user)
        with db.session() as s:
            job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None:
                raise HTTPException(404, "Задание не найдено.")
            if job.state not in TERMINAL:
                raise HTTPException(
                    409, "Сначала дождитесь окончания или отмены задания."
                )
            job.state = "deleting"
        root = config.job_dir(user["id"], job_id)
        try:
            if root.exists():
                shutil.rmtree(root)
        except OSError:
            with db.session() as s:
                job = s.get(Job, job_id)
                job.state, job.error = (
                    "failed",
                    "Не удалось удалить файлы. Повторите удаление или обратитесь к администратору.",
                )
            raise HTTPException(500, "Не удалось удалить файлы задания.")
        with db.session() as s:
            s.delete(s.get(Job, job_id))
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/result")
    def get_result(job_id: str, user=Depends(identity)):
        return public_data(result(job_id, user)[1])

    @app.get("/api/jobs/{job_id}/series/{series_index}/images/{projection}/{index}")
    def image(
        job_id: str,
        series_index: int,
        projection: str,
        index: int,
        annotated: bool = False,
        user=Depends(identity),
    ):
        root, series = series_result(job_id, series_index, user)
        images = series["images"].get(projection, [])
        if not 0 <= index < len(images):
            raise HTTPException(404, "Слой не найден.")
        record = images[index]
        path = record.get("_annotated") if annotated else None
        return FileResponse(
            private_file(root, path or record["_file"]), media_type="image/png"
        )

    def stone_record(series, side, index):
        rows = series["stones"].get(side, [])
        if not 0 <= index < len(rows):
            raise HTTPException(404, "Камень не найден.")
        return rows[index]

    @app.get(
        "/api/jobs/{job_id}/series/{series_index}/stones/{side}/{index}/preview/{kind}"
    )
    def preview(
        job_id: str,
        series_index: int,
        side: str,
        index: int,
        kind: str,
        user=Depends(identity),
    ):
        root, series = series_result(job_id, series_index, user)
        stone = stone_record(series, side, index)
        path = stone.get("_previews", {}).get(kind)
        if not path:
            raise HTTPException(404, "Изображение недоступно.")
        return FileResponse(private_file(root, path), media_type="image/png")

    @app.get("/api/jobs/{job_id}/series/{series_index}/models/{side}/{kind}/{index}")
    def model(
        job_id: str,
        series_index: int,
        side: str,
        kind: str,
        index: int,
        cut: bool = False,
        user=Depends(identity),
    ):
        root, series = series_result(job_id, series_index, user)
        if kind == "kidney":
            path = series.get("_kidney_models", {}).get(side)
        elif kind == "stone":
            stone = stone_record(series, side, index)
            path = stone.get("_cut_model" if cut else "_model")
        else:
            path = None
        if not path:
            raise HTTPException(404, "Модель недоступна.")
        return FileResponse(private_file(root, path), media_type="application/json")

    @app.get("/api/jobs/{job_id}/pdf/{series_index}")
    def pdf(job_id: str, series_index: int, user=Depends(identity)):
        root, series = series_result(job_id, series_index, user)
        if not series.get("_pdf"):
            raise HTTPException(409, "PDF недоступен: расчёт параметров не выполнен.")
        return FileResponse(
            private_file(root, series["_pdf"]),
            media_type="application/pdf",
            filename=f"SUD-report-{series_index + 1}.pdf",
        )

    @app.get("/assets/plotly.min.js")
    def plotly_bundle():
        import plotly

        return FileResponse(
            Path(plotly.__file__).parent / "package_data/plotly.min.js",
            media_type="application/javascript",
        )

    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_ROOT / "static/index.html")

    return app
