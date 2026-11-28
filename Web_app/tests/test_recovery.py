"""Password recovery boundaries, atomic token consumption, and TLS mail delivery."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from sudweb import recovery
from sudweb.api import create_app
from sudweb.config import Settings, MailSettings
from sudweb.db import Database, User, PasswordReset
from sudweb.manage import create_user
from sudweb.security import digest, password_matches


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = Settings(data=root, database_url=f'sqlite:///{root/"test.sqlite"}',
            secure_cookie=False, public_origin='http://testserver')
        # Valid canonical HTTPS origin is required for links; requests have no foreign Origin.
        self.config = replace(self.config, public_origin='https://sud.example.org',
            mail=MailSettings(username='sender@yandex.ru', sender='sender@yandex.ru', password='fake-app-secret'))
        self.db = Database(self.config.database_url)
        self.db.init()
        self.admin = create_user(self.db, 'admin', 'admin-test-password')
        self.alice = create_user(self.db, 'alice', 'alice-test-password', email='alice@example.org')
        self.app = create_app(self.config, self.db)
        self.admin_client, self.user = TestClient(self.app), TestClient(self.app)
        self.headers = {'X-CSRF-Token':self.login(self.admin_client, 'admin', 'admin-test-password')['csrf']}
        self.user_headers = {'X-CSRF-Token':self.login(self.user, 'alice', 'alice-test-password')['csrf']}

    def tearDown(self):
        self.admin_client.close(); self.user.close()
        self.db.engine.dispose(); self.temp.cleanup()

    def login(self, client, username, password):
        response = client.post('/api/login', json=dict(username=username, password=password))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def admin_reset(self):
        with patch('sudweb.recovery.send') as send:
            response = self.admin_client.post('/api/admin/users/'+self.alice['id']+'/reset-password', headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(send.call_count, 1)
        return response.json()['reset_link'].split('#reset=')[1]

    def test_admin_reset_revokes_sessions_requires_new_password_and_persists(self):
        token = self.admin_reset()
        self.assertEqual(self.user.get('/api/me').status_code, 401)
        response = self.user.post('/api/login', json=dict(username='alice', password='alice-test-password'))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()['reset_required'])
        self.assertEqual(self.user.get('/api/jobs').status_code, 401)
        rows = self.admin_client.get('/api/admin/users').json()
        alice = next(row for row in rows if row['username']=='alice')
        self.assertEqual(alice['email'], 'alice@example.org')
        self.assertTrue(alice['reset_required'])
        with self.db.session() as s:
            self.assertIsNotNone(s.get(PasswordReset, digest(token)))
            self.assertIsNone(s.get(PasswordReset, token))
        payload = dict(token=token, password='alice-new-password')
        self.assertEqual(self.user.post('/api/password/reset', json=payload).status_code, 200)
        self.assertEqual(self.user.post('/api/password/reset', json=payload).status_code, 400)
        self.assertEqual(self.user.post('/api/login', json=dict(username='alice',password='alice-test-password')).status_code,401)
        fresh = Database(self.config.database_url)
        try:
            fresh.init()
            with fresh.session() as s:
                account = s.get(User,self.alice['id'])
                self.assertFalse(account.password_reset_required)
                self.assertTrue(password_matches('alice-new-password', account.password_hash))
        finally:
            fresh.engine.dispose()
        self.login(self.user,'alice','alice-new-password')

    def test_only_admin_and_csrf_can_reset_and_admin_is_protected(self):
        path = '/api/admin/users/'+self.alice['id']+'/reset-password'
        self.assertEqual(self.user.post(path,headers=self.user_headers).status_code,403)
        self.assertEqual(self.admin_client.post(path).status_code,403)
        self.assertEqual(self.admin_client.post(path,headers={**self.headers,'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.admin_client.post('/api/admin/users/'+self.admin['id']+'/reset-password',headers=self.headers).status_code,409)
        self.assertEqual(self.admin_client.post('/api/admin/users/missing/reset-password',headers=self.headers).status_code,404)
        self.assertEqual(self.user.get('/api/me').status_code,200)

    def test_forgot_uniform_response_no_session_change_and_no_tokens_in_response(self):
        with patch('sudweb.recovery.send') as send:
            good = self.user.post('/api/password/forgot',json={'email':'ALICE@example.org'})
            bad = self.user.post('/api/password/forgot',json={'email':'missing@example.org'})
            self.assertEqual(good.status_code,202)
            self.assertEqual(good.json(),bad.json())
            self.assertEqual(send.call_count,1)
            link = send.call_args.args[2]
            self.assertTrue(link.startswith('https://sud.example.org/#reset='))
            self.assertNotIn(link,good.text)
        self.assertEqual(self.user.get('/api/me').status_code,200)
        token = link.split('#reset=')[1]
        recovery.complete(self.db,token,'recovered-password')
        self.assertEqual(self.user.get('/api/me').status_code,401)

    def test_email_rate_limits_and_disabled_deleted_accounts_send_nothing(self):
        with patch('sudweb.recovery.send') as send:
            for _ in range(7):
                self.assertEqual(self.user.post('/api/password/forgot',json={'email':'alice@example.org'}).status_code,202)
            self.assertEqual(send.call_count,5)
            with self.db.session() as s:
                s.get(User,self.alice['id']).enabled=False
            recovery.request_email(self.db,self.config,'alice@example.org')
            self.assertEqual(send.call_count,5)
            with self.db.session() as s:
                user = s.get(User,self.alice['id']); user.enabled=True;user.deleted_at=time.time()
            recovery.request_email(self.db,self.config,'alice@example.org')
            self.assertEqual(send.call_count,5)

    def test_expired_invalid_superseded_and_deleted_user_tokens(self):
        old = self.admin_reset()
        token = self.admin_reset()
        for bad in [old,'invalid-token']:
            with self.assertRaises(ValueError): recovery.complete(self.db,bad,'a-new-valid-password')
        with self.db.session() as s:
            s.execute(update(PasswordReset).values(expires=time.time()-1))
        with self.assertRaises(ValueError): recovery.complete(self.db,token,'a-new-valid-password')
        token = self.admin_reset()
        with self.db.session() as s:
            s.get(User,self.alice['id']).deleted_at=time.time()
        with self.assertRaises(ValueError): recovery.complete(self.db,token,'a-new-valid-password')

    def test_validation_failures_do_not_consume_token(self):
        token = self.admin_reset()
        for password in ['short','x'*129,'alice-test-password']:
            self.assertEqual(self.user.post('/api/password/reset',json={'token':token,'password':password}).status_code,400)
        self.assertEqual(self.user.post('/api/password/reset',json={'token':token,'password':'chosen-valid-password'}).status_code,200)
        self.assertEqual(self.user.post('/api/password/forgot',json={'email':'bad'}).status_code,400)
        self.assertEqual(self.user.post('/api/password/reset',json={'token':1,'password':None}).status_code,400)
        self.assertEqual(self.user.post('/api/password/reset',content=b'x'*4097).status_code,413)

    def test_concurrent_completion_succeeds_once(self):
        token = self.admin_reset()
        def attempt(password):
            try:
                recovery.complete(self.db,token,password)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt,['first-new-password','second-new-password']))
        self.assertEqual(sorted(outcomes),[False,True])

    def test_email_edit_uniqueness_roles_and_invalidation(self):
        token = self.admin_reset()
        path = '/api/admin/users/'+self.alice['id']+'/email'
        self.assertEqual(self.user.patch(path,json={'email':'new@example.org'},headers=self.user_headers).status_code,401)
        self.assertEqual(self.admin_client.patch(path,json={'email':'new@example.org'}).status_code,403)
        response = self.admin_client.patch(path,json={'email':'NEW@example.org'},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        with self.assertRaises(ValueError): recovery.complete(self.db,token,'chosen-valid-password')
        self.assertEqual(self.admin_client.post('/api/admin/users',json=dict(username='bob',password='bob-test-password',email='new@example.org'),headers=self.headers).status_code,409)
        self.assertEqual(self.admin_client.patch(path,json={'email':'invalid'},headers=self.headers).status_code,400)

    def test_failed_mail_and_missing_email_keep_admin_fallback_without_disclosure(self):
        with patch('sudweb.recovery.send',side_effect=RuntimeError('private-mail-error')):
            response = self.admin_client.post('/api/admin/users/'+self.alice['id']+'/reset-password',headers=self.headers)
            self.assertFalse(response.json()['email_sent'])
            self.assertIn('reset_link',response.json())
            self.assertNotIn('private-mail-error',response.text)
            reply = self.user.post('/api/password/forgot',json={'email':'alice@example.org'})
            self.assertEqual(reply.status_code,202)
        with self.db.session() as s:
            s.get(User,self.alice['id']).email=None
        with patch('sudweb.recovery.send') as send:
            response=self.admin_client.post('/api/admin/users/'+self.alice['id']+'/reset-password',headers=self.headers)
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json()['email_sent'])
            send.assert_not_called()

    def test_normal_password_change_invalidates_recovery_link(self):
        with patch('sudweb.recovery.send') as send:
            self.user.post('/api/password/forgot',json={'email':'alice@example.org'})
            token=send.call_args.args[2].split('#reset=')[1]
        response=self.user.post('/api/me/password',json=dict(current_password='alice-test-password',new_password='changed-by-user-password'),headers=self.user_headers)
        self.assertEqual(response.status_code,200)
        with self.assertRaises(ValueError): recovery.complete(self.db,token,'chosen-valid-password')

    def test_configuration_requires_canonical_origin_and_safe_tls_port(self):
        for origin in ['','http://public.example','https://user:pass@example.org','https://example.org/path']:
            self.assertFalse(recovery.mail_ready(replace(self.config,public_origin=origin)))
        self.assertTrue(recovery.mail_ready(replace(self.config,public_origin='http://localhost:8080')))
        self.assertFalse(recovery.mail_ready(replace(self.config,mail=replace(self.config.mail,port=25))))
        self.assertNotIn('fake-app-secret',repr(self.config))
        config=replace(self.config,mail=MailSettings())
        with TestClient(create_app(config,self.db)) as client:
            self.assertEqual(client.post('/api/password/forgot',json={'email':'alice@example.org'}).status_code,503)

    def test_smtp_uses_verified_tls_and_never_plaintext_authentication(self):
        for port,constructor in [(465,'SMTP_SSL'),(587,'SMTP')]:
            smtp=MagicMock()
            with patch('sudweb.recovery.smtplib.'+constructor) as connect:
                connect.return_value.__enter__.return_value=smtp
                recovery.send(replace(self.config,mail=replace(self.config.mail,port=port)),
                    'alice@example.org','https://sud.example.org/#reset=test')
                smtp.login.assert_called_once_with('sender@yandex.ru','fake-app-secret')
                smtp.send_message.assert_called_once()
                if port==587:
                    smtp.starttls.assert_called_once()
                    self.assertLess([call[0] for call in smtp.mock_calls].index('starttls'),
                                    [call[0] for call in smtp.mock_calls].index('login'))
                else:
                    self.assertTrue(connect.call_args.kwargs['context'].check_hostname)


if __name__ == '__main__':
    unittest.main()
