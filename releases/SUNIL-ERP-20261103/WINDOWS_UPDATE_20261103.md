# Windows 서버 업데이트 절차 — SUNIL ERP 2026-11-03

이 문서는 **kimmy 계정으로 Windows 11에서 직접 실행할 절차**입니다. 개발 도구가 운영 서버·DB를 변경하지 않았습니다. 계정·DB를 새로 만들거나 데이터를 재이관하지 않습니다. 현재 내부 HTTP, DEBUG=1, Caddy 8080 → Waitress 127.0.0.1:8000 구성을 유지합니다. NAS·HTTPS 전환을 같이 수행하지 않습니다.

배포 ZIP을 `C:\SUNIL\updates`에 풀면 아래 구조가 됩니다. 아래 날짜 경로는 ZIP의 실제 폴더명과 맞춥니다.

```text
C:\SUNIL\updates\SUNIL-ERP-20261103\
  app\                     코드·템플릿·정적 원본·최신 매뉴얼
  manifest.json            파일별 SHA-256, 기준 커밋
  Apply-UpdateFiles.ps1    검증 및 파일 반영 전용
  Restore-UpdateFiles.ps1  파일 되돌리기 전용
  WINDOWS_UPDATE.md        이 절차
  RELEASE_NOTES.md         기능·한도·현장 확인 설명
  VALIDATION.md            개발 검증 결과
```

배포물에는 DB·업무 자료·계정 덤프·가상환경·runtime.local.json·DPAPI 파일·Caddyfile·예약 작업 정의가 없습니다. 파일 반영 도구는 manifest의 코드 파일만 복사합니다. `robocopy /MIR`, 앱 폴더 전체 삭제, 설정 예제 복사, `Initialize-Secrets.ps1`, `flush`, `import_legacy`, `restore_database`, `createsuperuser`는 이 업데이트에 사용하지 마세요.

## 0. 사전 확인 — 아직 서버를 멈추지 않음

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

## 1. 백업 — 성공·검증 전에는 중지/파일 반영으로 진행하지 않음

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

## 2. 서버 중지 — PostgreSQL 서비스는 유지

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

## 3. 파일 반영 — 로컬 설정·비밀키·Caddyfile 보존

```powershell
$RollbackRoot = Join-Path $SafetyRoot 'code-rollback'
& (Join-Path $Release 'Apply-UpdateFiles.ps1') -ReleaseRoot $Release -AppRoot $AppRoot -RollbackRoot $RollbackRoot
if ($LASTEXITCODE -ne 0) { throw 'Code update failed. Keep tasks stopped and use rollback section.' }
foreach ($Path in $Protected) {
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $ProtectedBefore[$Path]) { throw ('Protected file changed: ' + $Path) }
}
```

도구는 모든 원본 해시를 먼저 검증하고, 덮어쓸 기존 파일을 검증된 `code-rollback\files`에 보관한 뒤 복사합니다. 새 파일 여부와 기존 해시도 `rollback.json`에 남깁니다. 설정·DB·가상환경·정적 수집 디렉터리·기존 Caddy/백업 실행 스크립트·작업 정의는 덮어쓰지 않습니다. 앱 전체 폴더를 ZIP 폴더로 교체하지 마세요.

## 4. 구조 점검과 정적 파일 수집

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

## 5. 기존 예약 작업 재시작

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

## 6. 읽기 중심 확인 → 업무 재개 → 업데이트 후 백업

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

## 실패 시 — 코드만 되돌리기

오류가 발생하면 업무 재개를 보류하고 2절의 작업 비활성화·중지·포트 확인을 수행합니다. DB를 초기화하거나 운영 DB에 백업을 덮어 복원하지 마세요. 이번 배포에는 새 migration이 없으므로 우선 코드만 되돌립니다.

```powershell
& (Join-Path $Release 'Restore-UpdateFiles.ps1') -RollbackRoot $RollbackRoot
if ($LASTEXITCODE -ne 0) { throw 'Rollback needs manual review; keep server stopped.' }
Invoke-ErpManage -Arguments @('check')
Invoke-ErpManage -Arguments @('collectstatic','--noinput')
# 5절의 원래 활성 상태 복구·ERP/Caddy 시작, 6절의 포트/로그/화면 확인을 반복합니다.
```

복구 도구는 기존 파일을 복사하고 이번 배포에서 새로 생긴 코드 파일만 제거합니다. 설치 후 별도로 수정한 파일은 해시 차이로 거부하므로 수동 검토가 필요합니다. runtime.local.json·DPAPI·Caddyfile·계정·운영 DB는 복구 도구 대상이 아닙니다. 배포 후 정상 업무로 생긴 데이터도 유지합니다. DB 복원이 필요한 별도 사고라면 이번 코드 복구와 구분해 판단하고, 운영 DB 덮어쓰기 대신 별도 새 DB 복원 검증부터 수행해야 합니다.
