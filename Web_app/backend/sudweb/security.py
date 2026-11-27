import hashlib
import hmac
import secrets


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password):
    if not 12 <= len(password) <= 128:
        raise ValueError("Пароль должен содержать от 12 до 128 символов.")
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return f"scrypt${salt}${key.hex()}"


def password_matches(password, stored):
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt" or len(password) > 128:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False
