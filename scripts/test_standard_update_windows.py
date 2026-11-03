"""Windows-only native checks, using disposable DPAPI files and our own process.

Never reads a real runtime config or SUNIL task; no security settings are changed.
"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('standard_windows_test',ROOT/'deploy/windows/standard_update/release_updater.py')
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)


@unittest.skipUnless(os.name=='nt','Native Windows PowerShell/DPAPI/CIM test')
class NativeWindowsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sunil-native-test-');self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.ops=u.WindowsOps.__new__(u.WindowsOps)
        self.ops.app=ROOT;self.ops.run=self.folder/'logs';self.ops.env=os.environ.copy()
        self.ops.config_path=self.folder/'runtime.synthetic.json'
        self.ops.powershell=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
    def test_actual_ps51_multiple_task_restore_and_original_regression(self):
        # Real scheduled tasks, isolated in a unique folder; no SUNIL root task
        # or server setting is read or changed. No trigger and never started.
        task_path='\\SUNIL-Updater-Test-'+uuid.uuid4().hex+'\\'
        tasks=[{'name':'SUNIL-ERP','path':task_path,'enabled':True},
               {'name':'SUNIL-LocalBackup','path':task_path,'enabled':True}]
        try:
            self.ops.ps("$v=$env:SUNIL_VALUES|ConvertFrom-Json; $action=New-ScheduledTaskAction -Execute ($env:SystemRoot+'\\System32\\cmd.exe') -Argument '/c exit 0'; foreach($t in $v.tasks){Register-ScheduledTask -TaskName $t.name -TaskPath $t.path -Action $action -Force -ErrorAction Stop|Out-Null; Disable-ScheduledTask -TaskName $t.name -TaskPath $t.path -ErrorAction Stop|Out-Null}",values={'tasks':tasks})
            # Single-item v2 call passed; exactly the multi-item JSON form that
            # failed on the server MUST fail under native Windows PowerShell 5.1.
            legacy="$a=@($env:SUNIL_VALUES|ConvertFrom-Json); foreach($t in $a){Enable-ScheduledTask -TaskName $t.name -TaskPath $t.path -ErrorAction Stop|Out-Null}"
            self.ops.ps(legacy,values=tasks[:1])
            with self.assertRaises(RuntimeError):self.ops.ps(legacy,values=tasks)
            self.assertIn('TaskName',(self.ops.run/'windows-command.stderr.log').read_text(encoding='utf-8'))
            for settings in (tasks[:1],tasks,[{**tasks[0],'enabled':False},tasks[1]],
                             [{**t,'enabled':False} for t in tasks],tasks):
                with self.subTest(settings=settings):
                    self.ops.restore_schedule(settings)
                    actual=self.ops.ps("$v=$env:SUNIL_VALUES|ConvertFrom-Json; $rows=@(foreach($t in $v.tasks){$a=Get-ScheduledTask -TaskName $t.name -TaskPath $t.path; [pscustomobject]@{name=$a.TaskName;enabled=[bool]$a.Settings.Enabled}}); $rows|ConvertTo-Json -Compress",values={'tasks':tasks})
                    expected={t['name']:t['enabled'] for t in tasks}
                    expected.update({t['name']:t['enabled'] for t in settings})
                    # Only selected task state must match; the other may retain
                    # a state set by the preceding single-item operation.
                    for row in actual:
                        if row['name'] in {t['name'] for t in settings}:
                            self.assertEqual(row['enabled'],expected[row['name']])
            # Full file transaction + REAL native task restore. Database and
            # HTTP probes stay synthetic; these tests never touch a real ERP.
            from test_standard_update import StandardUpdateTests
            for scenario in ('success','health','legacy-final-schedule'):
                case=StandardUpdateTests('test_success_adds_tables_without_changing_existing_fingerprint')
                case.setUp()
                try:
                    case.manifest['release']='SUNIL-ERP-20261104-integrated-v2'
                    case.ops.failure='health' if scenario=='health' else ''
                    original_restore=case.ops.restore_schedule
                    legacy_mode=[scenario=='legacy-final-schedule']
                    def restore(records):
                        native=[{**t,'path':task_path} for t in records]
                        if legacy_mode[0] and len([t for t in native if t['name'] in ('SUNIL-ERP','SUNIL-LocalBackup')])>1:
                            self.ops.ps(legacy,values=[t for t in native if t['name'] in ('SUNIL-ERP','SUNIL-LocalBackup')])
                        else:self.ops.restore_schedule(native)
                        original_restore(records)
                    case.ops.restore_schedule=restore
                    original_start=case.ops.start
                    def start(records):
                        restore([t for t in records if t['name']=='SUNIL-ERP'])
                        original_start(records)
                    case.ops.start=start
                    code=case.call_apply()
                    self.assertEqual(code,{'success':0,'health':10,'legacy-final-schedule':20}[scenario])
                    if scenario=='health':case.assert_original()
                    if scenario=='legacy-final-schedule':
                        journal=json.loads((case.run/'journal.json').read_text(encoding='utf-8'))
                        self.assertEqual(journal['phase'],'recovery_required')
                        legacy_mode[0]=False
                        u.reconcile(case.ops,case.run,case.manifest,journal)
                        case.assert_original();self.assertEqual(journal['phase'],'rolled_back')
                finally:case.doCleanups()
        finally:
            self.ops.ps("$v=$env:SUNIL_VALUES|ConvertFrom-Json; foreach($t in $v.tasks){if(Get-ScheduledTask -TaskName $t.name -TaskPath $t.path -ErrorAction SilentlyContinue){Unregister-ScheduledTask -TaskName $t.name -TaskPath $t.path -Confirm:$false -ErrorAction Stop}}; $s=New-Object -ComObject Schedule.Service; $s.Connect(); $s.GetFolder('\\').DeleteFolder($v.tasks[0].path.Trim('\\'),0)",values={'tasks':tasks})
    def test_actual_dpapi_roundtrip_owner_check_and_no_secret_logs(self):
        vault=self.folder/'synthetic.clixml'
        self.ops.config_path.write_text(json.dumps({'SecretsPath':str(vault)}),encoding='utf-8')
        try:
            self.ops.ps("$c=Get-Content -LiteralPath $env:SUNIL_CONFIG_PATH -Raw|ConvertFrom-Json; $v=[pscustomobject]@{OwnerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;Secrets=@{ERP_SECRET_KEY=(ConvertTo-SecureString 'synthetic-key-not-real' -AsPlainText -Force);ERP_DB_PASSWORD=(ConvertTo-SecureString 'synthetic-password-not-real' -AsPlainText -Force)}}; $v|Export-Clixml -LiteralPath $c.SecretsPath")
        except RuntimeError as error:
            diagnostic=(self.ops.run/'windows-command.stderr.log').read_text(encoding='utf-8')
            raise RuntimeError('Synthetic fixture creation failed: '+diagnostic) from error
        result=self.ops.ps(u.VAULT_COMMAND,secret=True)
        self.assertEqual(result['ERP_SECRET_KEY'],'synthetic-key-not-real')
        self.assertEqual(result['ERP_DB_PASSWORD'],'synthetic-password-not-real')
        for p in self.ops.run.glob('*.log'):
            self.assertNotIn('synthetic-key-not-real',p.read_text(encoding='utf-8'))
            self.assertNotIn('synthetic-password-not-real',p.read_text(encoding='utf-8'))
        self.ops.ps("$c=Get-Content -LiteralPath $env:SUNIL_CONFIG_PATH -Raw|ConvertFrom-Json; $v=Import-Clixml -LiteralPath $c.SecretsPath; $v.OwnerSid='S-1-0-0'; $v|Export-Clixml -LiteralPath $c.SecretsPath")
        with self.assertRaises(RuntimeError):self.ops.ps(u.VAULT_COMMAND,secret=True)
    def test_actual_waitress_cim_identity_creation_time_and_matching_child_stop(self):
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',8000))
            except OSError:self.skipTest('Port 8000 already occupied on this disposable runner')
        env={**os.environ,'ERP_DB_NAME':'','ERP_SQLITE_PATH':str(self.folder/'synthetic.sqlite3'),'ERP_DEBUG':'1'}
        process=subprocess.Popen([sys.executable,'-m','waitress','--listen=127.0.0.1:8000','config.wsgi:application'],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            row=None
            for _ in range(30):
                if process.poll() is not None:raise RuntimeError('Synthetic Waitress exited')
                row=self.ops.process()
                if row:break
                time.sleep(.2)
            self.assertIsNotNone(row);self.assertEqual(row['ProcessId'],process.pid)
            self.assertIsInstance(row['CreationDate'],str);self.assertTrue(row['CreationDate'].startswith('ticks:'))
            self.ops.captured=row
            self.ops.check_process(row,captured=True)
            with self.assertRaises(RuntimeError):self.ops.check_process({**row,'CreationDate':'different'},captured=True)
            native_ps=self.ops.ps
            def isolated_ps(code,**kwargs):
                if code.startswith('Stop-ScheduledTask'):return None # no real task exists or is touched
                return native_ps(code,**kwargs)
            self.ops.ps=isolated_ps
            self.ops.stop()
            self.assertIsNone(self.ops.process())
            process.wait(timeout=15)
        finally:
            if process.poll() is None:process.kill()
            process.communicate(timeout=15)


if __name__=='__main__':unittest.main(verbosity=2)
