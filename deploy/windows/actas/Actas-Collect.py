"""Run the local GUI with the EXISTING account's DPAPI environment. No elevation."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description='SUNIL Actas 수집 버튼 (검증용)')
    parser.add_argument('--config', default=r'C:\ProgramData\SUNIL\runtime.local.json')
    parser.add_argument('--profile')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('기존 Windows 계정의 ERP Python 3.12로 실행하세요.')
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if os.path.normcase(os.path.abspath(config['PythonExe'])) != os.path.normcase(os.path.abspath(sys.executable)):
        raise RuntimeError('기존 ERP 가상환경의 python.exe로 실행하세요.')
    app, data = Path(config['AppRoot']), Path(config['DataRoot'])
    env = os.environ.copy()
    for key, value in config['Environment'].items():
        if not re.fullmatch(r'ERP_[A-Z0-9_]+', key) or key in ('ERP_SECRET_KEY', 'ERP_DB_PASSWORD'):
            raise RuntimeError('기존 로컬 환경 설정을 확인하세요.')
        env[key] = str(value)
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    child_env = {k: v for k, v in env.items() if k not in ('ERP_SECRET_KEY', 'ERP_DB_PASSWORD')}
    child_env.update(SUNIL_CONFIG_PATH=str(config_path), PSModulePath=str(powershell.parent / 'Modules'))
    code = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Import-Module Microsoft.PowerShell.Security; $c=Get-Content -LiteralPath $env:SUNIL_CONFIG_PATH -Raw|ConvertFrom-Json; $v=Import-Clixml -LiteralPath $c.SecretsPath; $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; if($v.OwnerSid -ne $sid){throw 'Wrong secret owner'}; $r=@{}; foreach($n in @('ERP_SECRET_KEY','ERP_DB_PASSWORD')){$r[$n]=[Management.Automation.PSCredential]::new('unused',$v.Secrets[$n]).GetNetworkCredential().Password}; $r|ConvertTo-Json -Compress"
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', code],
                            env=child_env, capture_output=True, encoding='utf-8', timeout=30)
    if result.returncode:
        raise RuntimeError('기존 비밀키 소유 Windows 계정인지 확인하세요. 비밀값은 출력하지 않습니다.')
    env.update(json.loads(result.stdout))
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    profile = args.profile or str(data / 'actas' / 'profile.local.json')
    return subprocess.run([sys.executable, str(app / 'manage.py'), 'collect_actas',
                           '--directory', str(data / 'actas' / 'runs'), '--profile', profile],
                          cwd=app, env=env).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
