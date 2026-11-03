# 처음 하는 SUNIL ERP 서버 업데이트 — 따라 하기

이 안내는 이미 정상 운영 중인 Windows 서버에 새 프로그램 파일을 반영하는 절차입니다. 서버 설치·데이터 이관을 다시 하는 작업이 아닙니다. 개발 도구가 서버에 접속해 대신 실행하지 않습니다. 아래 명령을 실행하는 시점부터 서버에서 작업이 이루어집니다.

## 먼저 알아둘 네 가지

- **서버 PC**: 현재 ERP가 설치된 Windows 11 컴퓨터입니다. 다른 노트북에서 이 명령을 실행하지 마세요.
- **PowerShell**: Windows 명령 입력 창입니다. `Win + S` → `Windows PowerShell` 검색 → 우클릭 → 관리자 권한으로 실행합니다. 현재 로그인한 **kimmy 계정**을 사용합니다. 다른 관리자 계정 비밀번호를 요구하면 계정 전환을 하지 말고 담당자에게 문의하세요. DPAPI 비밀키는 같은 계정에서만 읽을 수 있습니다.
- **코드 블록**: 회색 상자 안 명령을 처음부터 끝까지 복사해 PowerShell에 붙여넣고 Enter를 누릅니다. 한 블록의 결과를 확인한 다음 다음 블록으로 갑니다. 설명 문장과 ` ``` ` 표시는 복사하지 않습니다.
- **창 유지**: 아래에서 만든 `$Config`, `$Tasks`, `$Before` 같은 값은 그 PowerShell 창에만 있습니다. 업데이트를 끝낼 때까지 같은 창을 사용하세요. 창을 닫았으면 뒤쪽 단계부터 임의로 재개하지 마세요.

## 준비 A. 파일 내려받기

GitHub 게시 폴더에서 **SUNIL-ERP-20261103.zip**을 받습니다. GitHub가 자동 제공하는 저장소 전체 ZIP 대신 이 이름의 배포 파일을 사용하세요. 이 ZIP에는 별도로 준비한 백업·업데이트·되돌리기 절차와 파일 목록이 들어 있습니다.

Windows 다운로드 폴더에 같은 이름의 ZIP을 저장했다고 가정하면, PowerShell에서 다음 명령으로 파일이 온전한지 확인합니다.

```powershell
Get-FileHash -LiteralPath "$env:USERPROFILE\Downloads\SUNIL-ERP-20261103.zip" -Algorithm SHA256
```

표시된 `Hash`는 다음 값과 같아야 합니다. 대소문자 차이는 괜찮습니다. 다운로드 폴더나 파일명이 다르면 그 실제 경로를 사용하세요.

```text
1e02734b8c8742de7985a93e50d33b1b1920f3e9cbb0c8ddf2de9d539104ddea
```

## 준비 B. ZIP 차단 해제와 압축 풀기

1. 파일 탐색기에서 다운로드한 ZIP을 우클릭 → **속성**을 엽니다.
2. 아래에 **차단 해제**가 있으면 체크 → 적용 → 확인합니다. 항목이 없으면 그대로 진행합니다.
3. ZIP 우클릭 → **모두 압축 풀기**를 선택합니다.
4. 대상 폴더를 정확히 **`C:\SUNIL\updates`**로 지정합니다. ZIP 자체에 `SUNIL-ERP-20261103` 폴더가 들어 있으므로 같은 이름을 한 번 더 덧붙이지 않습니다.
5. 아래 경로에 `manifest.json`, `app`, `Apply-UpdateFiles.ps1`이 보여야 합니다.

```text
C:\SUNIL\updates\SUNIL-ERP-20261103\manifest.json
C:\SUNIL\updates\SUNIL-ERP-20261103\app
C:\SUNIL\updates\SUNIL-ERP-20261103\Apply-UpdateFiles.ps1
```

PowerShell에서도 확인할 수 있습니다.

```powershell
Test-Path -LiteralPath 'C:\SUNIL\updates\SUNIL-ERP-20261103\manifest.json'
```

**`True`가 나오면 준비 완료**입니다. `False`이면 압축을 푼 위치를 먼저 확인하세요. 기존 프로그램 폴더인 `C:\SUNIL\ERP`에 ZIP을 통째로 풀거나 덮어쓰지 않습니다.

## 준비 C. 작업 시간 확보

입고·출고·예약·이동 처리를 잠시 중단할 시간을 정하고 사용자에게 알립니다. 먼저 사전 점검과 백업을 끝내고, 성공한 다음 서버를 멈춥니다. 중간에는 PostgreSQL 서비스를 중지하거나 PC를 재부팅하지 않습니다. 기존 설정·DPAPI 비밀키·계정·데이터는 그대로 유지합니다. 이번 업데이트에서 HTTPS나 DEBUG=0 전환은 하지 않습니다.

다음 순서대로 진행합니다. **사전 점검 → 백업 → 서버 중지 → 파일 반영 → 점검/정적 파일 수집 → 재시작 → 화면 확인 → 업데이트 후 백업**입니다.

## 1단계. 사전 점검 — ERP는 아직 켜 둡니다

아래 첫 블록은 현재 설정과 비밀키를 읽고 배포 파일과 DB 구조를 점검합니다. 데이터를 이관하거나 비밀키를 새로 만들지 않습니다. `PASS: verified ...`와 `System check identified no issues`가 나오고 오류가 없어야 합니다. `migrate --check`는 정상일 때 출력이 없을 수 있습니다. 다음 블록은 보호 파일의 해시와 예약 작업 정의를 안전 폴더에 기록합니다. 아직 서버는 중지하지 않습니다.

kimmy로 로그인해 **Windows PowerShell 5.1**을 엽니다. 예약 작업 제어 권한이 필요하면 동일 kimmy 계정으로 관리자 실행합니다. 다른 관리자 계정으로 전환하면 DPAPI 해독이 안 될 수 있습니다. 이번 세션에서 아래 변수를 끝까지 유지합니다.

```powershell
$ErrorActionPreference = 'Stop'
$Release = 'C:\SUNIL\updates\SUNIL-ERP-20261103'
$ConfigPath = 'C:\ProgramData\SUNIL\runtime.local.json'
$AppRoot = 'C:\SUNIL\ERP'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$SafetyRoot = Join-Path 'C:\SUNIL\update-backups' $Stamp
New-Item -ItemType Directory -Path $SafetyRoot | Out-Null

. (Join-Path $AppRoot 'deploy\windows\Common.ps1')
$Config = Get-SunilConfiguration -ConfigPath $ConfigPath
Import-SunilEnvironment -Config $Config
if ($Config.AppRoot -ne $AppRoot) { throw 'AppRoot differs; stop and review.' }
if ($Config.Environment.ERP_DEBUG -ne '1') { throw 'Expected current HTTP DEBUG=1 configuration.' }
if ($Config.Environment.ERP_DB_NAME -ne 'sunil' -or $Config.Environment.ERP_DB_USER -ne 'sunil') {
    throw 'Unexpected database profile.'
}
if ($Config.Listen -ne '127.0.0.1:8000') { throw 'Unexpected Waitress listener.' }
$PgBin = 'C:\Program Files\PostgreSQL\18\bin'
if (-not (Test-Path -LiteralPath (Join-Path $PgBin 'pg_dump.exe'))) { throw 'PostgreSQL 18 client missing.' }
Set-Location -LiteralPath $AppRoot

# Native stderr alone is not a failure in PowerShell 5.1.
# Scope Continue to this native call and always inspect its real exit code.
function Invoke-ErpManage([string[]]$Arguments) {
    $SavedPreference = $ErrorActionPreference
    $NativeExit = $null
    try {
        $ErrorActionPreference = 'Continue'
        & $Config.PythonExe (Join-Path $AppRoot 'manage.py') @Arguments
        $NativeExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $SavedPreference }
    if ($null -eq $NativeExit -or $NativeExit -ne 0) { throw ('manage.py failed, exit=' + $NativeExit) }
}

& (Join-Path $Release 'Apply-UpdateFiles.ps1') -ReleaseRoot $Release -AppRoot $AppRoot -ValidateOnly
if ($LASTEXITCODE -ne 0) { throw 'Release validation failed.' }
Invoke-ErpManage -Arguments @('check')
Invoke-ErpManage -Arguments @('migrate','--check')
```

설정값을 화면에 나열할 필요는 없습니다. 설정과 DPAPI는 읽기만 하고 다시 저장하지 않습니다. PostgreSQL 18 설치 경로가 다르면 실제 설치된 18 버전의 `bin`으로 `$PgBin`을 조정하세요.

다음 보호 파일의 **해시만** 기록하고 예약 작업의 기존 활성 상태를 보관합니다. 해시 기록에는 비밀값 원문이 없습니다. Caddy 설정 경로는 현재 runtime 설정을 그대로 사용합니다.

```powershell
$Protected = @($ConfigPath, $Config.SecretsPath, $Config.CaddyConfig)
$ProtectedBefore = @{}
foreach ($Path in $Protected) { $ProtectedBefore[$Path] = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
$ProtectedBefore | ConvertTo-Json | Set-Content (Join-Path $SafetyRoot 'protected-hashes.json') -Encoding UTF8

$Tasks = @()
foreach ($Name in @('SUNIL-ERP','SUNIL-Caddy','SUNIL-LocalBackup')) {
    $Found = @(Get-ScheduledTask -TaskName $Name)
    if ($Found.Count -ne 1) { throw ('Missing/ambiguous task: ' + $Name) }
    $Tasks += $Found[0]
    Export-ScheduledTask -TaskName $Found[0].TaskName -TaskPath $Found[0].TaskPath | Set-Content (Join-Path $SafetyRoot ($Name + '.xml')) -Encoding Unicode
}
$TaskStates = @($Tasks | ForEach-Object { [pscustomobject]@{ Name=$_.TaskName; Path=$_.TaskPath; Enabled=$_.Settings.Enabled } })
$TaskStates | ConvertTo-Json | Set-Content (Join-Path $SafetyRoot 'task-states.json') -Encoding UTF8
```

모든 업무 사용자에게 저장·처리를 멈추도록 알립니다. 확인 중에는 실제 입고/출고/이동을 추가하지 않습니다. 아래 작업이 끝날 때까지 정비 시간으로 둡니다.

## 2단계. 기존 데이터 백업 — 성공을 확인해야 다음으로 갑니다

이 단계가 가장 중요합니다. 아래 블록은 현재 PostgreSQL 데이터를 별도 로컬 백업 폴더에 저장하고 SHA-256 및 백업 목록을 검사합니다. **`BACKUP SHA256 OK`**가 보여야 합니다. DB와 계정의 변경 전 지문도 기록합니다. 이 지문은 원본 데이터나 비밀번호가 아니라 변경 여부를 비교하기 위한 값입니다. 백업 실패나 경로 오류가 나오면 서버를 멈추지 말고 여기서 멈춥니다.

기존 로컬 백업 작업이 실행 중이면 정상 완료를 기다립니다. NAS가 미완료이므로 이번 사전 백업은 **로컬 전용** 명령을 사용합니다. 이미 검증한 실제 DB를 다시 이관하지 않습니다.

```powershell
$BackupOutput = @(Invoke-ErpManage -Arguments @('backup_database','--directory',$Config.BackupDirectory,'--pg-bin',$PgBin))
$BackupDir = [string]$BackupOutput[-1]
$BackupDir = $BackupDir.Trim()
if (-not (Test-Path -LiteralPath (Join-Path $BackupDir 'database.dump'))) { throw 'Backup dump missing.' }
$env:SUNIL_UPDATE_BACKUP = $BackupDir
Invoke-ErpManage -Arguments @('shell','-c',"import os; from inventory.backups import verify_backup; verify_backup(os.environ['SUNIL_UPDATE_BACKUP']); print('BACKUP SHA256 OK')")
$BackupDir | Set-Content (Join-Path $SafetyRoot 'database-backup-path.txt') -Encoding UTF8

# PostgreSQL custom-format archive structure inspection; does not restore or modify DB.
$SavedPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& (Join-Path $PgBin 'pg_restore.exe') '--list' (Join-Path $BackupDir 'database.dump') > (Join-Path $SafetyRoot 'backup-contents.txt')
$ArchiveExit = $LASTEXITCODE
$ErrorActionPreference = $SavedPreference
if ($ArchiveExit -ne 0) { throw 'PostgreSQL backup archive inspection failed.' }

$env:SUNIL_UPDATE_SNAPSHOT = Join-Path $Release 'app\scripts\update_snapshot.py'
$SnapshotCommand = "import os; exec(open(os.environ['SUNIL_UPDATE_SNAPSHOT'],encoding='utf-8').read())"
$Before = @(Invoke-ErpManage -Arguments @('shell','-c',$SnapshotCommand) | Where-Object { $_ -like 'SUNIL_SNAPSHOT=*' })
if ($Before.Count -ne 1) { throw 'Before-update data fingerprint missing.' }
$Before | Set-Content (Join-Path $SafetyRoot 'data-before.txt') -Encoding UTF8
```

체크섬 검증은 백업 파일의 일치 여부를 확인합니다. 이 절차에서는 운영 DB에 복원하지 않습니다. 개발 검증에서는 격리된 PostgreSQL 18의 백업과 별도 DB 복원을 검증했습니다.

## 3단계. ERP와 Caddy 중지 — DB 서비스는 그대로 둡니다

이제 정비 시간이 시작됩니다. 아래 첫 블록은 기존 예약 작업이 중간에 서버를 다시 띄우지 않게 잠시 비활성화하고 ERP/Caddy를 중지합니다. 사용자의 ERP 접속이 잠시 안 되는 것은 이 단계에서는 정상입니다. 마지막 명령에 8000/8080 수신 목록이 **나오지 않아야** 합니다. 목록이 남으면 이어지는 확인 블록을 사용합니다. 알 수 없는 프로세스라는 오류가 나오면 임의로 Python이나 PostgreSQL을 종료하지 말고 멈춥니다.

```powershell
foreach ($Task in $Tasks) { $Task | Disable-ScheduledTask | Out-Null }
foreach ($Name in @('SUNIL-Caddy','SUNIL-ERP','SUNIL-LocalBackup')) {
    $Task = $Tasks | Where-Object TaskName -eq $Name
    $Task | Stop-ScheduledTask
}
Start-Sleep -Seconds 3
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(8000,8080) } |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

8000/8080에 프로세스가 남아 있으면 반영하지 마세요. 작업 중지 후에도 Python/Caddy 자식이 남았는지 해당 PID의 실행 경로를 확인합니다. 아래는 **남은 8000/8080 수신 프로세스만** 확인하고, 현재 앱의 Waitress 또는 설정의 Caddy와 일치할 때만 종료합니다. PostgreSQL이나 다른 Python을 일괄 종료하지 않습니다.

```powershell
$Listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8000,8080) })
foreach ($Listener in $Listeners) {
    $ProcessId = $Listener.OwningProcess
    $Proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $ProcessId)
    $IsErp = $Listener.LocalPort -eq 8000 -and $Proc.ExecutablePath -eq $Config.PythonExe -and $Proc.CommandLine -match '\-m\s+waitress\b'
    $IsCaddy = $Listener.LocalPort -eq 8080 -and $Proc.ExecutablePath -eq $Config.CaddyExe -and $Proc.CommandLine -match 'caddy'
    if (-not ($IsErp -or $IsCaddy)) { throw ('Unrecognized listener; review PID ' + $ProcessId) }
    Stop-Process -Id $ProcessId
}
if (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8000,8080) }) {
    throw 'Server ports still listening; do not apply files.'
}
```

## 4단계. 새 프로그램 파일 반영

아래 블록은 덮어쓸 기존 코드의 복구본을 먼저 저장하고 검증된 새 코드만 복사합니다. `PASS: installed ...`가 나오고 보호 파일 변경 오류가 없어야 합니다. 기존 데이터·계정·로컬 설정·비밀키·Caddy 설정을 새로 작성하지 않습니다. 오류가 나면 서버를 계속 중지한 채 이 문서 마지막의 되돌리기 절차를 사용합니다.

```powershell
$RollbackRoot = Join-Path $SafetyRoot 'code-rollback'
& (Join-Path $Release 'Apply-UpdateFiles.ps1') -ReleaseRoot $Release -AppRoot $AppRoot -RollbackRoot $RollbackRoot
if ($LASTEXITCODE -ne 0) { throw 'Code update failed. Keep tasks stopped and use rollback section.' }
foreach ($Path in $Protected) {
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $ProtectedBefore[$Path]) { throw ('Protected file changed: ' + $Path) }
}
```

도구는 모든 원본 해시를 먼저 검증하고, 덮어쓸 기존 파일을 검증된 `code-rollback\files`에 보관한 뒤 복사합니다. 새 파일 여부와 기존 해시도 `rollback.json`에 남깁니다. 설정·DB·가상환경·정적 수집 디렉터리·기존 Caddy/백업 실행 스크립트·작업 정의는 덮어쓰지 않습니다. 앱 전체 폴더를 ZIP 폴더로 교체하지 마세요.

## 5단계. 프로그램 검사와 화면 파일 수집

`collectstatic`은 새 CSS·JavaScript·이미지를 Caddy가 제공하는 폴더로 모으는 명령입니다. DB 데이터를 옮기는 명령이 아닙니다. 이번 배포에는 새 migration이 없으므로 `migrate --plan`에 **`No planned migration operations`**가 나와야 합니다. 다른 계획이 보이거나 DB/계정 지문이 달라졌다는 오류가 나오면 자동으로 migrate를 실행하지 말고 멈춥니다.

```powershell
Invoke-ErpManage -Arguments @('check')
Invoke-ErpManage -Arguments @('migrate','--plan')
Invoke-ErpManage -Arguments @('migrate','--check')
# 이번 배포에 추가된 migration은 없습니다. 계획은 'No planned migration operations'여야 합니다.
# --check가 실패하거나 예상 밖 migration이 보이면 여기서 중지하고 원인을 확인하세요.
Invoke-ErpManage -Arguments @('collectstatic','--noinput')

$After = @(Invoke-ErpManage -Arguments @('shell','-c',$SnapshotCommand) | Where-Object { $_ -like 'SUNIL_SNAPSHOT=*' })
$After | Set-Content (Join-Path $SafetyRoot 'data-after.txt') -Encoding UTF8
if ($After.Count -ne 1 -or $Before[0] -ne $After[0]) { throw 'DB/account fingerprint changed; keep tasks stopped and review.' }
foreach ($Path in $Protected) {
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $ProtectedBefore[$Path]) { throw ('Protected file changed: ' + $Path) }
}
```

기준 커밋이 운영에 정상 적용되어 있었다면 `migrate` 실행 자체가 필요 없습니다. 이전 배포의 미적용 migration이 있다면 이번 기능 업데이트와 별도 사안입니다. 백업 후 정확한 계획을 검토한 경우에만 관리자가 `Invoke-ErpManage -Arguments @('migrate','--noinput')`을 실행하고 다시 `--check`·데이터 지문을 확인합니다. 예상하지 못한 계획을 자동 실행하지 않습니다. `collectstatic --clear`도 사용하지 않습니다.

의존성은 기준 커밋과 동일하므로 pip 업그레이드·가상환경 재생성은 필요 없습니다. 현재 정상 동작하는 Python 3.12와 가상환경을 유지합니다.

## 6단계. 기존 방식으로 서버 다시 시작

아래 블록은 원래 활성 상태였던 예약 작업을 되돌리고 `SUNIL-ERP`, `SUNIL-Caddy`를 시작합니다. 기존 로그인 후 시작과 매시간 로컬 백업 설정을 새로 등록하지 않습니다. ERP와 Caddy 작업이 실행되고 8000·8080 수신 포트가 보여야 합니다. 작업 스케줄러의 ‘실행 중’ 표시는 정상일 수 있습니다.

```powershell
foreach ($Saved in $TaskStates) {
    if ($Saved.Enabled) { Enable-ScheduledTask -TaskName $Saved.Name -TaskPath $Saved.Path | Out-Null }
}
foreach ($Name in @('SUNIL-ERP','SUNIL-Caddy')) {
    $Saved = $TaskStates | Where-Object Name -eq $Name
    if ($Saved.Enabled) { Start-ScheduledTask -TaskName $Saved.Name -TaskPath $Saved.Path }
}
Start-Sleep -Seconds 5
Get-ScheduledTask -TaskName 'SUNIL-ERP','SUNIL-Caddy','SUNIL-LocalBackup' | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName 'SUNIL-ERP' | Select-Object LastRunTime,LastTaskResult
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(8000,8080) } | Select-Object LocalAddress,LocalPort,OwningProcess
```

원래 비활성 상태였던 작업은 자동 활성화하지 않습니다. 로그인 후 시작·로그인 중 매시간 로컬 백업이라는 기존 트리거를 재등록하지 않습니다. `SUNIL-ERP`는 실행 중 상태여야 하며, 실행 중인 장기 작업의 `LastTaskResult=267009 (0x41301)`은 ‘실행 중’ 표시일 수 있습니다. 마지막 종료 코드만으로 기동 성공을 판단하지 말고 포트·로그·HTTP 응답을 같이 확인하세요.

## 7단계. 화면 확인 후 업무 재개와 새 백업

먼저 아래 명령으로 로그인 화면과 새 CSS가 열리는지 확인합니다. 그다음 브라우저에서 평소 ERP 주소로 로그인해 기존 품목·수량·예약·계정이 그대로인지 확인합니다. 정리 리스트는 조회·인쇄만 하고 실제 운영 재고에 시험 입출고를 만들지 않습니다. 기존 미완료 NAS/HTTPS 항목과 새 DB/예약 불일치를 구분하세요. `check_readiness`가 실패하면 내용을 확인하고, 새 오류이거나 판단하기 어려우면 업무 재개 전 문의합니다. 정상 확인 후 아래 마지막 로컬 백업 명령까지 실행합니다.

```powershell
$Web = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/login/' -UseBasicParsing -TimeoutSec 15
if ($Web.StatusCode -ne 200) { throw 'Login page unavailable.' }
$Css = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/static/inventory/mobile.css?v=20261103-update' -UseBasicParsing -TimeoutSec 15
if ($Css.StatusCode -ne 200 -or $Css.Content -notmatch 'primary-nav') { throw 'New static asset unavailable.' }
Get-ChildItem -LiteralPath (Join-Path $Config.DataRoot 'logs') -Filter 'server-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 6 Name,Length,LastWriteTime
Invoke-ErpManage -Arguments @('check_readiness')
```

`check_readiness`의 NAS/HTTPS 관련 기존 미완료 항목은 이번 업데이트가 완료시키지 않습니다. 새 DB/예약 불일치나 서버 오류는 해결 전 업무를 재개하지 않습니다. 새 `.stderr.log`의 정상 `Serving on` 안내 자체는 오류가 아니며, traceback/접속 실패/즉시 종료가 없는지 확인합니다.

관리자와 기존 작업자 계정으로 로그인해 다음을 확인합니다. 아래 점검은 기존 데이터 조회 및 입력 화면 열기까지만 하며, 실제 운영 자료에 시험 입출고를 등록하지 않습니다.

- 기존 품목·위치·현재고·예약·피킹 주문·계정이 남아 있고 운영 점검의 원장/예약 합계가 정상인지 확인.
- PC/휴대폰에서 통합 메뉴, 정리 리스트 조회·인쇄 미리보기, 최신 사용 매뉴얼/PDF 확인.
- 관리자는 출고 전 예약의 위치·수량 변경 화면을 열 수 있고 출고 완료 행에는 변경 링크가 없는지 확인. 실제 변경은 정상 업무 시 사용.
- 작업자는 이동 요청 입력·처리 상태를 볼 수 있고, 입고/출고 실행·이동 승인·직원 계정 버튼은 없는지 확인. 요청 테스트는 별도 시험 환경에서 수행.
- Android 실제 기기에서 Binary Eye 아이콘 실행 → 알려진 실제 품목 코드 스캔 → ERP 복귀/정확 일치 결과 확인. 미설치 안내와 수동 검색도 확인.
- `http://192.168.0.252:8080/`을 회사 Wi-Fi에서 확인. 고정 IP 설정이 아직 완료되지 않았다면 실제 현재 IP를 먼저 확인하고, 이번 절차에서 네트워크 설정을 바꾸지 않음.

정상 확인 후 업무를 재개하고 업데이트 후 로컬 백업을 생성합니다. 이번에는 NAS 미완료를 이유로 로컬 백업을 실패시키는 명령을 사용하지 않습니다.

```powershell
Invoke-ErpManage -Arguments @('backup_database','--directory',$Config.BackupDirectory,'--pg-bin',$PgBin)
Invoke-ErpManage -Arguments @('check_backups',$Config.BackupDirectory,'--max-age-hours','2')
```

`SUNIL-LocalBackup`은 원래 활성 상태로 복귀했는지 확인하고 다음 정시 실행의 로그·최신 백업 시각을 확인합니다. 실제 kimmy 로그인 시작/종료·백업 트리거는 Windows 현장에서만 최종 검증할 수 있습니다.

## 문제가 생겼을 때. 데이터는 건드리지 않고 코드만 되돌립니다

서버 중지 후 발생한 실패라면 업무를 재개하지 않습니다. 먼저 위 3단계의 중지·포트 확인을 다시 수행한 뒤 아래 되돌리기 블록을 실행합니다. 완료 후 6단계 재시작과 7단계 화면 확인을 반복합니다. **DB 초기화·재이관·운영 DB 덮어 복원은 하지 않습니다.** PowerShell 창을 닫아 변수가 사라졌거나 오류 원인을 모르겠다면 추가 명령을 추측해 실행하지 말고 마지막 실행 단계와 오류 문구를 알려주세요.

오류가 발생하면 업무 재개를 보류하고 2절의 작업 비활성화·중지·포트 확인을 수행합니다. DB를 초기화하거나 운영 DB에 백업을 덮어 복원하지 마세요. 이번 배포에는 새 migration이 없으므로 우선 코드만 되돌립니다.

```powershell
& (Join-Path $Release 'Restore-UpdateFiles.ps1') -RollbackRoot $RollbackRoot
if ($LASTEXITCODE -ne 0) { throw 'Rollback needs manual review; keep server stopped.' }
Invoke-ErpManage -Arguments @('check')
Invoke-ErpManage -Arguments @('collectstatic','--noinput')
# 5절의 원래 활성 상태 복구·ERP/Caddy 시작, 6절의 포트/로그/화면 확인을 반복합니다.
```

복구 도구는 기존 파일을 복사하고 이번 배포에서 새로 생긴 코드 파일만 제거합니다. 설치 후 별도로 수정한 파일은 해시 차이로 거부하므로 수동 검토가 필요합니다. runtime.local.json·DPAPI·Caddyfile·계정·운영 DB는 복구 도구 대상이 아닙니다. 배포 후 정상 업무로 생긴 데이터도 유지합니다. DB 복원이 필요한 별도 사고라면 이번 코드 복구와 구분해 판단하고, 운영 DB 덮어쓰기 대신 별도 새 DB 복원 검증부터 수행해야 합니다.

## 흔한 상황과 대처

| 상황 | 해야 할 일 |
|---|---|
| PowerShell에 `>>`가 계속 보임 | 명령의 괄호·따옴표·블록 일부가 빠졌을 수 있습니다. Ctrl+C로 취소하고 해당 코드 블록 전체를 다시 복사합니다. 다른 단계로 건너뛰지 않습니다. |
| 경로를 찾을 수 없음 | 압축 위치와 폴더가 두 번 중첩됐는지 확인합니다. `C:\SUNIL\ERP`는 기존 프로그램, `C:\SUNIL\updates\SUNIL-ERP-20261103`은 새 파일입니다. |
| DPAPI/계정 오류 | 비밀키를 새로 생성하지 않습니다. 원래 kimmy 계정과 설정 경로인지 확인합니다. |
| 스크립트 실행이 차단됨 | ZIP 속성의 차단 해제를 압축 풀기 전에 했는지 확인합니다. 회사 정책으로 막혀 있으면 정책을 임의 변경하지 말고 담당자에게 문의합니다. |
| Waitress 로그에 Serving 안내가 있음 | 정상 시작 안내일 수 있습니다. 포트·화면 접속과 종료 코드도 같이 확인합니다. |
| 정리 리스트에 후보가 없음 | 현재 조건에 맞는 품목이 없을 수 있습니다. 입고 완료·위치 합계 3 이하·예약 없는 위치 기준입니다. |
| Binary Eye가 안 열림 | 실제 Android 앱 설치·버전·브라우저 설정을 확인하고 앱을 직접 열어 스캔할 수 있습니다. 다른 OS는 코드 직접 검색을 사용합니다. |
| 서버 중지 후 실패 | 작업을 계속 중지하고 마지막 절의 코드 되돌리기를 사용합니다. DB를 삭제하거나 재이관하지 않습니다. |

문의할 때는 마지막으로 완료한 단계, 오류 문구, 접속 여부를 알려주세요. `runtime.local.json` 전체나 DPAPI 비밀키 파일, 비밀번호는 채팅에 올리지 마세요.
