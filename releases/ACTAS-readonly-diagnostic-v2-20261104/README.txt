Actas 읽기 전용 상세 진단 v2 — 2026-11-04

기존 진단으로 숨겨진 PowerBuilder 입력칸을 구별하지 못해 보완한 도구입니다.
Actas 이름이 포함된 파일 탐색기/브라우저는 제외합니다.
현재 포커스, 메뉴 명령 정보와 Windows 접근성 패턴을 읽습니다.
입력값/비밀번호/재고 표 내용은 저장하지 않고 고정된 조회/엑셀/창고 등의 라벨만 기록합니다.
자동 클릭/키 입력/조회 실행/파일 추출/DB 연결/프로그램 설정 변경은 하지 않습니다.
설치 파일이나 수집기 완성본이 아닙니다. 운영 ERP 업데이트도 필요 없습니다.

1. ZIP의 actas-diagnostic-v2 폴더를 C:\SUNIL\updates 에 둡니다.
2. 서버 PC에서 Actas 현재고 현황을 열어 둡니다.
3. 같은 Windows 계정의 PowerShell에서 다음 한 줄을 실행합니다.

& 'C:\SUNIL\ERP\.venv\Scripts\python.exe' 'C:\SUNIL\updates\actas-diagnostic-v2\inspect_actas_details_v2.py' --output 'C:\SUNIL\updates\actas-diagnostic-v2\Actas-detail.json'

4. 안내가 나오면 12초 안에 Actas로 돌아가 창고 코드 칸 안쪽을 클릭하고 그대로 기다립니다.
   코드 값은 바꾸거나 조회 버튼을 누르지 않습니다. 별도 Start-Sleep 명령은 필요 없습니다.
5. 대기 포함 최대 1분 정도 후 결과가 저장됩니다. 접근성 읽기가 지원되지 않거나 지연되면
   그 상태도 결과에 남기며 Actas를 종료하거나 설정을 바꾸지 않습니다.
6. Actas-detail.json을 담당 개발자에게 전달합니다. 기존 결과는 덮어쓰지 않습니다.
   재실행은 Actas-detail-2.json처럼 새 파일명을 지정합니다.

.NET 3.5나 pywinauto의 추가 설치는 필요하지 않습니다.
Windows PowerShell과 Windows에 포함된 .NET 4 UIAutomation 라이브러리를 사용합니다.
관리자 권한 전환/실행 정책 변경/백신 허용·제외 설정을 하지 않습니다.
개인키·접속 비밀번호·DB 설정 파일을 보내지 마세요. 진단 결과는 공개 저장소에 올리지 않습니다.
