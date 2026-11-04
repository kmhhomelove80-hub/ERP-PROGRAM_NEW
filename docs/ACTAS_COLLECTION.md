# Actas Y02·Y09 원클릭 대조 자료 수집 — 개발 검증 단계

## 목적과 현재 상태

Actas 현재고 현황에서 Y02 영주본사, Y09 부산창고를 조회하고 각각 Excel로 저장한 뒤
기존 ERP 대조 스냅샷으로 함께 등록합니다. **Stock/Allocation/입고/출고는 변경하지 않습니다.**
화면 진단·로컬 버튼·파일 안정성 검사·기존 권한/등록 서비스 연결·실패/중복 방지 처리를 구현했습니다.
**실제 Actas 화면 제어 항목과 조회 완료 표시가 아직 확인되지 않았습니다. 실제 화면 자동 수집 성공이나 운영 준비 완료로 보지 않습니다.**
이 단계에서는 운영 ERP 업데이트가 필요 없는 읽기 전용 진단부터 실시합니다.

## 1. 실제 PC 읽기 전용 진단 (별도 설치/DB 연결 없음)

서버 PC에 Actas를 설치하고 담당자가 직접 로그인해 현재고 현황을 열어 둡니다.
Actas 비밀번호를 도구에 저장하거나 공유하지 않습니다. 진단은 기존 Python 3.12와
`scripts/inspect_actas_controls.py` 한 파일만 사용합니다.

예시 — 진단 파일을 `C:\SUNIL\updates\actas-diagnostic\inspect_actas_controls.py`에 둔 경우:

```powershell
& 'C:\SUNIL\ERP\.venv\Scripts\python.exe' 'C:\SUNIL\updates\actas-diagnostic\inspect_actas_controls.py' --output 'C:\SUNIL\updates\actas-diagnostic\Actas-controls.json'
```

관리자 실행·실행 정책 변경·추가 패키지·DB 암호가 필요 없습니다. Actas와 같은 Windows 사용자 세션에서 실행합니다.
기존 결과 파일은 덮어쓰지 않습니다. 다시 진단할 때는 다른 파일명을 지정합니다.
출력에는 컨트롤 클래스/ID/부모 관계/활성 상태 및 제한된 고정 라벨만 있습니다.
입력란 값·비밀번호·재고 표 내용은 읽지 않습니다. 진단 파일은 공개 저장소에 올리지 않습니다.
창이 0개면 Actas 실행 여부/세션/권한 수준을 확인하고 화면을 자동 클릭하지 않습니다.

이 결과로 창고 입력란, 조회/엑셀 툴바, 현재고 화면을 식별합니다. 저장 대화상자도
실제 화면에서 별도 확인해야 합니다. 조회 시작→완료가 검증되는 표시를 찾지 못하면
CPU 유휴/일정 시간 대기만으로 성공 처리하지 않고 화면 어댑터를 보완합니다.

## 2. 원클릭 도구 구조 (실제 PC 확인 후 적용)

- `inventory/actas_collection.py`: 두 창고를 새 실행 폴더에 저장, 파일 완료/검증/원자적 스냅샷 등록.
- `inventory/actas_windows.py`: pywinauto 기반 제어 항목 탐색, 창고 읽기 확인, 조회 완료 상태 전환, Save As 제어.
- `inventory/actas_desktop.py`: 담당자가 SUNIL 계정으로 인증한 뒤 버튼 실행. 별도 웹 엔드포인트를 열지 않습니다.
- `deploy/windows/actas/Actas-Collect.py`: 기존 runtime.local.json과 같은 Windows 계정의 DPAPI만 사용해 GUI 실행.
- `requirements-actas.txt`: Windows 수집 PC에만 선택적으로 설치하는 pywinauto 의존성. 기본 ERP 의존성은 유지합니다.

도구는 Actas 로그인을 자동화하지 않습니다. 이미 로그인한 현재고 현황을 사용합니다.
Windows 잠금/RDP 연결 해제/다른 사용자의 마우스 조작은 실행 실패 원인이므로 현장 검증이 필요합니다.
Windows 예약 작업과 정기 실행은 이번 범위에 포함하지 않습니다.

검증할 프로필 항목(실제 값을 추정하여 채우지 말 것):

```json
{
  "schema": 1,
  "verified_on_this_pc": false,
  "computer_name": "실제 PC 확인 후 설정",
  "backend": "win32",
  "main_window": null,
  "stock_window": null,
  "warehouse_edit": null,
  "query_action": null,
  "completion": null,
  "export_action": null,
  "save_dialog": null,
  "filename_edit": null,
  "save_button": null
}
```

프로필은 기존 DataRoot의 `actas/profile.local.json`에 비밀키 없이 보관합니다.
확인되지 않은 프로필은 실행을 거부합니다. 제어 항목이 여러 개면 첫 번째를 임의로 클릭하지 않습니다.
두 액션의 범위는 main/stock, selector는 title/title_re/class_name/control_id/auto_id/control_type만 허용합니다.
툴바는 확인된 toolbar_button 텍스트로 선택합니다. completion에는 범위/selector/busy_text_re/ready_text_re를 둡니다.
조회 중→완료 전환을 실제 관찰해야 합니다. 창고 입력만 바꾸고 이전 조회 결과를 저장하지 않습니다.

검증 후 설치 시 실행 명령 예시 (현재 단계에서 실행하지 말 것):

```powershell
& 'C:\SUNIL\ERP\.venv\Scripts\python.exe' 'C:\SUNIL\ERP\deploy\windows\actas\Actas-Collect.py'
```

현재 표준 v3 배포본에는 이 신규 도구가 없습니다. 현장 진단 후 별도 업데이트에 포함합니다.

## 데이터/오류 처리

1. SUNIL 계정 인증 및 **관리—모두 가능** 권한 확인 후 Actas를 조작합니다.
2. 실행별 새 UUID 폴더를 생성합니다. 과거 y02.xls/y09.xls를 재사용하거나 덮어쓰지 않습니다.
3. Y02 조회·추출·창고 확인 후 Y09를 처리합니다. 두 파일이 모두 정상이어야 등록합니다.
4. 파일 크기/변경 시각 안정화 + Windows 공유 모드 0 읽기로 작성 핸들이 닫혔는지 확인합니다.
5. 기존 CP949 탭 구분 .xls 파서를 사용하고 헤더/정수/중복/창고별 행을 검사합니다.
   현재고·출하대기는 원본 음수도 보존합니다. 빈 창고는 임의로 전량 0으로 해석하지 않고 중단합니다.
6. 파일 SHA256·조회 확인 시각·저장 시각·수집 시작/완료·건수·스냅샷 UUID·상태를 run.json에 기록합니다.
7. 기존 create_snapshot 트랜잭션·감사 이력·권한을 재사용합니다. Y02는 참고, Y09는 기존 대조 방식입니다.
8. 등록 성공 후 결과 기록만 실패했으면 동일 요청 UUID로 결과를 재확인합니다. 중복 등록하지 않습니다.
9. 아직 등록되지 않은 자료는 수집 후 15분이 지나면 expired로 남기고 다음 실행에서 새로 수집합니다.
   이미 커밋된 요청은 시간이 지나도 동일 결과만 확인합니다. 변조된 파일은 재등록하지 않습니다.

두 창고는 순차 조회하므로 동일 DB 트랜잭션 시점의 통합 재고라고 표현하지 않습니다.
자료 기준 시각은 수집 시작이며, 창고별 확인·저장 시각도 보관합니다.
내보낸 원본에는 창고 열이 없으므로 화면의 창고 선택과 조회 완료 검증을 건너뛸 수 없습니다.
실패 로그에는 예외 종류만 기록하고 비밀번호/DPAPI/DB 접속 문자열을 넣지 않습니다.
원본 파일/진단/기록은 운영 PC의 비공개 DataRoot 아래 보관하며 공개 배포물에 포함하지 않습니다.

## 현장 합격 기준 (아직 미완료)

- 실제 Actas에서 Y02·Y09 창고 코드, 조회 완료, 파일 저장 대화상자 제어를 담당자가 확인.
- 수동 추출 결과와 자동 추출 결과의 품목·수량 일치 및 창고 뒤바뀜 없음.
- 실제 대조 등록 1회 및 같은 요청 재확인 시 중복 없음.
- 잘못된 창고/조회 지연/잠금/저장 실패/네트워크 단절 시 오래된 자료를 등록하지 않음.
- SUNIL의 실제 재고·예약·입출고 값 보존과 담당자/원본/실행 이력 확인.

운영 DB에 테스트 자료를 쓰거나 Actas DB에 직접 연결하는 검증은 개발자가 실행하지 않습니다.
