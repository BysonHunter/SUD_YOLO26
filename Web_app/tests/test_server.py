"""Run with: PYTHONPATH=backend python -m unittest discover -s tests -v."""

import io
import base64
import json
import stat
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi.testclient import TestClient
from sudweb.api import create_app, public_data
from sudweb.config import Settings
from sudweb.db import Database, Job, User, LoginSession
from sudweb.manage import create_user
from sudweb.archive import unpack_zip
from sudweb.security import password_hash, password_matches
from sqlalchemy import select
from sudweb import transport
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Settings(
            data=self.root,
            database_url=f'sqlite:///{self.root/"db.sqlite"}',
            secure_cookie=False,
            upload_limit=1024,
            max_jobs_per_user=2,
        )
        self.db = Database(self.config.database_url)
        self.db.init()
        create_user(self.db, "alice", "test-password-alice")
        create_user(self.db, "bobby", "test-password-bobby")
        self.app = create_app(self.config, self.db)
        self.client = TestClient(self.app)
        self.other = TestClient(self.app)
        self.csrf = self.login(self.client, "alice", "test-password-alice")
        self.other_csrf = self.login(self.other, "bobby", "test-password-bobby")

    def tearDown(self):
        self.client.close()
        self.other.close()
        self.db.engine.dispose()
        self.temp.cleanup()

    def login(self, client, user, password):
        response = client.post(
            "/api/login", json=dict(username=user, password=password)
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf"]

    def upload(self, client=None, csrf=None, data=b"PK\x03\x04test"):
        client, csrf = client or self.client, csrf or self.csrf
        if not isinstance(data, bytes):
            data = b''.join(data)
        headers = {'X-CSRF-Token': csrf}
        key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(8)
        public = client.get('/api/upload-key').json()
        rsa = serialization.load_der_public_key(base64.b64decode(public['spki']))
        response = client.post('/api/jobs', json=dict(filename='study.zip', size=len(data),
            wrapped_key=transport.b64(rsa.encrypt(key, transport.oaep())), nonce=transport.b64(nonce)), headers=headers)
        if response.status_code != 201:
            return response
        job = response.json()['id']
        for i, offset in enumerate(range(0, len(data), transport.CHUNK_SIZE)):
            encrypted = AESGCM(key).encrypt(nonce+i.to_bytes(4,'big'),
                data[offset:offset+transport.CHUNK_SIZE], transport.aad(job,len(data),i))
            response=client.put(f'/api/jobs/{job}/upload/{i}', content=encrypted, headers=headers)
            if response.status_code != 200:
                return response
        return client.post(f'/api/jobs/{job}/complete', headers=headers)

    def test_static_and_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/assets/plotly.min.js").status_code, 200)

    def test_auth_csrf_origin_and_session_revocation(self):
        self.assertEqual(TestClient(self.app).get("/api/jobs").status_code, 401)
        self.assertEqual(
            self.client.post("/api/jobs", content=b"PKxx").status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/api/logout",
                headers={
                    "X-CSRF-Token": self.csrf,
                    "Origin": "https://foreign.invalid",
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/login", json={"username": "alice", "password": "wrong"}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/logout", headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_upload_limits_and_quota(self):
        self.assertEqual(self.upload(data=b"x" * 1025).status_code, 413)

        def chunks():
            yield b"x" * 800
            yield b"x" * 800

        self.assertEqual(self.upload(data=chunks()).status_code, 413)
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(self.upload().status_code, 409)
        self.assertFalse(list(self.root.rglob("upload.part")))

    def test_login_localhost_and_numeric_loopback(self):
        config = replace(self.config, public_origin="http://localhost:8080")
        app = create_app(config, self.db)
        for host in ("localhost", "127.0.0.1"):
            origin = f"http://{host}:8080"
            with TestClient(app, base_url=origin) as client:
                response = client.post(
                    "/api/login",
                    headers={"Origin": origin},
                    json={"username": "alice", "password": "test-password-alice"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(client.get("/api/me").status_code, 200)
                self.assertEqual(
                    client.post(
                        "/api/logout",
                        headers={
                            "Origin": origin,
                            "X-CSRF-Token": response.json()["csrf"],
                        },
                    ).status_code,
                    200,
                )

    def test_loopback_alias_does_not_allow_other_ports_or_remote_origins(self):
        from sudweb.api import origin_allowed

        expected = "http://localhost:8080"
        request_origin = "http://127.0.0.1:8080"
        for origin in (
            "http://localhost:8081",
            "https://localhost:8080",
            "http://localhost.evil:8080",
            "null",
            "http://localhost:invalid",
            "http://user@localhost:8080",
            "http://localhost:8080/path",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(origin_allowed(origin, expected, request_origin))
        self.assertFalse(
            origin_allowed(request_origin, "https://sud.example.org", request_origin)
        )
        config = replace(self.config, public_origin=expected)
        with TestClient(create_app(config, self.db), base_url=request_origin) as client:
            response = client.post(
                "/api/login",
                headers={"Origin": "http://localhost:8081"},
                json={"username": "alice", "password": "test-password-alice"},
            )
            self.assertEqual(response.status_code, 403)

    def test_ownership_for_every_result_resource(self):
        job = self.upload().json()["id"]
        with self.db.session() as s:
            row = s.get(Job, job)
            row.state = "completed"
            user_id = row.user_id
        root = self.config.job_dir(user_id, job)
        (root / "image.png").write_bytes(b"image")
        (root / "report.pdf").write_bytes(b"%PDF-1.4")
        (root / "mesh.json").write_text('{"data":[]}', encoding="utf-8")
        manifest = dict(
            series=[
                dict(
                    identity={},
                    images={"coronal": [{"_file": "image.png"}]},
                    stones={
                        "right": [
                            {"_previews": {"hu": "image.png"}, "_model": "mesh.json"}
                        ]
                    },
                    _kidney_models={"right": "mesh.json"},
                    _pdf="report.pdf",
                )
            ]
        )
        (root / "result.json").write_text(json.dumps(manifest), encoding="utf-8")
        paths = [
            "",
            "/result",
            "/pdf/0",
            "/series/0/images/coronal/0",
            "/series/0/stones/right/0/preview/hu",
            "/series/0/models/right/stone/0",
            "/series/0/models/right/kidney/0",
        ]
        for path in paths:
            url = "/api/jobs/" + job + path
            self.assertEqual(self.client.get(url).status_code, 200, url)
            self.assertEqual(self.other.get(url).status_code, 404, url)
        self.assertNotIn("_file", self.client.get("/api/jobs/" + job + "/result").text)
        self.assertEqual(
            self.other.post(
                "/api/jobs/" + job + "/cancel",
                headers={"X-CSRF-Token": self.other_csrf},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.other.delete(
                "/api/jobs/" + job, headers={"X-CSRF-Token": self.other_csrf}
            ).status_code,
            404,
        )
        self.assertEqual(len(self.other.get("/api/jobs").json()), 0)
        self.assertEqual(
            self.client.delete(
                "/api/jobs/" + job, headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            200,
        )
        self.assertTrue(root.exists())
        self.assertEqual(self.client.get('/api/jobs/'+job).status_code, 404)
        self.assertEqual(self.client.get('/api/jobs').json(), [])

    def test_cancel_lease_and_atomic_claim(self):
        ids = [self.upload().json()["id"] for _ in range(2)]
        with ThreadPoolExecutor(2) as pool:
            records = list(pool.map(lambda _: self.db.claim(90), range(2)))
        claimed = [r["id"] for r in records if r]
        self.assertEqual(len(set(claimed)), len(claimed))
        # A losing SQLite CAS may return no record; the next poll claims the other.
        while next_job := self.db.claim(90):
            claimed.append(next_job["id"])
        self.assertCountEqual(claimed, ids)
        self.assertEqual(
            self.client.post(
                "/api/jobs/" + ids[0] + "/cancel", headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            200,
        )
        with self.db.session() as s:
            self.assertTrue(s.get(Job, ids[0]).cancelled)
            s.get(Job, ids[1]).lease_until = time.time() - 1
        self.db.expire_leases()
        with self.db.session() as s:
            self.assertEqual(s.get(Job, ids[1]).state, "failed")

    def test_disabled_user_and_expired_session(self):
        with self.db.session() as s:
            user = s.scalar(select(User).where(User.username == "alice"))
            user.enabled = False
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        with self.db.session() as s:
            for session in s.scalars(select(LoginSession)):
                session.expires = time.time() - 1
        self.assertEqual(self.other.get("/api/me").status_code, 401)


class ArchiveTests(unittest.TestCase):
    def unpack(self, names, limit=1024, file_limit=10):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "in.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for name, content in names:
                    z.writestr(name, content)
            return unpack_zip(archive, root / "out", limit, file_limit)

    def test_preserves_dicomdir_layout(self):
        self.assertEqual(
            self.unpack([("DICOMDIR", b"index"), ("CT/SERIES/001", b"dicom")]), 10
        )

    def test_paths_duplicates_symlinks_limits(self):
        for name in [
            "../escape",
            "/escape",
            "C:/escape",
            "CT/../../escape",
            "CT\\..\\escape",
            "CON",
            "CT/file.",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.unpack([(name, b"x")])
        with self.assertRaises(ValueError):
            self.unpack([("A", b"x"), ("a", b"y")])
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            self.unpack([(info, b"../outside")])
        with self.assertRaises(ValueError):
            self.unpack([("hu", b"x" * 1025)])
        with self.assertRaises(ValueError):
            self.unpack([("a", b"a"), ("b", b"b")], file_limit=1)

    def test_password_and_public_manifest(self):
        hashed = password_hash("a-password-with-length")
        self.assertTrue(password_matches("a-password-with-length", hashed))
        self.assertFalse(password_matches("wrong", hashed))
        self.assertEqual(
            public_data({"_path": "private", "a": [{"_x": 1, "visible": 2}]}),
            {"a": [{"visible": 2}]},
        )


if __name__ == "__main__":
    unittest.main()
