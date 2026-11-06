param(
    [ValidateSet('CheckServer','ShareServer','CheckClient','ConnectClient','InstallWorker')][string]$Mode='CheckServer',
    [string]$PrinterName,
    [string]$ServerName,
    [string]$ShareName='회사 프린터',
    [string]$AppPath='C:\SUNIL\ERP',
    [string]$PythonPath='C:\SUNIL\ERP\.venv\Scripts\python.exe',
    [string]$ConfigPath='C:\ProgramData\SUNIL\runtime.local.json'
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
if($Mode -in @('CheckServer','ShareServer')){
    if($Mode -eq 'CheckServer'){
        Get-Printer|Select-Object Name,DriverName,PortName,Shared,ShareName,PrinterStatus|Format-Table -AutoSize
        Write-Output '인쇄 작업자와 같은 Windows 계정에서 프린터 이름 및 테스트 출력을 확인하세요.'
    }else{
        if(-not $PrinterName -or $ShareName -notmatch '^[\p{L}\p{Nd}_ -]{1,60}$'){throw '프린터 이름과 공유 이름을 확인하세요.'}
        Set-Printer -Name $PrinterName -Shared $true -ShareName $ShareName
        Write-Output '공유를 설정했습니다. VPN 연결, 인쇄 권한, 회사 내부 방화벽 범위는 관리자가 별도로 확인하세요.'
    }
}elseif($Mode -in @('CheckClient','ConnectClient')){
    if($ServerName -notmatch '^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$' -or $ShareName -notmatch '^[\p{L}\p{Nd}_ -]{1,60}$'){throw 'VPN 내부 서버 이름과 공유 이름을 확인하세요.'}
    $connection='\\'+$ServerName+'\'+$ShareName
    Test-NetConnection -ComputerName $ServerName -Port 445 -InformationLevel Detailed
    if($Mode -eq 'ConnectClient'){
        Add-Printer -ConnectionName $connection
        Write-Output ('프린터가 연결됐습니다: '+$connection+' · Windows 설정에서 표시 이름을 회사 프린터로 정할 수 있습니다.')
    }else{Write-Output '조회만 수행했습니다. VPN과 회사 계정 권한이 필요합니다. 이 검사 통과만으로 RPC 인쇄 연결까지 보장되지는 않습니다.'}
}else{
    $app=(Resolve-Path -LiteralPath $AppPath).Path
    $python=(Resolve-Path -LiteralPath $PythonPath).Path
    if(-not (Test-Path -LiteralPath (Join-Path $app 'manage.py'))){throw 'ERP 프로그램 경로를 확인하세요.'}
    $identity=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $config=(Resolve-Path -LiteralPath $ConfigPath).Path
    $launcher=Join-Path $app 'inventory\printing\Run-PrintWorker.ps1'
    $powershell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments='-NoProfile -NonInteractive -File "'+$launcher+'" -ConfigPath "'+$config+'"'
    $action=New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $app
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName 'SUNIL-Company-Printer' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'ERP에서 요청한 회사 프린터 작업 처리' -Force|Out-Null
    Start-ScheduledTask -TaskName 'SUNIL-Company-Printer'
    Write-Output '인쇄 작업자를 시작했습니다. 이 계정의 Windows 로그인이 유지되어야 합니다. 절전과 프린터 연결 상태를 확인하세요.'
}
