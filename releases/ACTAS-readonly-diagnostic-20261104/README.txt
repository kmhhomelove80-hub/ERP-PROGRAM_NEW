SUNIL Actas 화면 읽기 전용 진단 — 2026-11-04

이 파일은 완성된 자동 수집/업데이트 프로그램이 아닙니다.
Actas 버튼·입력란을 실제 PC에서 식별하기 위한 진단만 수행합니다.
Actas 조작, DB 연결, ERP 업데이트, 공유기/방화벽 설정 변경을 하지 않습니다.
추가 패키지·관리자 실행·비밀키·DB 비밀번호가 필요하지 않습니다.

1. 서버 PC의 기존 Windows 계정으로 로그인하고 Actas의 현재고 현황을 열어 둡니다.
   아직 Actas가 설치되지 않았다면 먼저 정상 설치·로그인·수동 조회가 되는지 확인합니다.
2. 이 actas-diagnostic 폴더를 C:\SUNIL\updates\actas-diagnostic 에 둡니다.
3. 같은 계정의 일반 PowerShell에서 다음 명령을 한 번 실행합니다.

& 'C:\SUNIL\ERP\.venv\Scripts\python.exe' 'C:\SUNIL\updates\actas-diagnostic\inspect_actas_controls.py' --output 'C:\SUNIL\updates\actas-diagnostic\Actas-controls.json'

4. 발견한 Actas 창 수가 1 이상인지 확인하고 결과 JSON을 개발 담당자에게 전달합니다.
   0개면 실행 여부/세션/권한 수준을 먼저 확인합니다. 다음 조작을 자동 실행하지 않습니다.
   결과에는 입력값·비밀번호·재고 표 내용이 없지만 공개 저장소에는 올리지 않습니다.
5. 기존 결과 파일은 덮어쓰지 않습니다. 재진단은 Actas-controls-2.json처럼 다른 파일명을 지정합니다.

개인키·DB 암호·Actas 비밀번호·원본 업무 파일은 보내지 마세요.
현재 단계에서 자동 수집용 프로필의 확인 플래그를 임의로 바꾸지 마세요.
백신 경고가 발생하면 허용/예외 등록 없이 중단하고 담당자에게 전달합니다.
