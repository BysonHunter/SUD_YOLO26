"""Admin access, reversible user deletion, archival and encrypted upload boundaries."""
import base64
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from sqlalchemy import select, text, inspect
from sudweb.api import create_app
from sudweb.config import Settings
from sudweb.db import Database, Job, User
from sudweb.manage import create_user
from sudweb import transport


class StudyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Settings(data=self.root, database_url=f'sqlite:///{self.root/"db.sqlite"}', secure_cookie=False)
        self.db = Database(self.config.database_url)
        self.db.init()
        self.app = create_app(self.config, self.db)
        self.clients, self.accounts, self.headers = {}, {}, {}
        for name in ('admin', 'alice', 'bobby'):
            self.accounts[name] = create_user(self.db, name, name+'-test-password')
            client = self.clients[name] = TestClient(self.app)
            csrf = client.post('/api/login', json=dict(username=name, password=name+'-test-password')).json()['csrf']
            self.headers[name] = {'X-CSRF-Token': csrf}

    def tearDown(self):
        for client in self.clients.values():
            client.close()
        self.db.engine.dispose()
        self.temp.cleanup()

    def ready(self, owner='alice', state='completed'):
        with self.db.session() as s:
            job = Job(user_id=self.accounts[owner]['id'], filename='patient.zip',
                      state=state, stage='Готово', upload_bytes=12)
            s.add(job); s.flush(); job_id = job.id
        root = self.config.job_dir(self.accounts[owner]['id'], job_id)
        root.mkdir(parents=True)
        (root/'input.zip').write_bytes(b'PK\x03\x04original')
        (root/'slice.png').write_bytes(b'image')
        (root/'report.pdf').write_bytes(b'pdf')
        (root/'mesh.json').write_text('{"data":[]}', encoding='utf-8')
        (root/'result.json').write_text(json.dumps(dict(series=[dict(
            images={'coronal':[{'_file':'slice.png'}]}, stones={'right':[dict(
                _previews={'hu':'slice.png'}, _model='mesh.json')]},
            _kidney_models={'right':'mesh.json'}, _pdf='report.pdf')]), ensure_ascii=False), encoding='utf-8')
        return job_id, root

    def begin(self, data, owner='alice', **extra):
        client = self.clients[owner]
        public = serialization.load_der_public_key(base64.b64decode(client.get('/api/upload-key').json()['spki']))
        key, prefix = AESGCM.generate_key(bit_length=256), os.urandom(8)
        payload = dict(filename='dicom.zip', size=len(data),
            wrapped_key=transport.b64(public.encrypt(key, transport.oaep())), nonce=transport.b64(prefix))
        payload.update(extra)
        response = client.post('/api/jobs', json=payload, headers=self.headers[owner])
        return response, key, prefix

    def block(self, job_id, data, key, prefix, index=0, owner='alice', aad_job=None, size=None):
        size = len(data) if size is None else size
        cipher = AESGCM(key).encrypt(prefix+index.to_bytes(4,'big'),
            data[index*transport.CHUNK_SIZE:(index+1)*transport.CHUNK_SIZE],
            transport.aad(aad_job or job_id,size,index))
        response = self.clients[owner].put(f'/api/jobs/{job_id}/upload/{index}',
            content=cipher, headers=self.headers[owner])
        return response, cipher

    def test_admin_sees_all_owners_and_can_read_every_result(self):
        job, root = self.ready()
        other, _ = self.ready('bobby')
        rows = self.clients['admin'].get('/api/jobs').json()
        self.assertEqual({r['owner'] for r in rows}, {'alice','bobby'})
        self.assertEqual(len(self.clients['admin'].get('/api/jobs?limit=1&offset=1').json()),1)
        self.assertNotIn('owner', self.clients['alice'].get('/api/jobs').json()[0])
        for suffix in ('', '/result', '/pdf/0', '/series/0/images/coronal/0',
                       '/series/0/stones/right/0/preview/hu', '/series/0/models/right/stone/0',
                       '/series/0/models/right/kidney/0'):
            url = '/api/jobs/'+job+suffix
            self.assertEqual(self.clients['admin'].get(url).status_code,200,url)
            self.assertEqual(self.clients['bobby'].get(url).status_code,404,url)
        self.clients['admin'].delete('/api/admin/users/'+self.accounts['alice']['id'],headers=self.headers['admin'])
        row = self.clients['admin'].get('/api/jobs/'+job).json()
        self.assertEqual(row['owner'],'alice'); self.assertTrue(row['owner_deleted'])

    def test_user_deletion_hides_every_resource_but_preserves_admin_access(self):
        job, root = self.ready()
        response = self.clients['alice'].delete('/api/jobs/'+job,headers=self.headers['alice'])
        self.assertTrue(response.json()['hidden'])
        self.assertTrue((root/'input.zip').exists())
        self.assertEqual(self.clients['alice'].get('/api/jobs').json(),[])
        for suffix in ('', '/result','/pdf/0','/series/0/images/coronal/0',
                       '/series/0/stones/right/0/preview/hu','/series/0/models/right/kidney/0'):
            self.assertEqual(self.clients['alice'].get('/api/jobs/'+job+suffix).status_code,404)
            self.assertEqual(self.clients['admin'].get('/api/jobs/'+job+suffix).status_code,200)
        row = self.clients['admin'].get('/api/jobs/'+job).json()
        self.assertEqual(row['display_status'],'Удалено пользователем')
        self.assertEqual(row['state'],'completed')
        self.assertEqual(self.clients['alice'].post('/api/jobs/'+job+'/cancel',headers=self.headers['alice']).status_code,404)
        self.assertEqual(self.clients['bobby'].delete('/api/jobs/'+job,headers=self.headers['bobby']).status_code,404)

    def test_hide_active_job_and_admin_must_wait_for_worker(self):
        job, root = self.ready(state='queued')
        self.assertEqual(self.clients['alice'].delete('/api/jobs/'+job,headers=self.headers['alice']).status_code,200)
        self.assertEqual(self.db.claim(90)['id'],job)
        self.assertEqual(self.clients['admin'].delete('/api/jobs/'+job,headers=self.headers['admin']).status_code,409)
        self.assertTrue(root.exists())

    def test_admin_archives_and_can_download_and_reprocess_after_restart(self):
        job, root = self.ready()
        original = (root/'input.zip').read_bytes()
        self.clients['alice'].delete('/api/jobs/'+job,headers=self.headers['alice'])
        for _ in range(2):
            response = self.clients['admin'].delete('/api/jobs/'+job,headers=self.headers['admin'])
            self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(root.exists())
        archive = self.config.archive_dir(self.accounts['alice']['id'],job)/'input.zip'
        self.assertEqual(archive.read_bytes(),original)
        self.assertEqual(self.clients['admin'].get('/api/jobs/'+job+'/archive').content,original)
        self.assertEqual(self.clients['alice'].get('/api/jobs/'+job+'/archive').status_code,403)
        self.assertEqual(self.clients['admin'].get('/api/jobs/'+job+'/result').status_code,409)
        self.db.init()
        self.assertEqual(self.clients['admin'].get('/api/jobs/'+job).json()['display_status'],'В архиве')
        response = self.clients['admin'].post('/api/jobs/'+job+'/reprocess',headers=self.headers['admin'])
        self.assertEqual(response.status_code,201,response.text)
        new_id = response.json()['id']
        with self.db.session() as s:
            new = s.get(Job,new_id)
            self.assertEqual(new.user_id,self.accounts['admin']['id'])
            self.assertEqual(new.source_job_id,job)
        self.assertEqual((self.config.job_dir(self.accounts['admin']['id'],new_id)/'input.zip').read_bytes(),original)
        self.assertEqual(self.clients['alice'].get('/api/jobs').json(),[])
        self.assertEqual(self.db.claim(90)['id'],new_id)

    def test_archive_failure_keeps_source_and_retry_is_safe(self):
        job, root = self.ready()
        with patch('sudweb.studies.checksum', side_effect=[b'a',b'b']):
            response = self.clients['admin'].delete('/api/jobs/'+job,headers=self.headers['admin'])
        self.assertEqual(response.status_code,500)
        self.assertTrue((root/'input.zip').is_file())
        self.assertTrue((root/'result.json').is_file())
        self.assertIsNone(self.db.claim(90))
        self.assertEqual(self.clients['admin'].delete('/api/jobs/'+job,headers=self.headers['admin']).status_code,200)

    def test_incomplete_upload_archive_has_no_source_and_is_not_reprocessed(self):
        job, root = self.ready(state='failed')
        (root/'input.zip').unlink()
        with self.db.session() as s:
            s.get(Job,job).upload_bytes = 0
        self.assertEqual(self.clients['admin'].delete('/api/jobs/'+job,headers=self.headers['admin']).status_code,200)
        self.assertFalse(self.clients['admin'].get('/api/jobs/'+job).json()['archive_available'])
        self.assertEqual(self.clients['admin'].post('/api/jobs/'+job+'/reprocess',headers=self.headers['admin']).status_code,404)

    def test_missing_accepted_zip_does_not_remove_remaining_results(self):
        job, root = self.ready()
        (root/'input.zip').unlink()
        with self.assertLogs('sudweb.api',level='ERROR'):
            response=self.clients['admin'].delete('/api/jobs/'+job,headers=self.headers['admin'])
        self.assertEqual(response.status_code,500)
        self.assertTrue((root/'result.json').is_file())
        self.assertTrue((root/'slice.png').is_file())

    def test_old_job_schema_migration_preserves_data_and_is_idempotent(self):
        job, root = self.ready()
        with self.db.engine.begin() as c:
            for column in ('deleted_by_user_at','archived_at','source_job_id','upload_envelope'):
                c.execute(text(f'ALTER TABLE jobs DROP COLUMN {column}'))
        self.db.init(); self.db.init()
        with self.db.session() as s:
            self.assertEqual(s.get(Job,job).filename,'patient.zip')
            self.assertIsNone(s.get(Job,job).archived_at)

    def test_encrypted_multiblock_upload_and_transport_key_persistence(self):
        data = b'PK\x03\x04'+os.urandom(transport.CHUNK_SIZE+37)
        response,key,prefix = self.begin(data)
        self.assertEqual(response.status_code,201,response.text)
        job = response.json()['id']
        self.assertIsNone(self.db.claim(90))
        for i in (0,1):
            response,cipher = self.block(job,data,key,prefix,i)
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn(data[i*transport.CHUNK_SIZE:i*transport.CHUNK_SIZE+32],cipher)
        app = create_app(self.config,self.db)
        self.assertEqual(transport.public_key(app.state.transport_key),transport.public_key(self.app.state.transport_key))
        response = self.clients['alice'].post('/api/jobs/'+job+'/complete',headers=self.headers['alice'])
        self.assertEqual(response.status_code,201,response.text)
        root = self.config.job_dir(self.accounts['alice']['id'],job)
        self.assertEqual((root/'input.zip').read_bytes(),data)
        self.assertFalse((root/'upload.part').exists())
        with self.db.session() as s:
            self.assertIsNone(s.get(Job,job).upload_envelope)
        self.assertEqual(self.db.claim(90)['id'],job)

    def test_corruption_wrong_aad_order_replay_and_incomplete_upload_fail_closed(self):
        data = b'PK\x03\x04'+os.urandom(transport.CHUNK_SIZE+17)
        response,key,prefix = self.begin(data)
        job = response.json()['id']
        path = '/api/jobs/'+job
        self.assertEqual(self.clients['alice'].post(path+'/complete',headers=self.headers['alice']).status_code,409)
        self.assertEqual(self.block(job,data,key,prefix,1)[0].status_code,409)
        self.assertEqual(self.block(job,data,key,prefix,aad_job='foreign')[0].status_code,400)
        encrypted = AESGCM(key).encrypt(prefix+bytes(4),data[:transport.CHUNK_SIZE],transport.aad(job,len(data),0))
        corrupt = encrypted[:-1]+bytes([encrypted[-1]^1])
        self.assertEqual(self.clients['alice'].put(path+'/upload/0',content=corrupt,headers=self.headers['alice']).status_code,400)
        self.assertEqual(self.clients['alice'].put(path+'/upload/0',content=encrypted[:-1],headers=self.headers['alice']).status_code,400)
        self.assertEqual(self.clients['bobby'].put(path+'/upload/0',content=encrypted,headers=self.headers['bobby']).status_code,404)
        self.assertEqual(self.clients['admin'].put(path+'/upload/0',content=encrypted,headers=self.headers['admin']).status_code,404)
        self.assertFalse((self.config.job_dir(self.accounts['alice']['id'],job)/'upload.part').exists())
        self.assertEqual(self.block(job,data,key,prefix)[0].status_code,200)
        self.assertEqual(self.block(job,data,key,prefix)[0].status_code,409)
        self.assertEqual(self.clients['alice'].post(path+'/complete',headers=self.headers['alice']).status_code,409)
        self.assertIsNone(self.db.claim(90))

    def test_plaintext_bad_key_bad_nonce_and_oversized_chunk_rejected(self):
        client = self.clients['alice']; headers = self.headers['alice']
        self.assertEqual(client.post('/api/jobs',content=b'PKxx',headers={**headers,'Content-Type':'application/zip'}).status_code,415)
        self.assertEqual(self.begin(b'PKxx',nonce='invalid')[0].status_code,400)
        self.assertEqual(self.begin(b'PKxx',wrapped_key=transport.b64(bytes(384)))[0].status_code,400)
        self.assertEqual(self.begin(b'PKxx',size=True)[0].status_code,400)
        self.assertEqual(client.get('/api/jobs').json(),[])
        response,key,prefix = self.begin(b'PKxx')
        job = response.json()['id']
        self.assertEqual(client.put('/api/jobs/'+job+'/upload/0',content=b'x'*(transport.CHUNK_SIZE+17),headers=headers).status_code,413)
        self.assertEqual(client.post('/api/jobs/'+job+'/complete',headers={}).status_code,403)

    def test_unsafe_archive_routes_are_admin_only_and_csrf_protected(self):
        job,_ = self.ready()
        for suffix in ('archive','reprocess'):
            response=(self.clients['alice'].get('/api/jobs/'+job+'/archive') if suffix=='archive'
                else self.clients['alice'].post('/api/jobs/'+job+'/reprocess',headers=self.headers['alice']))
            self.assertEqual(response.status_code,403)
        self.assertEqual(self.clients['admin'].post('/api/jobs/'+job+'/reprocess').status_code,403)
        self.assertEqual(self.clients['admin'].delete('/api/jobs/'+job).status_code,403)


if __name__ == '__main__':
    unittest.main()
