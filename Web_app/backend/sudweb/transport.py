"""WebCrypto-compatible RSA-OAEP/SHA256 + independent AES-256-GCM records.

HTTPS authenticates the server key and protects credentials. Each upload has a
fresh AES key; the 96-bit IV is a random 64-bit prefix plus a 32-bit record index.
AAD binds ciphertext to the server-assigned job UUID, total length and index.
"""
import base64
import os
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHUNK_SIZE = 1024 * 1024


def oaep():
    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def b64(value):
    return base64.b64encode(value).decode('ascii')


def unb64(value, length):
    if not isinstance(value, str) or len(value) > length * 2 + 8:
        raise ValueError('Invalid encrypted upload metadata')
    result = base64.b64decode(value, validate=True)
    if len(result) != length:
        raise ValueError('Invalid encrypted upload metadata')
    return result


def private_key(data):
    folder = Path(data) / 'transport'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / 'private.pem'
    if not target.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        encoded = key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        # Publish a fully written key atomically; concurrent API starts retain
        # the first key rather than returning a key another process overwrites.
        fd, name = tempfile.mkstemp(dir=folder, suffix='.part')
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(name, target)
            except FileExistsError:
                pass
        finally:
            Path(name).unlink(missing_ok=True)
    return serialization.load_pem_private_key(target.read_bytes(), password=None)


def public_key(key):
    return dict(protocol='sud-aesgcm-v1', chunk_size=CHUNK_SIZE,
        spki=b64(key.public_key().public_bytes(serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo)))


def unwrap(key, envelope):
    secret = key.decrypt(unb64(envelope['wrapped_key'], 384), oaep())
    if len(secret) != 32:
        raise ValueError('Invalid AES key')
    return secret


def aad(job_id, size, index):
    return f'sud-aesgcm-v1:{job_id}:{size}:{index}'.encode('ascii')


def decrypt_chunk(key, envelope, job_id, index, data):
    prefix = unb64(envelope['nonce'], 8)
    return AESGCM(unwrap(key, envelope)).decrypt(
        prefix + index.to_bytes(4, 'big'), data, aad(job_id, envelope['size'], index))
