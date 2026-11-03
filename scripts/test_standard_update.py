"""Cross-platform real-filesystem tests; Windows/DB side effects are isolated."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('standard_updater',ROOT/'deploy/windows/standard_update/release_updater.py')
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)


class FakeOps:
    def __init__(self,root,app,failure=''):
        self.root=Path(root);self.app=Path(app);self.failure=failure;self.sid='synthetic-owner'
        self.tasks=[{'name':'SUNIL-ERP','enabled':True,'running':True},{'name':'SUNIL-LocalBackup','enabled':True,'running':False}]
        self.tasks.append({'name':'SUNIL-Caddy','enabled':True,'running':True})
        for task in self.tasks:task.update(path='\\',actions=[{'Execute':'synthetic','Arguments':task['name'],'WorkingDirectory':'synthetic'}])
        self.web='http://127.0.0.1:8080'
        self.protected={str(self.root/'runtime.local.json'):u.digest(self.root/'runtime.local.json')}
        self.events=[];self.running=True;self.schedule=True
        self.db={'stock':{'count':1,'sha256':'unchanged'},'accounts':{'count':1,'sha256':'unchanged'}}
    def check_protected(self,protected):
        for path,sha in protected.items():
            if u.digest(path)!=sha:raise RuntimeError('changed protected file')
    def select_web(self,value):
        if value!=self.web:raise RuntimeError('web changed')
    def maintenance(self,tasks):self.events.append('maintenance');self.schedule=False
    def stop(self):self.events.append('stop');self.running=False
    def start(self,tasks):self.events.append('start');self.running=True
    def restore_schedule(self,tasks):self.events.append('restore-schedule');self.schedule=True
    def fingerprint(self):
        value=json.loads(json.dumps(self.db))
        if self.failure=='fingerprint' and 'migrate' in self.events:value['stock']['sha256']='injected-change'
        return value
    def backup(self,label):
        self.events.append('backup-'+label)
        if self.failure=='backup-'+label:raise RuntimeError('injected backup failure')
        folder=self.root/('backup-'+label);folder.mkdir(exist_ok=True)
        (folder/'database.dump').write_bytes(json.dumps(self.db).encode())
        u.atomic_json(folder/'manifest.json',{'sha256':u.digest(folder/'database.dump')})
        return str(folder)
    def migrate(self):
        self.events.append('migrate');self.db['new_tables']={'count':1,'sha256':'new-grants'}
        if self.failure=='migrate':raise RuntimeError('injected migration failure')
    def validate(self,old=False):
        self.events.append('validate-old' if old else 'validate-new')
        if self.failure in ('validate','damaged-recovery') and not old:
            if self.failure=='damaged-recovery':
                first=next((self.root/'run/old-files').rglob('*.py'));first.write_text('damaged')
            raise RuntimeError('injected validation failure')
    def health(self,old=False):
        self.events.append('health-old' if old else 'health-new')
        if self.failure=='health' and not old:raise RuntimeError('injected web failure')


class StandardUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sunil-standard-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.release=self.root/'release';self.app=self.root/'app';self.run=self.root/'run'
        for p in (self.release,self.app,self.run):p.mkdir()
        (self.root/'runtime.local.json').write_text('protected settings')
        rows=[]
        for name,old in [('inventory/views.py',True),('config/urls.py',True),('inventory/new_module.py',False)]:
            source=self.release/'app'/name;source.parent.mkdir(parents=True,exist_ok=True);source.write_text('new '+name)
            target=self.app/name;target.parent.mkdir(parents=True,exist_ok=True)
            if old:target.write_text('old '+name)
            rows.append({'path':name,'sha256':u.digest(source),'accepted_previous_sha256':[u.digest(target)] if old else [],'required_existing':old})
        helpers={}
        for name in ('Update-Erp.py','release_updater.py','release_probe.py','update_snapshot.py'):
            (self.release/name).write_text('synthetic helper '+name);helpers[name]=u.digest(self.release/name)
        self.manifest={'format':3,'release':'synthetic-release','target_migration':'0013_employee_menu_access','files':rows,'helpers':helpers}
        self.old={p.relative_to(self.app).as_posix():p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        self.ops=FakeOps(self.root,self.app)
    def call_apply(self):
        with contextlib.redirect_stdout(io.StringIO()):return u.apply(self.ops,self.release,self.run,self.manifest)
    def assert_original(self):
        current={p.relative_to(self.app).as_posix():p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        self.assertEqual(current,self.old);self.assertTrue(self.ops.running);self.assertTrue(self.ops.schedule)
        self.ops.check_protected(self.ops.protected)
    def test_success_adds_tables_without_changing_existing_fingerprint(self):
        self.assertEqual(self.call_apply(),0)
        self.assertLess(self.ops.events.index('backup-before-stop'),self.ops.events.index('stop'))
        self.assertLess(self.ops.events.index('backup-stopped'),self.ops.events.index('migrate'))
        self.assertTrue(self.ops.schedule and self.ops.running)
        self.assertEqual(json.loads((self.run/'journal.json').read_text(encoding='utf-8'))['phase'],'complete')
        for entry in self.manifest['files']:self.assertEqual(u.digest(self.app/entry['path']),entry['sha256'])
    def test_failure_cases_restore_files_and_tasks_but_never_restore_database(self):
        # Separate fixture instances prevent one scenario from influencing another.
        for failure in ('backup-before-stop','backup-stopped','migrate','validate','fingerprint','health'):
            with self.subTest(failure=failure):
                case=StandardUpdateTests('test_success_adds_tables_without_changing_existing_fingerprint');case.setUp()
                try:
                    case.ops.failure=failure;self.assertEqual(case.call_apply(),10);case.assert_original()
                    if failure=='backup-before-stop':self.assertNotIn('stop',case.ops.events)
                    if failure in ('migrate','validate','fingerprint','health'):self.assertIn('new_tables',case.ops.db)
                finally:case.doCleanups()
    def test_post_backup_failure_keeps_verified_new_program(self):
        self.ops.failure='backup-after-update';self.assertEqual(self.call_apply(),2)
        self.assertTrue(self.ops.running and self.ops.schedule)
        self.assertEqual(u.digest(self.app/'inventory/views.py'),self.manifest['files'][0]['sha256'])
    def test_partial_copy_rolls_back_all_existing_files(self):
        original=u.atomic_copy;count=0
        def fail(source,target):
            nonlocal count
            if Path(source).is_relative_to(self.release/'app'):
                count+=1
                if count==2:raise RuntimeError('injected partial copy')
            original(source,target)
        with patch.object(u,'atomic_copy',side_effect=fail):self.assertEqual(self.call_apply(),10)
        self.assert_original()
    def test_damaged_rollback_is_reported_without_blind_restore(self):
        self.ops.failure='damaged-recovery';self.assertEqual(self.call_apply(),20)
        self.assertEqual(json.loads((self.run/'journal.json').read_text(encoding='utf-8'))['phase'],'recovery_required')
    def test_modified_source_is_rejected_before_tasks_or_backup(self):
        (self.release/'app/inventory/views.py').write_text('tampered')
        with self.assertRaises(RuntimeError):self.call_apply()
        self.assertEqual(self.ops.events,[]);self.assert_original()
    def test_modified_helper_is_rejected_before_tasks(self):
        (self.release/'release_probe.py').write_text('tampered')
        with self.assertRaises(RuntimeError):self.call_apply()
        self.assertEqual(self.ops.events,[])
    def test_custom_installed_code_is_not_overwritten(self):
        (self.app/'inventory/views.py').write_text('custom')
        with self.assertRaises(RuntimeError):self.call_apply()
        self.assertEqual((self.app/'inventory/views.py').read_text(encoding='utf-8'),'custom')
    def test_bad_paths_and_duplicate_names_are_rejected(self):
        for value in ('../outside','inventory/../outside','inventory//a.py','/absolute','C:/bad','inventory/a\\b'):
            with self.subTest(value=value),self.assertRaises(RuntimeError):u.code_path(value)
        self.manifest['files'].append(self.manifest['files'][0])
        with self.assertRaises(RuntimeError):u.validate_payload(self.release,self.app,self.manifest)
    def test_corrupt_rollback_has_no_partial_restore(self):
        journal={'rollback_ready':False}
        u.install_files(self.release,self.app,self.run,self.manifest,journal)
        (self.run/'old-files/config/urls.py').write_text('damaged')
        with self.assertRaises(RuntimeError):u.restore_files(self.app,self.run,self.manifest)
        for entry in self.manifest['files']:self.assertEqual(u.digest(self.app/entry['path']),entry['sha256'])
    def test_post_install_custom_changes_are_preserved(self):
        journal={'rollback_ready':False}
        u.install_files(self.release,self.app,self.run,self.manifest,journal)
        (self.app/'inventory/views.py').write_text('custom after install')
        with self.assertRaises(RuntimeError):u.restore_files(self.app,self.run,self.manifest)
        self.assertEqual((self.app/'inventory/views.py').read_text(encoding='utf-8'),'custom after install')
    def test_recovery_from_written_journal(self):
        journal={'format':1,'rollback_ready':False,'protected':self.ops.protected,'tasks':self.ops.tasks,'phase':'files_installed','web':self.ops.web}
        u.install_files(self.release,self.app,self.run,self.manifest,journal)
        loaded=json.loads((self.run/'journal.json').read_text(encoding='utf-8'))
        u.recover(self.ops,self.run,self.manifest,loaded)
        self.assert_original();self.assertEqual(loaded['phase'],'rolled_back')
    def restored_v2_journal(self):
        journal={'release':'SUNIL-ERP-20261104-integrated-v2','rollback_ready':False,
                 'protected':self.ops.protected,'tasks':json.loads(json.dumps(self.ops.tasks)),
                 'phase':'recovery_required','web':self.ops.web,'error':'original v2 schedule failure'}
        u.install_files(self.release,self.app,self.run,self.manifest,journal)
        u.restore_files(self.app,self.run,self.manifest)
        return journal
    def test_reconcile_verified_v2_does_not_stop_copy_migrate_or_backup(self):
        journal=self.restored_v2_journal()
        u.reconcile(self.ops,self.run,self.manifest,journal)
        self.assertEqual(self.ops.events,['health-old','restore-schedule'])
        self.assert_original();self.assertEqual(journal['phase'],'rolled_back')
        self.assertEqual(journal['error'],'original v2 schedule failure')
        self.assertEqual(journal['reconciled_by'],self.manifest['release'])
    def test_reconcile_rejects_incomplete_restore_before_any_side_effects(self):
        journal=self.restored_v2_journal()
        u.atomic_copy(self.release/'app/inventory/views.py',self.app/'inventory/views.py')
        with self.assertRaises(RuntimeError):u.reconcile(self.ops,self.run,self.manifest,journal)
        self.assertEqual(self.ops.events,[]);self.assertEqual(journal['phase'],'recovery_required')
    def test_reconcile_rejects_changed_task_definition_or_stopped_server(self):
        journal=self.restored_v2_journal()
        self.ops.tasks[0]['actions'][0]['Arguments']='changed'
        with self.assertRaises(RuntimeError):u.reconcile(self.ops,self.run,self.manifest,journal)
        self.assertEqual(self.ops.events,[])
        self.ops.tasks=json.loads(json.dumps(journal['tasks']));self.ops.tasks[0]['running']=False
        with self.assertRaises(RuntimeError):u.reconcile(self.ops,self.run,self.manifest,journal)
        self.assertEqual(self.ops.events,[])
    def test_reconcile_rejects_damaged_backup_and_preserves_current_code(self):
        journal=self.restored_v2_journal()
        (self.run/'old-files/config/urls.py').write_text('damaged backup')
        with self.assertRaises(RuntimeError):u.reconcile(self.ops,self.run,self.manifest,journal)
        self.assertEqual(self.ops.events,[]);self.assert_original()
    def test_reconcile_schedule_failure_does_not_mark_complete_or_change_code(self):
        journal=self.restored_v2_journal()
        with patch.object(self.ops,'restore_schedule',side_effect=RuntimeError('injected task error')):
            with self.assertRaises(RuntimeError):u.reconcile(self.ops,self.run,self.manifest,journal)
        self.assertEqual(journal['phase'],'recovery_required');self.assert_original()
    def test_single_process_lock_prevents_a_second_writer(self):
        with u.exclusive_lock(self.root/'updater.lock'):
            with self.assertRaises(RuntimeError):
                with u.exclusive_lock(self.root/'updater.lock'):pass
    def test_nonmatching_process_identity_and_pid_reuse_are_rejected(self):
        ops=u.WindowsOps.__new__(u.WindowsOps)
        row={'ExecutablePath':os.path.abspath(os.sys.executable),'CommandLine':'python -m waitress --listen=127.0.0.1:8000 config.wsgi:application','ProcessId':123,'CreationDate':'first'}
        ops.captured=row.copy();ops.check_process(row,True)
        with self.assertRaises(RuntimeError):ops.check_process({**row,'CreationDate':'reused'},True)
        with self.assertRaises(RuntimeError):ops.check_process({**row,'CommandLine':'python other.py'})
    def test_atomic_replace_updates_existing_file_and_json(self):
        target=self.root/'existing.json';target.write_text('{}');u.atomic_json(target,{'한글':'값'})
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'한글':'값'})
    def test_missing_old_data_or_account_changes_are_not_ignored(self):
        for after in ({'accounts':1},{'stock':1,'accounts':2}):
            with self.assertRaises(RuntimeError):u.preserve_fingerprint({'stock':1,'accounts':1},after)

    def test_real_native_stderr_is_logged_without_becoming_failure(self):
        ops=u.WindowsOps.__new__(u.WindowsOps);ops.app=self.root;ops.run=self.run;ops.env=os.environ.copy()
        output=ops.command([os.sys.executable,'-c',"import sys; print('정상'); print('normal notice',file=sys.stderr)"],'notice')
        self.assertEqual(output.strip(),'정상')
        self.assertEqual((self.run/'notice.stderr.log').read_text(encoding='utf-8').strip(),'normal notice')
        with self.assertRaises(RuntimeError):ops.command([os.sys.executable,'-c','raise SystemExit(7)'],'exit-seven')
    def test_real_timeout_is_a_failure_and_secret_output_never_creates_logs(self):
        ops=u.WindowsOps.__new__(u.WindowsOps);ops.app=self.root;ops.run=self.run;ops.env=os.environ.copy()
        with self.assertRaises(RuntimeError):ops.command([os.sys.executable,'-c','import time; time.sleep(5)'],'timeout',timeout=1)
        output=ops.command([os.sys.executable,'-c',"print('synthetic-secret')"],'secret-read',secret=True)
        self.assertEqual(output.strip(),'synthetic-secret')
        self.assertFalse((self.run/'secret-read.stdout.log').exists())
        self.assertFalse((self.run/'secret-read.stderr.log').exists())


if __name__=='__main__':unittest.main(verbosity=2)
