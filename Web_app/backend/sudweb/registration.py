"""Self-registration with explicit versioned consent; never grants admin rights."""
import hashlib
import re
import time
from urllib.parse import urlsplit

from email_validator import validate_email, EmailNotValidError
from sqlalchemy import select
from .db import User, LoginAttempt
from .security import digest, password_hash


def description(config):
    options = config.registration
    if options.policy_url and urlsplit(options.policy_url).scheme != 'https':
        raise ValueError('SUD_PRIVACY_POLICY_URL must be an HTTPS URL')
    text = options.consent_text.strip()
    return dict(enabled=options.enabled and bool(text), consent_text=text,
        policy_url=options.policy_url, consent_version=hashlib.sha256(
            (text+'\n'+options.policy_url).encode('utf-8')).hexdigest())


def rate_limit(db, peer):
    """Five attempts/hour/direct peer; proxy deployments should also rate-limit."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.engine.dialect.name == 'postgresql' else sqlite_insert
    key = digest('registration-ip:'+peer)
    with db.session() as s:
        now = time.time()
        s.execute(insert(LoginAttempt).values(key=key,count=0,since=now)
                  .on_conflict_do_nothing(index_elements=['key']))
        attempt = s.scalar(select(LoginAttempt).where(LoginAttempt.key==key).with_for_update())
        if now-attempt.since > 3600:
            attempt.since,attempt.count = now,0
        if attempt.count >= 5:
            return False
        attempt.count += 1
        return True


def register(db, config, payload):
    info = description(config)
    if not info['enabled']:
        raise ValueError('Самостоятельная регистрация отключена администратором.')
    fields = {'username','email','password','consent','consent_version'}
    if (not isinstance(payload,dict) or set(payload)!=fields
            or payload['consent'] is not True
            or any(not isinstance(payload[k],str) for k in fields-{'consent'})):
        raise ValueError('Заполните поля и подтвердите согласие на обработку персональных данных.')
    if payload['consent_version'] != info['consent_version']:
        raise ValueError('Текст согласия обновился. Обновите страницу и ознакомьтесь с ним повторно.')
    username = payload['username'].strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.@-]{2,79}',username):
        raise ValueError('Имя пользователя: 3–80 латинских букв, цифр или символов _.@-, первый символ — буква или цифра.')
    if username == 'admin':
        raise FileExistsError('Имя пользователя зарезервировано.')
    try:
        if len(payload['email'])>254:
            raise ValueError()
        email = validate_email(payload['email'].strip(), check_deliverability=False,
                               allow_smtputf8=False).normalized.lower()
    except (EmailNotValidError,ValueError):
        raise ValueError('Укажите корректный адрес электронной почты.')
    hashed = password_hash(payload['password'])
    with db.session() as s:
        if s.scalar(select(User.id).where((User.username==username)|(User.email==email))):
            raise FileExistsError('Это имя пользователя или email уже используется.')
        user = User(username=username,email=email,password_hash=hashed,enabled=True,
            consent_at=time.time(),consent_text=info['consent_text'],
            consent_policy_url=info['policy_url'],consent_version=info['consent_version'])
        s.add(user); s.flush()
        return dict(username=user.username)
