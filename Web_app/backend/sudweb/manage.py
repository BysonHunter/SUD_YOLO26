"""Administration through the server console; no public registration endpoint."""

import argparse
import getpass
import os
import re
import secrets
import time
from sqlalchemy import select, delete
from .config import Settings
from .db import Database, User, LoginSession, LoginAttempt
from .security import password_hash


class UserExists(ValueError):
    pass


def create_user(db, username, password, replace=False):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,79}", username):
        raise ValueError("Логин: от 3 до 80 латинских букв, цифр или символов _.@-")
    hashed = password_hash(password)
    with db.session() as s:
        user = s.scalar(select(User).where(User.username == username))
        if user:
            if user.deleted_at is not None:
                raise UserExists('Этот логин принадлежит удалённой учётной записи. Выберите другой логин.')
            if not replace:
                raise UserExists("Такой пользователь уже существует.")
            user.password_hash, user.enabled = hashed, True
            s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        else:
            user = User(username=username, password_hash=hashed)
            s.add(user)
        s.flush()
        return dict(id=user.id, username=user.username, enabled=user.enabled)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["init", "user", "password", "disable", "sessions-prune"]
    )
    parser.add_argument("username", nargs="?")
    args = parser.parse_args()
    config = Settings.from_env()
    config.data.mkdir(parents=True, exist_ok=True)
    db = Database(config.database_url)
    if args.command == "init":
        db.init()
        username, password = os.getenv("SUD_BOOTSTRAP_USER"), os.getenv(
            "SUD_BOOTSTRAP_PASSWORD"
        )
        if username and password:
            with db.session() as s:
                existing = s.scalar(
                    select(User).where(User.username == username.lower())
                )
            if existing is None:
                create_user(db, username, password)
        print("Database initialized. Existing passwords are not changed.")
    elif args.command in ("user", "password"):
        if not args.username:
            parser.error("Укажите логин.")
        create_user(
            db,
            args.username,
            getpass.getpass("Новый пароль (не менее 12 символов): "),
            replace=args.command == "password",
        )
        print("User updated.")
    elif args.command == "disable":
        with db.session() as s:
            user = s.scalar(select(User).where(User.username == args.username))
            if user:
                user.enabled = False
                s.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    else:
        with db.session() as s:
            s.execute(delete(LoginSession).where(LoginSession.expires < time.time()))
            s.execute(
                delete(LoginAttempt).where(LoginAttempt.since < time.time() - 86400)
            )


if __name__ == "__main__":
    main()
