"""Create local deployment secrets once; never overwrite an existing configuration."""

import os
from pathlib import Path
import secrets


def main():
    target = Path(__file__).resolve().parent / ".env"
    if target.exists():
        raise SystemExit(
            ".env already exists; edit it explicitly. No settings were changed."
        )
    password = secrets.token_urlsafe(24)
    content = (
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n"
        "SUD_BOOTSTRAP_USER=admin\n"
        f"SUD_BOOTSTRAP_PASSWORD={password}\n"
        "SUD_BIND=127.0.0.1\nSUD_PORT=8080\n"
        "SUD_PUBLIC_ORIGIN=http://localhost:8080\nSUD_SECURE_COOKIE=false\n"
        "SUD_REGISTRATION_ENABLED=true\nSUD_PRIVACY_POLICY_URL=\n"
        "SUD_SMTP_HOST=smtp.yandex.ru\nSUD_SMTP_PORT=465\n"
        "SUD_SMTP_USER=\nSUD_SMTP_PASSWORD=\nSUD_MAIL_FROM=\n"
    )
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    print("Created .env. Local URL: http://localhost:8080")
    print("Login: admin. Password is in SUD_BOOTSTRAP_PASSWORD in .env.")
    print(
        "For remote access configure HTTPS, SUD_PUBLIC_ORIGIN and SUD_SECURE_COOKIE=true."
    )


if __name__ == "__main__":
    main()
