"""Self-registration, uniqueness, consent evidence and persisted credentials."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sudweb.api import create_app
from sudweb.config import Settings
from sudweb.db import Database, User
from sudweb.manage import create_user
from sudweb.security import password_matches


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Settings(data=Path(self.temp.name),
            database_url=f'sqlite:///{Path(self.temp.name)/"db.sqlite"}',secure_cookie=False)
        self.db = Database(self.config.database_url); self.db.init()
        self.app = create_app(self.config,self.db)
        self.client = TestClient(self.app)
        self.info = self.client.get('/api/registration').json()

    def tearDown(self):
        self.client.close(); self.db.engine.dispose(); self.temp.cleanup()

    def signup(self,**changes):
        payload = dict(username='doctor',email='doctor@example.org',password='my-self-chosen-password',
            consent=True,consent_version=self.info['consent_version'])
        payload.update(changes)
        return self.client.post('/api/register',json=payload)

    def test_register_then_login_with_saved_password_and_consent_evidence(self):
        response = self.signup(username=' Doctor ',email='Doctor@Example.ORG')
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json(),{'username':'doctor'})
        self.assertNotIn('password',response.text)
        with self.db.session() as s:
            user = s.scalar(select(User).where(User.username=='doctor'))
            self.assertEqual(user.email,'doctor@example.org')
            self.assertTrue(password_matches('my-self-chosen-password',user.password_hash))
            self.assertNotEqual(user.password_hash,'my-self-chosen-password')
            self.assertIsNotNone(user.consent_at)
            self.assertEqual(user.consent_text,self.info['consent_text'])
            self.assertEqual(user.consent_version,self.info['consent_version'])
        with TestClient(create_app(self.config,self.db)) as fresh:
            response = fresh.post('/api/login',json=dict(username='DOCTOR',password='my-self-chosen-password'))
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json()['is_admin'])
            self.assertEqual(fresh.get('/api/admin/users').status_code,403)
            self.assertEqual(fresh.get('/api/jobs').json(),[])

    def test_duplicate_login_case_and_email_rejected_without_overwriting_password(self):
        self.assertEqual(self.signup().status_code,201)
        self.assertEqual(self.signup(username='DOCTOR',email='another@example.org').status_code,409)
        self.assertEqual(self.signup(username='different',email='DOCTOR@EXAMPLE.ORG').status_code,409)
        create_user(self.db,'admin','my-admin-test-password')
        self.assertEqual(self.signup(username='ADMIN',email='admin@example.org').status_code,409)
        with self.db.session() as s:
            self.assertEqual(len(s.scalars(select(User)).all()),2)

    def test_consent_required_and_version_change_must_be_accepted_again(self):
        for value in (False,'true',1,None):
            self.assertEqual(self.signup(consent=value).status_code,400)
        self.assertEqual(self.signup(consent_version='old').status_code,400)
        with self.db.session() as s:
            self.assertEqual(s.scalars(select(User)).all(),[])

    def test_email_password_login_and_privilege_fields_validated(self):
        for changes in (dict(email='not-an-email'),dict(password='short'),dict(username='../doctor'),
                        dict(is_admin=True),dict(email='a@example.org\nBcc:intruder@example.org')):
            self.assertEqual(self.signup(**changes).status_code,400)
        with self.db.session() as s:
            self.assertEqual(s.scalars(select(User)).all(),[])

    def test_admin_name_reserved_even_without_bootstrap_account(self):
        self.assertEqual(self.signup(username='admin').status_code,409)

    def test_concurrent_same_username_or_email_only_creates_one_account(self):
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: self.signup(),range(2)))
        self.assertEqual(sorted(r.status_code for r in responses),[201,409])

    def test_concurrent_different_logins_cannot_claim_same_email(self):
        with ThreadPoolExecutor(2) as pool:
            responses=list(pool.map(lambda name:self.signup(username=name),('doctor1','doctor2')))
        self.assertEqual(sorted(r.status_code for r in responses),[201,409])

    def test_registration_rate_origin_limits_and_disabled_mode(self):
        for _ in range(5):
            self.assertEqual(self.signup(consent=False).status_code,400)
        self.assertEqual(self.signup().status_code,429)
        self.assertEqual(self.client.post('/api/register',json={},headers={'Origin':'https://foreign.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/register',content=b'x'*65537).status_code,413)
        config = replace(self.config,registration=replace(self.config.registration,enabled=False))
        with TestClient(create_app(config,self.db)) as disabled:
            self.assertFalse(disabled.get('/api/registration').json()['enabled'])
            self.assertEqual(disabled.post('/api/register',json={}).status_code,403)

    def test_email_and_consent_schema_upgrade_leaves_existing_accounts_valid(self):
        create_user(self.db,'existing','existing-test-password')
        with self.db.engine.begin() as c:
            # Recreate only an old users table, as SQLite cannot DROP a UNIQUE column.
            c.execute(text('ALTER TABLE users RENAME TO users_backup'))
            c.execute(text('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(80) UNIQUE, password_hash TEXT, enabled BOOLEAN, deleted_at FLOAT)'))
            c.execute(text('INSERT INTO users SELECT id, username, password_hash, enabled, deleted_at FROM users_backup'))
            c.execute(text('DROP TABLE users_backup'))
        self.db.init(); self.db.init()
        with self.db.session() as s:
            existing = s.scalar(select(User).where(User.username=='existing'))
            self.assertIsNone(existing.email)
            self.assertTrue(password_matches('existing-test-password',existing.password_hash))


if __name__=='__main__':
    unittest.main()
