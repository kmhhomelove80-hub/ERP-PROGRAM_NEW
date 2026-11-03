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
    def test_actual_dpapi_roundtrip_owner_check_and_no_secret_logs(self):
        vault=self.folder/'synthetic.clixml'
        self.ops.config_path.write_text(json.dumps({'SecretsPath':str(vault)}),encoding='utf-8')
        self.ops.ps("$c=Get-Content -LiteralPath $env:SUNIL_CONFIG_PATH -Raw|ConvertFrom-Json; $v=[pscustomobject]@{OwnerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;Secrets=@{ERP_SECRET_KEY=(ConvertTo-SecureString 'synthetic-key-not-real' -AsPlainText -Force);ERP_DB_PASSWORD=(ConvertTo-SecureString 'synthetic-password-not-real' -AsPlainText -Force)}}; $v|Export-Clixml -LiteralPath $c.SecretsPath")
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
            self.assertIsInstance(row['CreationDate'],str);self.assertIn('T',row['CreationDate'])
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
