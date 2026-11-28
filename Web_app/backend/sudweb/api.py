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
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, HTTPException, Depends, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, update, delete
from sqlalchemy.exc import IntegrityError
from starlette.middleware.gzip import GZipMiddleware

from .config import Settings, WEB_ROOT
from .db import Database, User, LoginSession, LoginAttempt, Job, new_id
from .security import digest, password_matches, password_hash
from .manage import create_user, UserExists
from . import transport
from .studies import archive_study
from . import registration
from . import recovery
from cryptography.exceptions import InvalidTag

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


def job_json(job, owner=None, config=None):
    data = {
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
            "deleted_by_user_at",
            "archived_at",
            "source_job_id",
        )
    }
    data['display_status'] = ('В архиве' if job.archived_at else
        'Удалено пользователем' if job.deleted_by_user_at else job.stage)
    if owner is not None:
        data['owner'] = owner.username
        data['owner_deleted'] = owner.deleted_at is not None
    if config is not None:
        folder = (config.archive_dir(job.user_id, job.id) if job.archived_at
                  else config.job_dir(job.user_id, job.id))
        data['archive_available'] = (folder / 'input.zip').is_file()
    return data


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


async def account_payload(request, fields, optional=frozenset()):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4096:
            raise HTTPException(413, 'Слишком большой запрос.')
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, 'Некорректные данные учётной записи.')
    if (not isinstance(payload, dict) or not fields <= set(payload) or set(payload) - fields - optional
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
    app.state.transport_key = transport.private_key(config.data)
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.middleware("http")
    async def boundaries(request, call_next):
        limit = (transport.CHUNK_SIZE + 16
                 if '/upload/' in request.url.path and request.method == 'PUT'
                 else 65536)
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
            if user is None or not user.enabled or user.deleted_at is not None or user.password_reset_required:
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
            if job is None or (user['username'] != 'admin' and (
                job.user_id != user['id'] or job.deleted_by_user_at is not None
                or job.archived_at is not None or job.state == 'deleting')):
                raise HTTPException(404, "Задание не найдено.")
            return job

    def result(job_id, user):
        job = owned(job_id, user)
        if job.state != "completed":
            raise HTTPException(409, "Обработка ещё не завершена.")
        root = config.job_dir(job.user_id, job.id)
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
            if user.password_reset_required:
                return JSONResponse(dict(detail='Администратор сбросил пароль. Создайте новый пароль по одноразовой ссылке или запросите её по email.',
                    reset_required=True), status_code=403)
            s.execute(
                update(LoginAttempt).where(LoginAttempt.key == keys[0]).values(count=0)
            )
            return login_response(s, user)

    @app.get('/api/registration')
    def registration_info():
        return registration.description(config)

    @app.post('/api/register', status_code=201)
    async def signup(request: Request):
        if not config.registration.enabled:
            raise HTTPException(403, 'Самостоятельная регистрация отключена администратором.')
        peer = request.client.host if request.client else 'unknown'
        if not await asyncio.to_thread(registration.rate_limit, db, peer):
            raise HTTPException(429, 'Слишком много попыток регистрации. Повторите через час.')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>4096:
                raise HTTPException(413, 'Слишком большой запрос.')
        try:
            payload = json.loads(raw)
            return await asyncio.to_thread(registration.register, db, config, payload)
        except (FileExistsError,IntegrityError):
            raise HTTPException(409, 'Это имя пользователя или email уже используется.')
        except (ValueError,TypeError):
            raise HTTPException(400, 'Проверьте имя, email и пароль (12–128 символов). Подтвердите согласие; если текст изменился, обновите страницу.')

    app.state.dummy_password = password_hash(secrets.token_hex(20))

    @app.post('/api/password/forgot', status_code=202)
    async def forgot_password(request: Request, tasks: BackgroundTasks):
        payload = await account_payload(request, {'email'})
        if not recovery.mail_ready(config):
            raise HTTPException(503, 'Восстановление по email пока не настроено. Обратитесь к администратору.')
        try:
            email = recovery.email_address(payload['email'])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        peer = request.client.host if request.client else 'unknown'
        allowed = await asyncio.to_thread(recovery.rate_limit, db, peer, email)
        # Same response for an unknown address and a limited address: no user enumeration.
        if allowed:
            tasks.add_task(recovery.request_email, db, config, email)
        return dict(message=recovery.GENERIC)

    @app.post('/api/password/reset')
    async def reset_password(request: Request):
        payload = await account_payload(request, {'token', 'password'})
        peer = request.client.host if request.client else 'unknown'
        if not await asyncio.to_thread(recovery.rate_limit, db, peer, '', 'complete'):
            raise HTTPException(429, 'Слишком много попыток. Повторите через час.')
        try:
            return await asyncio.to_thread(recovery.complete, db, payload['token'], payload['password'])
        except ValueError as exc:
            raise HTTPException(400, str(exc))

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
                         email=user.email, consent_at=user.consent_at,
                         reset_required=user.password_reset_required,
                         is_admin=user.username == 'admin', studies=count)
                    for user, count in rows]

    @app.post('/api/admin/users', status_code=201)
    async def add_user(request: Request, admin=Depends(administrator)):
        payload = await account_payload(request, {'username', 'password'}, {'email'})
        if len(payload['username']) > 80 or len(payload['password']) > 128:
            raise HTTPException(400, 'Логин — до 80 символов, пароль — от 12 до 128 символов.')
        try:
            email = recovery.email_address(payload['email']) if payload.get('email') else None
            return await asyncio.to_thread(create_user, db, payload['username'], payload['password'], email=email)
        except UserExists as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except IntegrityError:
            # Concurrent requests can pass the initial lookup together.
            raise HTTPException(409, 'Такой пользователь или email уже существует.')

    @app.patch('/api/admin/users/{user_id}/email')
    async def set_user_email(user_id: str, request: Request, admin=Depends(administrator)):
        payload = await account_payload(request, {'email'})
        try:
            email = recovery.email_address(payload['email'])
            def save():
                with db.session() as s:
                    user = s.scalar(select(User).where(User.id == user_id).with_for_update())
                    if user is None or user.deleted_at is not None:
                        raise HTTPException(404, 'Пользователь не найден.')
                    if user.email != email:
                        user.email = email
                        recovery.invalidate(s, user.id)
                return dict(ok=True)
            return await asyncio.to_thread(save)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except IntegrityError:
            raise HTTPException(409, 'Этот email уже используется.')

    @app.post('/api/admin/users/{user_id}/reset-password')
    def admin_reset_password(user_id: str, admin=Depends(administrator)):
        try:
            with db.session() as s:
                user = s.scalar(select(User).where(User.id == user_id).with_for_update())
                if user is None or user.deleted_at is not None:
                    raise HTTPException(404, 'Пользователь не найден.')
                if user.username == 'admin' or user.id == admin['id']:
                    raise HTTPException(409, 'Для своей учётной записи используйте смену пароля или восстановление по email.')
                if not user.enabled:
                    raise HTTPException(409, 'Учётная запись отключена.')
                link = recovery.issue(s, user, config)
                user.password_reset_required = True
                s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                email = user.email
        except ValueError as exc:
            raise HTTPException(503, str(exc))
        sent = False
        if email and recovery.mail_ready(config):
            try:
                recovery.send(config, email, link)
                sent = True
            except Exception:
                log.warning('Admin password reset email could not be delivered; check SMTP configuration.')
        # Only authenticated admin receives the fallback link, never a public caller.
        return dict(ok=True, email_sent=sent, reset_link=link, expires_in=recovery.TOKEN_SECONDS)

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
            recovery.invalidate(s, user.id)
            s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        return dict(ok=True, studies_preserved=True)

    def update_password(user, payload):
        if any(len(value) > 128 for value in payload.values()):
            raise HTTPException(400, 'Пароль должен содержать от 12 до 128 символов.')
        with db.session() as s:
            account = s.scalar(select(User).where(User.id == user['id']).with_for_update())
            if account is None or not account.enabled or account.deleted_at is not None or account.password_reset_required:
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
            recovery.invalidate(s, account.id)
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
    def jobs(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
             user=Depends(identity)):
        with db.session() as s:
            query = select(Job, User).join(User, Job.user_id == User.id)
            if user['username'] != 'admin':
                query = query.where(Job.user_id == user['id'],
                    Job.deleted_by_user_at.is_(None), Job.archived_at.is_(None),
                    Job.state != 'deleting')
            return [job_json(job, owner if user['username'] == 'admin' else None, config)
                    for job, owner in s.execute(query.order_by(Job.created.desc(), Job.id)
                                              .offset(offset).limit(limit))]

    def reserve(s, user, size):
        account = s.scalar(select(User).where(User.id == user['id']).with_for_update())
        if account is None or not account.enabled or account.deleted_at is not None:
            raise HTTPException(401, 'Войдите в систему.')
        total = s.scalar(select(func.count()).select_from(Job).where(
            Job.user_id == user['id'], Job.archived_at.is_(None)))
        active = s.scalar(select(func.count()).select_from(Job).where(
            Job.user_id == user['id'], Job.state.in_(['uploading', 'queued', 'running'])))
        # Reserve the remaining declared upload size, including concurrent uploads.
        pending = sum(max(0, json.loads(j.upload_envelope)['size'] - j.upload_bytes)
                      for j in s.scalars(select(Job).where(Job.user_id == user['id'],
                          Job.state == 'uploading', Job.upload_envelope.is_not(None))))
        if (total >= config.max_total_jobs_per_user or active >= config.max_jobs_per_user
                or bytes_used(config.data / 'users' / user['id']) + pending + size > config.max_user_bytes):
            raise HTTPException(409, 'Лимит исследований или хранения исчерпан. Дождитесь обработки или обратитесь к администратору для архивирования.')

    @app.get('/api/upload-key')
    def upload_key(user=Depends(identity)):
        return transport.public_key(app.state.transport_key)

    @app.post('/api/jobs', status_code=201)
    async def begin_upload(request: Request, user=Depends(identity)):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, 'Требуется зашифрованная загрузка. Обновите страницу.')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise HTTPException(413, 'Слишком большой запрос.')
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {'filename', 'size', 'wrapped_key', 'nonce'}:
                raise ValueError()
            size = payload['size']
            if type(size) is not int or size < 4:
                raise ValueError()
            if size > config.upload_limit:
                raise HTTPException(413, 'Превышен допустимый размер ZIP.')
            if not isinstance(payload['filename'], str) or len(payload['filename']) > 180:
                raise ValueError()
            filename = Path(payload['filename'].replace('\\', '/')).name
            if not filename.lower().endswith('.zip') or any(ord(c) < 32 for c in filename):
                raise ValueError()
            transport.unb64(payload['nonce'], 8)
            await asyncio.to_thread(transport.unwrap, app.state.transport_key, payload)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'Некорректные параметры зашифрованного архива.')
        job_id = new_id()
        def allocate():
            root = config.job_dir(user['id'], job_id)
            try:
                with db.session() as s:
                    reserve(s, user, size)
                    root.mkdir(parents=True)
                    s.add(Job(id=job_id, user_id=user['id'], filename=filename,
                              upload_envelope=json.dumps(payload)))
            except BaseException:
                if root.exists():
                    shutil.rmtree(root)
                raise
        await asyncio.to_thread(allocate)
        return dict(id=job_id, state='uploading', chunk_size=transport.CHUNK_SIZE)

    @app.put('/api/jobs/{job_id}/upload/{index}')
    async def upload_chunk(job_id: str, index: int, request: Request, user=Depends(identity)):
        owned(job_id, user)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > transport.CHUNK_SIZE + 16:
                raise HTTPException(413, 'Превышен размер блока.')
        def receive():
            with db.session() as s:
                job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
                # Admin can view other uploads but cannot alter their transport.
                if job.user_id != user['id'] or job.deleted_by_user_at is not None:
                    raise HTTPException(404, 'Задание не найдено.')
                if job.state != 'uploading' or not job.upload_envelope:
                    raise HTTPException(409, 'Загрузка уже закрыта.')
                envelope = json.loads(job.upload_envelope)
                remaining = envelope['size'] - job.upload_bytes
                if index < 0 or index != job.upload_bytes // transport.CHUNK_SIZE or remaining <= 0:
                    raise HTTPException(409, 'Неверный порядок блоков.')
                expected = min(transport.CHUNK_SIZE, remaining)
                if len(data) != expected + 16:
                    raise HTTPException(400, 'Неверная длина блока.')
                try:
                    plain = transport.decrypt_chunk(app.state.transport_key, envelope, job_id, index, bytes(data))
                except (InvalidTag, ValueError):
                    raise HTTPException(400, 'Проверка целостности зашифрованного блока не пройдена.')
                path = config.job_dir(job.user_id, job.id) / 'upload.part'
                with path.open('r+b' if path.exists() else 'w+b') as output:
                    output.seek(0, 2)
                    if output.tell() < job.upload_bytes:
                        raise HTTPException(409, 'Загрузка повреждена. Загрузите архив заново.')
                    output.truncate(job.upload_bytes)  # Recover a write before a failed DB commit.
                    output.seek(job.upload_bytes)
                    output.write(plain)
                    output.flush()
                    os.fsync(output.fileno())
                job.upload_bytes += len(plain)
                job.updated = time.time()
                job.progress = min(99, int(100 * job.upload_bytes / envelope['size']))
                return dict(received=job.upload_bytes)
        return await asyncio.to_thread(receive)

    @app.post('/api/jobs/{job_id}/complete', status_code=201)
    def complete_upload(job_id: str, user=Depends(identity)):
        owned(job_id, user)
        with db.session() as s:
            job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.user_id != user['id'] or job.deleted_by_user_at is not None:
                raise HTTPException(404, 'Задание не найдено.')
            if job.state != 'uploading' or not job.upload_envelope:
                raise HTTPException(409, 'Загрузка уже закрыта.')
            if job.upload_bytes != json.loads(job.upload_envelope)['size']:
                raise HTTPException(409, 'Получены не все блоки архива.')
            root = config.job_dir(job.user_id, job.id)
            part, source = root / 'upload.part', root / 'input.zip'
            # A crash after rename but before DB commit is safe to retry.
            path = part if part.exists() else source
            if not path.exists() or path.stat().st_size != job.upload_bytes:
                raise HTTPException(409, 'Загрузка повреждена. Загрузите архив заново.')
            with path.open('rb') as inp:
                if inp.read(4) != b'PK\x03\x04':
                    raise HTTPException(400, 'Полученный файл не является ZIP-архивом.')
            if path == part:
                part.replace(source)
            job.state, job.stage, job.progress = 'queued', 'В очереди', 0
            job.upload_envelope, job.updated = None, time.time()
        return dict(id=job_id, state='queued')

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str, user=Depends(identity)):
        job = owned(job_id, user)
        with db.session() as s:
            return job_json(job, s.get(User, job.user_id) if user['username'] == 'admin' else None, config)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id: str, user=Depends(identity)):
        owned(job_id, user)
        with db.session() as s:
            job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.state in ('queued', 'running', 'uploading'):
                job.cancelled = True
                if job.state in ('queued', 'uploading'):
                    job.state, job.stage = 'cancelled', 'Отменено'
            return dict(ok=True)

    @app.delete('/api/jobs/{job_id}')
    def delete_job(job_id: str, user=Depends(identity)):
        owned(job_id, user)
        with db.session() as s:
            job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if user['username'] != 'admin':
                if job.user_id != user['id'] or job.deleted_by_user_at is not None:
                    raise HTTPException(404, 'Задание не найдено.')
                job.deleted_by_user_at, job.updated = time.time(), time.time()
                return dict(ok=True, hidden=True)
            if job.archived_at:
                return dict(ok=True, archived=True)
            if job.state not in TERMINAL | {'deleting'}:
                raise HTTPException(409, 'Сначала дождитесь окончания или отмены задания.')
            job.state, job.stage = 'deleting', 'Архивирование'
        # Commit the barrier before touching files; worker cannot claim this job.
        # Hold the row lock through cleanup to serialize concurrent admin requests.
        try:
            with db.session() as s:
                job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if not job.archived_at:
                    archive_study(config, job)
                    job.state, job.stage, job.progress = 'archived', 'В архиве', 0
                    job.archived_at, job.updated = time.time(), time.time()
                    job.upload_envelope, job.error = None, None
        except OSError:
            log.exception('Study archival failed for %s', job_id)
            raise HTTPException(500, 'Не удалось завершить архивирование. Проверьте исходный ZIP и свободное место, затем повторите операцию.')
        return dict(ok=True, archived=True)

    @app.get('/api/jobs/{job_id}/archive')
    def download_archive(job_id: str, admin=Depends(administrator)):
        job = owned(job_id, admin)
        folder = (config.archive_dir(job.user_id, job.id) if job.archived_at
                  else config.job_dir(job.user_id, job.id))
        return FileResponse(private_file(folder, 'input.zip'), media_type='application/zip', filename=job.filename)

    @app.post('/api/jobs/{job_id}/reprocess', status_code=201)
    def reprocess(job_id: str, admin=Depends(administrator)):
        # The original owner remains unchanged. A separate new admin-owned study
        # avoids silently restoring a deleted user's study in their workspace.
        with db.session() as s:
            reserve(s, admin, 0)
            job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None:
                raise HTTPException(404, 'Задание не найдено.')
            if job.state not in TERMINAL | {'archived'}:
                raise HTTPException(409, 'Дождитесь окончания обработки.')
            folder = (config.archive_dir(job.user_id, job.id) if job.archived_at
                      else config.job_dir(job.user_id, job.id))
            source = private_file(folder, 'input.zip')
            reserve_size = source.stat().st_size
            if bytes_used(config.data / 'users' / admin['id']) + reserve_size > config.max_user_bytes:
                raise HTTPException(409, 'Недостаточно места для повторного анализа.')
            new_job = Job(id=new_id(), user_id=admin['id'], filename=job.filename,
                source_job_id=job.id, state='queued', stage='В очереди', upload_bytes=reserve_size)
            root = config.job_dir(admin['id'], new_job.id)
            root.mkdir(parents=True)
            try:
                shutil.copyfile(source, root / 'input.zip')
                s.add(new_job)
                s.flush()
            except BaseException:
                shutil.rmtree(root)
                raise
            return dict(id=new_job.id, state='queued')

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
