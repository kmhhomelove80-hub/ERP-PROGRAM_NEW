Actas 조회/파일 저장 검증 v4
검증된 실제 키 입력 + Tab/Shift+Tab 방식을 사용합니다. SUNIL ERP 등록은 하지 않습니다.
이전 v1/v2는 실행하지 마세요. v3는 입력만 검증합니다.

압축 안의 actas-export-check-v4 폴더를 C:\SUNIL\updates에 넣으세요.
Actas와 동일 Windows 계정/권한으로 PowerShell에서 실행:
& 'C:\SUNIL\ERP\.venv\Scripts\python.exe' 'C:\SUNIL\updates\actas-export-check-v4\actas_export_check_v4.py' --export-checked

Actas 현재고 현황을 열고 검색어와 분류 조건을 전체로 두세요. 저장창을 닫으세요.
다른 사람이 Actas를 조작하지 않도록 하세요.
1. start 입력.
2. Y02 준비에서 ready 입력 후 12초 안에 Actas 창고 코드 칸을 클릭하고 손을 떼세요.
3. 코드 Y02, 오른쪽 명칭 영주제품창고를 확인하고 PowerShell에서 y02 input 입력.
   불일치/알 수 없음은 Enter로 중단. 아직 조회 전입니다.
4. Ctrl+R은 도구가 보냅니다. Actas로 돌아가 표가 갱신되고 조회가 끝났는지 직접 확인.
   PowerShell에서 y02 입력하면 도구가 Ctrl+E와 새 경로 저장을 실행합니다.
   아직 처리 중이거나 이전 표라면 확인 입력하지 마세요.
5. Y09 준비에서 ready 입력 후 12초 안에 창고 코드 칸을 클릭하고 손을 떼세요.
6. Y09와 부산창고 확인 후 y09 input 입력. 표 갱신/조회 완료 확인 후 y09 입력.
   수동으로 Ctrl+R/Ctrl+E를 누르지 마세요. 각 확인 후 키보드/마우스에서 손을 떼세요.

도구는 조회 완료와 창고명을 자동 판독하지 않습니다. 담당자 확인이 필수입니다.
원본 소수, 음수, 공란은 그대로 보존합니다. 공란은 0이 아닙니다.
두 파일의 코드·수량이 같거나 품목 구성이 같으면 성공으로 처리하지 않고 검토를 요청합니다.
이는 창고가 잘못됐다고 단정하는 조건이 아니라 전환을 파일만으로 입증하지 못한다는 뜻입니다.
최종 export_files_checked는 파일 형식 검사 완료이며 SUNIL 자동 등록 성공이 아닙니다.
결과: 이 폴더의 runs 아래 새 실행 폴더/result.json, Y02.xls, Y09.xls.
오류가 나면 자동 재실행하지 말고 result.json을 보내세요. 코드가 빈칸/일부 입력이면 수동 복원하세요.
정상 완료 후 result.json과 두 XLS 파일을 전달하세요.
DB/API/환경설정/서비스/예약작업 변경 없음. 설치/.NET 추가 불필요.
