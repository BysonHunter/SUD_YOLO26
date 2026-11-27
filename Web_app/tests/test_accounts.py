"""Account lifecycle, authority boundaries, password persistence and upgrades."""
import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, text, inspect
from sudweb.api import create_app
from sudweb.config import Settings
from sudweb.db import Database, User, Job, LoginSession
from sudweb.manage import create_user, main as manage_main
from sudweb.security import password_matches, password_hash


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Settings(data=self.root, database_url=f'sqlite:///{self.root/"test.sqlite"}', secure_cookie=False)
        self.db = Database(self.config.database_url)
        self.db.init()
        self.admin = create_user(self.db, 'admin', 'admin-test-password')
        self.alice = create_user(self.db, 'alice', 'alice-test-password')
        self.app = create_app(self.config, self.db)
        self.client = TestClient(self.app)
        self.user = TestClient(self.app)
        self.admin_csrf = self.login(self.client, 'admin', 'admin-test-password')['csrf']
        self.user_csrf = self.login(self.user, 'alice', 'alice-test-password')['csrf']

    def tearDown(self):
        self.client.close()
        self.user.close()
        self.db.engine.dispose()
        self.temp.cleanup()

    def login(self, client, username, password):
        response = client.post('/api/login', json=dict(username=username, password=password))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def post_user(self, username='doctor', password='doctor-test-password', **extra):
        return self.client.post('/api/admin/users', json=dict(username=username, password=password, **extra),
                                headers={'X-CSRF-Token': self.admin_csrf})

    def test_admin_only_and_csrf_on_all_account_mutations(self):
        self.assertTrue(self.client.get('/api/me').json()['is_admin'])
        self.assertFalse(self.user.get('/api/me').json()['is_admin'])
        for client, expected in [(TestClient(self.app), 401), (self.user, 403)]:
            try:
                headers={'X-CSRF-Token': self.user_csrf}
                self.assertEqual(client.get('/api/admin/users').status_code, expected)
                self.assertEqual(client.post('/api/admin/users', json={}, headers=headers).status_code, expected)
                self.assertEqual(client.delete('/api/admin/users/'+self.alice['id'], headers=headers).status_code, expected)
            finally:
                if client is not self.user:
                    client.close()
        self.assertEqual(self.client.post('/api/admin/users', json={}).status_code, 403)
        self.assertEqual(self.client.delete('/api/admin/users/'+self.alice['id']).status_code, 403)
        self.assertEqual(self.user.post('/api/me/password', json={}).status_code, 403)

    def test_creation_validation_no_password_disclosure_and_duplicate(self):
        response = self.post_user(' Doctor ')
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['username'], 'doctor')
        self.assertNotIn('password', response.text)
        self.assertEqual(self.post_user('DOCTOR').status_code, 409)
        for username, password in [('aa', 'valid-test-password'), ('../bad', 'valid-test-password'),
                                   ('doctor2', 'short'), ('doctor2','x'*129)]:
            self.assertEqual(self.post_user(username,password).status_code, 400)
        self.assertEqual(self.post_user(is_admin=True).status_code, 400)
        self.assertEqual(self.post_user(username=123).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/users', json=[],
            headers={'X-CSRF-Token':self.admin_csrf}).status_code, 400)
        self.assertEqual(self.client.post('/api/admin/users', content=b'x'*4097,
            headers={'X-CSRF-Token':self.admin_csrf}).status_code, 413)
        listing=self.client.get('/api/admin/users').json()
        self.assertNotIn('password', json.dumps(listing))
        with self.db.session() as s:
            stored=s.scalar(select(User).where(User.username=='doctor')).password_hash
        self.assertNotEqual(stored,'doctor-test-password')
        self.assertTrue(password_matches('doctor-test-password',stored))
        with TestClient(self.app) as doctor:
            self.assertFalse(self.login(doctor,'doctor','doctor-test-password')['is_admin'])

    def test_concurrent_creation_has_one_account(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.post_user('doctor'),range(2)))
        self.assertEqual(sorted(r.status_code for r in responses),[201,409])

    def test_deletion_revokes_sessions_keeps_studies_and_reserves_login(self):
        with self.db.session() as s:
            job=Job(user_id=self.alice['id'],filename='private.zip',state='completed')
            s.add(job);s.flush();job_id=job.id
        folder=self.config.job_dir(self.alice['id'],job_id)
        folder.mkdir(parents=True)
        source=folder/'input.zip';source.write_bytes(b'original private data')
        listing=self.client.get('/api/admin/users').json()
        self.assertEqual(next(u for u in listing if u['username']=='alice')['studies'],1)
        response=self.client.delete('/api/admin/users/'+self.alice['id'],headers={'X-CSRF-Token':self.admin_csrf})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['studies_preserved'])
        self.assertEqual(self.user.get('/api/me').status_code,401)
        self.assertEqual(self.user.get('/api/jobs/'+job_id).status_code,401)
        self.assertEqual(self.user.post('/api/login',json=dict(username='alice',password='alice-test-password')).status_code,401)
        self.assertFalse(any(u['username']=='alice' for u in self.client.get('/api/admin/users').json()))
        self.assertEqual(self.post_user('alice').status_code,409)
        with self.db.session() as s:
            self.assertIsNotNone(s.get(User,self.alice['id']).deleted_at)
            self.assertIsNotNone(s.get(Job,job_id))
            self.assertEqual(s.scalars(select(LoginSession).where(LoginSession.user_id==self.alice['id'])).all(),[])
        self.assertEqual(source.read_bytes(),b'original private data')
        self.assertEqual(self.client.get('/api/jobs/'+job_id).status_code,404)

    def test_admin_cannot_delete_itself_and_missing_user_is_404(self):
        headers={'X-CSRF-Token':self.admin_csrf}
        self.assertEqual(self.client.delete('/api/admin/users/'+self.admin['id'],headers=headers).status_code,409)
        self.assertEqual(self.client.delete('/api/admin/users/missing',headers=headers).status_code,404)
        self.assertEqual(self.client.get('/api/me').status_code,200)

    def test_password_change_persists_rotates_session_and_revokes_other_devices(self):
        other=TestClient(self.app)
        try:
            self.login(other,'alice','alice-test-password')
            old_cookie=self.user.cookies.get('sud_session')
            response=self.user.post('/api/me/password',json=dict(current_password='alice-test-password',new_password='my-new-test-password'),
                                    headers={'X-CSRF-Token':self.user_csrf})
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotEqual(response.json()['csrf'],self.user_csrf)
            self.assertNotEqual(self.user.cookies.get('sud_session'),old_cookie)
            self.assertEqual(self.user.get('/api/me').status_code,200)
            self.assertEqual(other.get('/api/me').status_code,401)
            self.assertEqual(self.user.post('/api/logout',headers={'X-CSRF-Token':self.user_csrf}).status_code,403)
            self.assertEqual(other.post('/api/login',json=dict(username='alice',password='alice-test-password')).status_code,401)
            # A separate engine/app proves that the new secret is stored in the DB.
            fresh_db=Database(self.config.database_url)
            fresh_db.init()
            try:
                with TestClient(create_app(self.config,fresh_db)) as fresh:
                    self.login(fresh,'alice','my-new-test-password')
                with fresh_db.session() as s:
                    stored=s.get(User,self.alice['id']).password_hash
                    self.assertTrue(password_matches('my-new-test-password',stored))
                    self.assertNotIn('my-new-test-password',stored)
            finally:
                fresh_db.engine.dispose()
        finally:
            other.close()

    def test_password_errors_do_not_change_account_and_admin_password_survives_init(self):
        for current,new in [('wrong-password','new-valid-password'),('alice-test-password','short'),
                            ('alice-test-password','alice-test-password'),('alice-test-password','x'*129)]:
            response=self.user.post('/api/me/password',json=dict(current_password=current,new_password=new),
                                    headers={'X-CSRF-Token':self.user_csrf})
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.user.get('/api/me').status_code,200)
        response=self.client.post('/api/me/password',json=dict(current_password='admin-test-password',new_password='admin-user-chosen-password'),
                                  headers={'X-CSRF-Token':self.admin_csrf})
        self.assertEqual(response.status_code,200)
        with patch('sudweb.manage.Settings.from_env',return_value=self.config), \
             patch('sudweb.manage.Database',return_value=self.db), \
             patch('sys.argv',['manage','init']), \
             patch.dict('os.environ',{'SUD_BOOTSTRAP_USER':'admin','SUD_BOOTSTRAP_PASSWORD':'bootstrap-old-password'}):
            manage_main()
        with self.db.session() as s:
            stored=s.get(User,self.admin['id']).password_hash
            self.assertTrue(password_matches('admin-user-chosen-password',stored))
            self.assertFalse(password_matches('bootstrap-old-password',stored))


class UpgradeTests(unittest.TestCase):
    def test_old_database_upgrade_is_idempotent_and_preserves_existing_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Database(f'sqlite:///{Path(temp)/"old.sqlite"}')
            hashed=password_hash('existing-test-password')
            with db.engine.begin() as c:
                c.execute(text('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(80) UNIQUE, password_hash TEXT, enabled BOOLEAN)'))
                c.execute(text('INSERT INTO users VALUES (:id,:name,:hash,1)'),dict(id='old-id',name='admin',hash=hashed))
            try:
                db.init();db.init()
                with db.session() as s:
                    admin=s.get(User,'old-id')
                    self.assertEqual(admin.password_hash,hashed)
                    self.assertIsNone(admin.deleted_at)
            finally:
                db.engine.dispose()
