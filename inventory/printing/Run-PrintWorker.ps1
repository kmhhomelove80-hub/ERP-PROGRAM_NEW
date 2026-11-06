param([string]$ConfigPath='C:\ProgramData\SUNIL\runtime.local.json')
$ErrorActionPreference='Stop'
$appRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $appRoot 'deploy\windows\Common.ps1')
try {
    $config=Get-SunilConfiguration -ConfigPath $ConfigPath
    if([IO.Path]::GetFullPath($config.AppRoot) -ne [IO.Path]::GetFullPath($appRoot)){throw '기존 ERP 설치 경로와 다릅니다.'}
    Import-SunilEnvironment -Config $config
    $env:PYTHONUTF8='1'
    $logs=Join-Path $config.DataRoot 'logs'
    [void](New-Item -ItemType Directory -Force -Path $logs)
    $id=(Get-Date -Format 'yyyyMMdd-HHmmss-fff')+'-'+$PID
    $child=Start-Process -FilePath $config.PythonExe -ArgumentList @('manage.py','company_print_queue','--watch') -WorkingDirectory $config.AppRoot -NoNewWindow -Wait -PassThru -RedirectStandardOutput (Join-Path $logs ('printer-'+$id+'.stdout.log')) -RedirectStandardError (Join-Path $logs ('printer-'+$id+'.stderr.log'))
    exit $child.ExitCode
}catch{[Console]::Error.WriteLine('인쇄 작업자 시작 실패: '+$_.Exception.Message);exit 1}
