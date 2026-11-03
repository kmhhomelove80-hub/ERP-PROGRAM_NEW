# SUNIL ERP 2026-11-03 개발 검증 결과

기준 커밋: `d425d71`. 최종 코드 커밋과 파일별 SHA-256은 배포 ZIP의 `manifest.json`을 확인하세요. 이 문서의 테스트는 개발 환경과 임시 데이터에서 수행했으며 운영 서버·운영 DB에는 접속하거나 변경하지 않았습니다.

## 자동 검증

| 항목 | 결과 |
|---|---|
| Django 전체 테스트 — Python 3.12.14 / Django 5.2.18 / SQLite | 226건, 성공 214 / 조건부 건너뜀 12 / 실패 0 |
| Django 전체 테스트 — PostgreSQL 18.6 | 226건, 성공 221 / SQLite 전용 등 조건부 건너뜀 5 / 실패 0 |
| Django system check | 오류 없음 |
| makemigrations --check --dry-run | No changes detected — 신규 migration 없음 |
| 의존성 파일 | d425d71 대비 변경 없음 |
| 자동 배정 | 요청 40:A32/B40→B40; 37:A34/B3/C100→A34+B3; 40:A45/B100→A40; 50:A35/B30/C20→B30+C20 |
| 정확성 추가 확인 | 고정 난수 450건을 전수조합 정답과 비교, 모두 일치 |
| 상태·가용량 | 다른 예약 차감, 예정/완료 동등 선택, 동일 위치 여러 행 합산, 계산 한도 명시 중단 |
| 예약 전 수동 조정 | 서명된 미리보기, 합계·품목·가용량 검증, 중복 확정 방지 |
| 예약 후 위치 변경 | 총수량·주문 보존, 여러 재고로 분할, 원자적 예약 변경, 변경 이력, 오래된 화면·다른 품목·다른 예약 침범·출고 완료·작업자 거부, 감사 기록 실패 시 전체 롤백 |
| PostgreSQL 동시성 | 경쟁 예약·이동·추가 피킹 등 기존 검사, 두 예약 변경의 가용량 경쟁, 동일 변경 재전송, 실제 출고와 예약 변경의 경쟁 통과 |
| 작업자 | 이동 요청 등록/조회 가능, 등록만으로 재고 불변, 승인·실행과 다른 업무 쓰기 차단 |
| 정리 리스트 | 완료 재고만, 위치 합계 3 이하, 예약 위치 제외, 최대 보유 적격 도착, 비울 위치 수 우선, 출력 재고 불변 |
| Binary Eye 미들웨어 | 루트 GET/scan/빈 q/길이/인코딩/제어문자/기존 q/로그인/특수문자·한글 검색, 반복 리다이렉트 없음 |
| 실제 Chromium + 로컬 Waitress | PC 메뉴·수동 미리보기·예약 후 분할 변경, Android 크기 작업자 이동 요청·처리 대기, 정리 리스트 인쇄, 스캔 반환 검색, 앱 호출 URI·대체 안내, JS 오류 0 |
| 모바일 크기 | 360 / 390 / 768px에서 전체 페이지 가로 넘침 없이 메뉴 접근 |
| PowerShell | 7.4.6 Linux에서 문법 검사, 정상 stderr와 종료 0, 비정상 종료 7 보존, 설정 실패 1, stdout/stderr/lifecycle 로그 유지 |
| 파일 업데이트·되돌리기 | 사전 검증 시 무변경, 실제 코드 복사/되돌리기, 설정/DPAPI/DB/Caddy/백업 스크립트/venv 보존, 변조 해시·경로 이탈 거부, 설치 후 별도 수정 파일의 덮어쓰기 거부 |
| PostgreSQL 백업/복원 | PG18 custom dump → SHA-256 → 모의 별도 저장소 복사 → 격리된 새 DB 복원, 모든 업무 테이블·계정 지문 일치, 예약 변경 이력 보존, 비어 있지 않은 DB 복원·손상 백업 거부 |
| 매뉴얼 | HTML·PDF·Word·Markdown 개정, 프로그램 내 검색·PDF 테스트 통과 |

SQLite에서 건너뛴 PostgreSQL 동시성 테스트는 별도 PostgreSQL 실행에서 검증했습니다. PostgreSQL에서 건너뛴 항목은 SQLite 파일/노트북 전용 검사입니다. 실제 운영 DB를 테스트 DB로 사용하지 않았습니다. 모의 별도 저장소 검증은 실제 NAS 연결 성공을 뜻하지 않습니다.

## 재실행

격리된 개발 DB에서만 실행합니다. 운영 환경 설정을 상속한 셸에서 `manage.py test`나 아래 브라우저 검증을 실행하지 마세요.

```bash
python manage.py test --noinput
python manage.py makemigrations --check --dry-run
python scripts/smoke_update.py
```

브라우저 검증은 requirements-dev.txt의 Playwright와 로컬 Chromium(`/usr/bin/chromium`)을 사용하고 별도 임시 SQLite DB·가상 계정·로컬 포트의 Waitress를 띄운 뒤 제거합니다. 위 전체 테스트의 PostgreSQL 실행은 개발 전용 로컬 Docker PG18 컨테이너를 사용했습니다.

## 현장 확인이 필요한 범위

- Windows PowerShell **5.1**, 실제 kimmy DPAPI 해독, Windows 예약 작업의 기동·종료·로그와 다음 정시 로컬 백업. Linux PowerShell 검증은 실제 Windows 현장 검증을 대체하지 않습니다.
- 설치된 실제 Binary Eye 앱·버전·Android 브라우저에서 앱 실행과 스캔 후 ERP 복귀. 개발 Chromium의 앱 설치/외부 프로토콜 정책으로 실제 앱 실행 자체는 검증하지 않았으며, 공식 소스에 따른 URI 구성과 ERP 대체 안내·반환 검색을 검증했습니다.
- 고정 IP `192.168.0.252`의 현장 적용 상태와 회사 Wi-Fi 접근. NAS·외부 HTTPS는 이번 업데이트에서 완료하지 않습니다.
- 정리 목적지의 실제 공간·품목 보관 조건과 작업자가 확인한 실제 수량.

정확한 현장 순서는 [Windows 업데이트 절차](WINDOWS_UPDATE_20261103.md)의 백업 → 서버 중지 → 코드 반영 → migration 점검/collectstatic → 재시작 → 확인 → 업데이트 후 로컬 백업을 따르세요. 이번 배포에 새 migration은 없습니다.
