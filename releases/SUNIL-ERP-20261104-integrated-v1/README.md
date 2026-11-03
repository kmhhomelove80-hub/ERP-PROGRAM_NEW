# SUNIL ERP 통합 업데이트 — 2026년 11월 4일용

**[업데이트 ZIP 바로 다운로드](https://github.com/kmhhomelove80-hub/ERP-PROGRAM_NEW/raw/refs/heads/downloads/sunil-erp-20261103/releases/SUNIL-ERP-20261104-integrated-v1/SUNIL-ERP-20261104-integrated-v1.zip)**

- [단계별 업데이트 안내](WINDOWS_UPDATE.md)
- [기능 변경 내역](RELEASE_NOTES.md)
- [검증 결과](VALIDATION.json) · [Windows·PostgreSQL 18·브라우저 검사](https://github.com/kmhhomelove80-hub/ERP-PROGRAM_NEW/actions/runs/41675488294)

생산계획조회, 대메뉴별 직원 권한, 이동 대기 알림, 오른쪽 업무 조회 영역과 기존 피킹·검색·메뉴 개선을 포함합니다.

## 실행 순서

1. 회사 ERP 서버에 기존 비밀키를 만든 Windows 계정으로 로그인합니다.
2. ZIP과 압축 해제 폴더를 백신으로 검사합니다. 차단되면 허용하지 말고 중단합니다.
3. 압축을 풀어 `C:\SUNIL\updates\SUNIL-ERP-20261104-integrated-v1`에 둡니다.
4. 같은 계정의 **관리자 Windows PowerShell**에서 사전 점검합니다.

```powershell
& 'C:\SUNIL\ERP\.venv\Scripts\python.exe' 'C:\SUNIL\updates\SUNIL-ERP-20261104-integrated-v1\Update-Erp.py' check
```

**사전 점검 완료**가 나오면 직원들의 저장·처리를 잠시 멈추고 업데이트합니다.

```powershell
& 'C:\SUNIL\ERP\.venv\Scripts\python.exe' 'C:\SUNIL\updates\SUNIL-ERP-20261104-integrated-v1\Update-Erp.py' apply --work-paused
```

백업 → ERP 중지 → 최신 백업·기존 코드 보관 → 파일 반영 → 필요한 0012·0013 마이그레이션·정적 파일 수집 → 재시작·확인 → 업데이트 후 백업을 자동 처리합니다. 완료 후 브라우저를 새로고침하고 생산계획·권한·이동 알림·원장/예약 합계·백업을 확인합니다. 오류·중단 시 [상세 안내](WINDOWS_UPDATE.md)를 따릅니다.

이 ZIP에는 **EXE가 없습니다. 이전에 보류한 EXE는 사용하지 마세요.** 기존 Python으로 업데이트하며 실행 정책·백신 예외·자동 권한 승격을 변경하지 않습니다. app 폴더를 수동 덮어쓰기하지 마세요.

기존 DB·계정·로컬 설정·비밀키·Windows 실행 스크립트·Caddy/예약 작업 정의를 보존합니다. DB 초기화·재이관·자동 DB 복원은 하지 않습니다. 편집용 Word 문서와 실제 운영 자료는 배포물에 포함하지 않습니다.

ZIP SHA256: `5d42cb6519ee85512895464cbc4c3fa94e8d3a5f5eebe7d7d5cbc3de5fd82049`

운영 서버에는 아직 적용하지 않았습니다. Windows 검사는 Windows Server 2025/Python 3.12 CI에서 수행했으며, 실제 Windows 11 서버의 설정·계정·예약 작업 상태는 check에서 먼저 확인해야 합니다.
