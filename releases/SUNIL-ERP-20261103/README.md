# SUNIL ERP 2026-11-03 다운로드

기준 커밋 `d425d71`, 패키지 소스 `1460a6c`. 이 폴더는 검증한 배포 ZIP과 업데이트 안내를 제공하기 위한 다운로드 게시물입니다. 운영 서버에는 자동 배포하지 않습니다.

- [배포 ZIP 다운로드](https://github.com/kmhhomelove80-hub/ERP-PROGRAM_NEW/raw/refs/heads/downloads/sunil-erp-20261103/releases/SUNIL-ERP-20261103/SUNIL-ERP-20261103.zip)
- [처음 하는 서버 업데이트 — 단계별 설명](BEGINNER_WINDOWS_UPDATE_20261103.md)
- [브라우저용 안내서 다운로드](BEGINNER_WINDOWS_UPDATE_20261103.html) — 내려받아 더블클릭하면 명령 블록을 복사하며 볼 수 있습니다.
- [정확한 Windows 명령 절차](WINDOWS_UPDATE_20261103.md)
- [기능·동률·계산 한도](RELEASE_NOTES_20261103.md)
- [개발 검증 결과](VALIDATION_20261103.md)
- [파일 체크섬](SHA256SUMS.txt)

## 업데이트 순서

1. 서버의 kimmy 계정으로 로그인합니다.
2. 이 폴더의 **SUNIL-ERP-20261103.zip**을 다운로드합니다. 저장소 전체 Source ZIP을 사용하지 않습니다.
3. ZIP을 `C:\SUNIL\updates`에 풀고, `C:\SUNIL\updates\SUNIL-ERP-20261103\manifest.json`이 있는지 확인합니다.
4. 초보자용 안내서를 열고 같은 PowerShell 창에서 각 코드 블록을 순서대로 실행합니다.
5. 사전 점검 → 로컬 백업/검증 → 서버 중지 → 파일 반영 → migration 점검/collectstatic → 재시작 → 화면 확인 → 새 로컬 백업 순서입니다.

신규 migration과 의존성 변경이 없습니다. 기존 DB·계정·로컬 설정·DPAPI·Caddy 설정과 HTTP/DEBUG=1을 유지합니다. DB 초기화·재이관·운영 DB 덮어 복원을 하지 않습니다.

실제 Windows PowerShell 5.1 예약 작업·DPAPI 및 Android Binary Eye는 현장에서 최종 확인합니다. 실패하면 안내서의 코드 되돌리기 절차를 사용합니다.

ZIP SHA-256: `1e02734b8c8742de7985a93e50d33b1b1920f3e9cbb0c8ddf2de9d539104ddea`

이 브랜치는 다운로드 게시용입니다. 프로그램 변경은 ZIP의 app 폴더에 들어 있습니다. 이 브랜치를 main에 합쳐 운영 배포하는 절차는 수행하지 않습니다.
