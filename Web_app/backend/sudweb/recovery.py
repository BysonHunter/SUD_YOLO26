"""Single-use password recovery; SMTP credentials and raw tokens never enter the DB/logs."""
import logging
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from urllib.parse import urlsplit

from email_validator import validate_email, EmailNotValidError
from sqlalchemy import select, delete

from .db import User, LoginSession, LoginAttempt, PasswordReset
from .security import digest, password_hash, password_matches

log = logging.getLogger(__name__)
TOKEN_SECONDS = 1800
GENERIC = 'Если указанный email зарегистрирован, на него придёт ссылка для создания нового пароля. Проверьте также папку «Спам».'


def email_address(value):
    try:
        if not isinstance(value, str) or len(value) > 254:
            raise ValueError()
        return validate_email(value.strip(), check_deliverability=False,
            allow_smtputf8=False).normalized.lower()
    except (ValueError, EmailNotValidError):
        raise ValueError('Укажите корректный адрес электронной почты.')


def reset_origin(config):
    origin = config.public_origin
    url = urlsplit(origin)
    if (not url.hostname or url.username or url.password or url.path or url.query or url.fragment
        or not (url.scheme == 'https' or
            (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}))):
        raise ValueError('Настройте SUD_PUBLIC_ORIGIN: HTTPS-адрес сайта без завершающего пути.')
    return origin


def mail_ready(config):
    try:
        reset_origin(config)
        email_address(config.mail.sender)
        return bool(config.mail.host and config.mail.username and config.mail.password
                    and config.mail.port in {465, 587})
    except ValueError:
        return False


def rate_limit(db, peer, email='', action='request'):
    """Per-peer and per-address limits also apply to unknown addresses."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.engine.dialect.name == 'postgresql' else sqlite_insert
    limits = [(digest('reset-'+action+'-ip:'+peer), 30)]
    if email:
        limits.append((digest('reset-email:'+email), 5))
    allowed = True
    with db.session() as s:
        now = time.time()
        for key, limit in limits:
            s.execute(insert(LoginAttempt).values(key=key, count=0, since=now)
                .on_conflict_do_nothing(index_elements=['key']))
            attempt = s.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
            if now - attempt.since > 3600:
                attempt.since, attempt.count = now, 0
            if attempt.count >= limit:
                allowed = False
            else:
                attempt.count += 1
    return allowed


def invalidate(session, user_id):
    session.execute(delete(PasswordReset).where(PasswordReset.user_id == user_id))


def issue(session, user, config):
    # The caller locks the account before replacing its previous tokens.
    token = secrets.token_urlsafe(32)
    origin = reset_origin(config)
    invalidate(session, user.id)
    session.add(PasswordReset(token_hash=digest(token), user_id=user.id,
                              expires=time.time() + TOKEN_SECONDS))
    # Fragment is not transmitted in GET requests or HTTP access logs.
    return origin + '/#reset=' + token


def send(config, recipient, link):
    if not mail_ready(config):
        raise RuntimeError('Почтовая отправка не настроена.')
    message = EmailMessage()
    message['Subject'] = 'SUD YOLO26 — создание нового пароля'
    message['From'] = config.mail.sender
    message['To'] = recipient
    message.set_content('Для создания нового пароля откройте ссылку:\n\n' + link
        + '\n\nСсылка одноразовая и действует 30 минут. Если вы не запрашивали '
        'восстановление, проигнорируйте письмо. Пароли в письмах не передаются.\n')
    context = ssl.create_default_context()
    if config.mail.port == 465:
        connection = smtplib.SMTP_SSL(config.mail.host, 465, timeout=15, context=context)
    else:
        connection = smtplib.SMTP(config.mail.host, 587, timeout=15)
    with connection as smtp:
        if config.mail.port == 587:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config.mail.username, config.mail.password)
        smtp.send_message(message)


def request_email(db, config, email):
    """Background task: public response never discloses lookup/delivery results."""
    try:
        with db.session() as s:
            user = s.scalar(select(User).where(User.email == email).with_for_update())
            if user is None or not user.enabled or user.deleted_at is not None:
                return
            link = issue(s, user, config)
        send(config, email, link)
    except Exception:
        # Never include SMTP exceptions (addresses/authentication data) or links.
        log.warning('Password recovery email could not be delivered; check SMTP configuration.')


def complete(db, token, password):
    if not 40 <= len(token) <= 100:
        raise ValueError('Ссылка недействительна или истекла. Запросите новую.')
    hashed = password_hash(password)
    with db.session() as s:
        user_id = s.scalar(select(PasswordReset.user_id).where(PasswordReset.token_hash == digest(token)))
        user = s.scalar(select(User).where(User.id == user_id).with_for_update()) if user_id else None
        if user is None or not user.enabled or user.deleted_at is not None:
            raise ValueError('Ссылка недействительна или истекла. Запросите новую.')
        if password_matches(password, user.password_hash):
            raise ValueError('Новый пароль должен отличаться от предыдущего.')
        # Atomic consumption is required even on SQLite, where FOR UPDATE is ignored.
        claimed = s.execute(delete(PasswordReset).where(PasswordReset.token_hash == digest(token),
            PasswordReset.user_id == user.id, PasswordReset.expires > time.time()))
        if claimed.rowcount != 1:
            raise ValueError('Ссылка недействительна или истекла. Запросите новую.')
        user.password_hash = hashed
        user.password_reset_required = False
        invalidate(s, user.id)
        s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        s.execute(delete(LoginAttempt).where(LoginAttempt.key == digest('user:'+user.username)))
    return dict(ok=True)
