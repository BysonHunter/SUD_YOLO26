"""Configure Yandex Mail in the ignored local .env without echoing its app password."""
import getpass
import os
from pathlib import Path
import re
import tempfile


def main():
    path = Path(__file__).resolve().parent / '.env'
    if not path.is_file():
        raise SystemExit('Run python configure.py first.')
    sender = input('Full Yandex email address: ').strip().lower()
    if not re.fullmatch(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', sender):
        raise SystemExit('Enter a full email address.')
    secret = getpass.getpass('Yandex Mail app password (hidden): ').strip()
    if not re.fullmatch(r'[a-zA-Z0-9]{16,128}', secret):
        raise SystemExit('Use a generated Mail app password, not your Yandex account password.')
    values = dict(SUD_SMTP_HOST='smtp.yandex.ru', SUD_SMTP_PORT='465',
                  SUD_SMTP_USER=sender, SUD_SMTP_PASSWORD=secret, SUD_MAIL_FROM=sender)
    lines = path.read_text(encoding='utf-8').splitlines()
    # Preserve all unrelated settings, including existing DB/admin passwords.
    lines = [line for line in lines if line.split('=', 1)[0] not in values]
    content = '\n'.join(lines + [key+'='+value for key, value in values.items()])+'\n'
    descriptor, temp = tempfile.mkstemp(prefix='.mail-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(content)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print('SMTP settings saved locally. Restart API: docker compose up -d api')


if __name__ == '__main__':
    main()
