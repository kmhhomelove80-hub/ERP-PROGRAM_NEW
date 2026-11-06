# 회사 프린터 원격 출력

집에서 사용하는 프로그램과 ERP에서 같은 회사 프린터를 사용할 수 있습니다. 두 경로의 설치는 별도입니다.

## ERP의 회사 프린터 출력

피킹 주문 조회 → 주문 열기 → 회사 프린터로 출력 → 미리보기·부수 확인 → 출력 요청 순서입니다. 출고 관리의 모두 가능 권한이 필요합니다. 인쇄로 재고나 출고 상태를 변경하지 않습니다.

회사 Windows 서버가 요청을 대기열에서 가져와 지정된 프린터로 보냅니다. 기본은 출력 비활성이며, 업데이트만으로 예약 작업을 만들거나 인쇄하지 않습니다.

1. ERP 서버에서 평소 운영하는 Windows 계정으로 로그인합니다. 이 계정에서 Windows 테스트 페이지를 인쇄할 수 있는지 확인합니다.
2. 관리자 PowerShell에서 아래 **조회 전용** 명령을 실행해 등록된 프린터 이름을 확인합니다.

```powershell
& 'C:\SUNIL\ERP\inventory\printing\Office-Printer.ps1' -Mode CheckServer
```

3. ERP의 관리 → 회사 프린터 설정에서 해당 Windows 프린터 이름을 입력하고 출력 허용을 저장합니다.
4. 같은 Windows 계정의 관리자 PowerShell에서 아래 명령으로 인쇄 작업자를 설치·시작합니다. 기존 runtime.local.json과 해당 계정의 암호화된 비밀값을 사용하며, 비밀번호를 새로 입력하거나 파일에 평문으로 적지 않습니다.

```powershell
& 'C:\SUNIL\ERP\inventory\printing\Office-Printer.ps1' -Mode InstallWorker
```

5. 가상 자료를 실제 재고로 등록하지 말고, 실제 피킹 주문의 미리보기를 확인해 필요한 문서 1부로 현장 출력을 확인합니다.

인쇄 작업은 `SUNIL-Company-Printer`라는 별도 예약 작업입니다. Windows 로그인 유지, 절전 해제, 프린터 연결이 필요합니다. 다른 계정으로 설치하면 기존 암호화된 비밀값을 읽지 못합니다. 인쇄 서버가 실제 사용 중인 PostgreSQL에 연결되지 않으면 작업자는 중단합니다.

PowerShell이 다운로드한 스크립트를 차단하면 먼저 배포 파일 해시와 스크립트를 검토한 뒤 해당 파일의 차단 해제를 진행하세요. 전역 실행 정책을 완화하지 마세요. 회사 보안 정책에 따른 관리자 승인이 필요할 수 있습니다.

## 엑셀·PDF·다른 Windows 프로그램에서 선택하기

이 기능은 ERP 화면 기능이 아니라 Windows 공유 프린터 설정입니다. 집 PC와 회사 서버가 VPN으로 연결되어야 합니다. 회사 웹사이트에 접속되는 것만으로 공유 프린터가 연결되지는 않습니다.

1. 회사 서버에서 위의 CheckServer로 프린터 이름을 확인합니다.
2. 관리자가 아래 명령에서 프린터 이름을 실제 이름으로 바꿔 공유를 설정합니다. 기존 프린터의 공유 설정이 변경되는 명령입니다.

```powershell
& 'C:\SUNIL\ERP\inventory\printing\Office-Printer.ps1' -Mode ShareServer -PrinterName '등록된 실제 프린터 이름' -ShareName '회사 프린터'
```

3. 관리자는 해당 공유 프린터의 보안 탭에서 사용할 회사 계정에 인쇄 권한을 부여합니다. 프린터 드라이버는 제조사 또는 Windows의 신뢰할 수 있는 드라이버를 사용합니다.
4. 집 PC에 회사 VPN을 연결합니다. `Office-Printer.ps1`을 집 PC에 복사하고 아래 서버 이름을 **VPN 내부 회사 서버 이름 또는 주소**로 바꿔 조회합니다.

```powershell
.\Office-Printer.ps1 -Mode CheckClient -ServerName 'OFFICE-SERVER-VPN' -ShareName '회사 프린터'
```

5. 연결 가능 여부와 계정 권한을 확인한 뒤 등록합니다.

```powershell
.\Office-Printer.ps1 -Mode ConnectClient -ServerName 'OFFICE-SERVER-VPN' -ShareName '회사 프린터'
```

6. 엑셀이나 PDF의 인쇄 창에서 회사 공유 프린터를 선택합니다. Windows에 따라 표시 이름에 서버 이름이 함께 표시됩니다. 처음 연결할 때 드라이버 설치를 위한 관리자 권한이나 회사 계정 인증이 필요할 수 있습니다.

TCP 445 검사만으로 전체 인쇄 연결이 보장되지는 않습니다. Windows 인쇄에 필요한 인증과 RPC 통신도 VPN 내부에서 허용돼야 합니다. 회사 네트워크 관리자가 확인해야 하며, 스크립트는 방화벽·VPN·공유 권한을 자동 완화하지 않습니다. 인증을 끄거나 Point and Print 보안 설정을 낮추지 마세요.

프린터 공유 포트를 인터넷에 직접 공개하거나 공유기에 인쇄용 포트 전달을 추가하지 마세요. 외부 사용은 회사에서 승인한 VPN 안으로 제한합니다.

## 오류와 중복 출력

- 대기: 작업자가 아직 가져가지 않았습니다. 이 단계에서는 ERP에서 취소할 수 있습니다.
- 전송 중·결과 확인 필요: 작업자가 가져갔습니다. 자동 재등록하지 않습니다.
- 인쇄 대기열 전달: Windows 인쇄 대기열에 전달했으며 종이 출력 완료를 보장하지 않습니다.
- 결과 확인 필요: 전송 중 오류·시간 초과 등이 발생했습니다. 현장 프린터와 Windows 대기열부터 확인하세요.

전송 중 서버가 꺼졌거나 응답이 끊긴 작업은 자동 재출력하지 않습니다. 원래 작업이 실제 출력됐는지 확인한 후 피킹 주문의 새 미리보기에서 다시 요청하세요. 새 미리보기는 새로운 출력 요청이므로 중복 출력될 수 있습니다.

## 다음 업데이트 전에

직원 작업과 저장을 멈추고 인쇄 작업자도 중지·비활성화합니다. 전송 중 문서는 현장 결과를 확인하세요.

```powershell
Disable-ScheduledTask -TaskName 'SUNIL-Company-Printer'
Stop-ScheduledTask -TaskName 'SUNIL-Company-Printer'
```

이후 기존 표준 업데이트를 진행합니다. 완료 후 작업자를 다시 활성화·시작합니다.

```powershell
Enable-ScheduledTask -TaskName 'SUNIL-Company-Printer'
Start-ScheduledTask -TaskName 'SUNIL-Company-Printer'
```

회사 프린터와 VPN은 이 개발 환경에 연결돼 있지 않습니다. 배포 전 검증은 Windows 문서 렌더링과 대기열 동작이며, 실제 회사 장비에서 출력과 집 PC의 VPN 연결은 설치 후 확인해야 합니다.
