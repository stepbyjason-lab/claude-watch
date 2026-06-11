# Plan: Library root 재배치 — 플랫폼 표준 디폴트 + 오버라이드

날짜: 2026-06-11 · 브랜치: feat/slides-mode

## 무엇을

claude-watch의 라이브러리 루트(`~/claude-watch/library` 하드코딩)를:

1. **공개 디폴트**: 플랫폼 표준 데이터 디렉토리로 변경
   - Windows: `%LOCALAPPDATA%\claude-watch\library`
   - macOS: `~/Library/Application Support/claude-watch/library`
   - Linux: `$XDG_DATA_HOME/claude-watch/library` (없으면 `~/.local/share/...`)
2. **오버라이드 체인** (우선순위 높은 순):
   - `--out-dir` CLI 플래그 (기존 유지)
   - `CLAUDE_WATCH_LIBRARY` 환경변수 (신규)
   - `~/.config/claude-watch/.env`의 `CLAUDE_WATCH_LIBRARY` 키 (신규)
   - 레거시 폴백: `~/claude-watch/library`가 이미 존재하면 그대로 사용 (기존 공개 사용자 캐시 보호)
   - 플랫폼 표준 디폴트
3. **이 머신 마이그레이션**: 634MB 라이브러리(17항목)를 `D:\Work\claude-watch\library`로 이동,
   `.env`에 오버라이드 등록, `C:\Users\<user>\claude-watch\` 완전 제거

## 왜

- Windows에서 홈 루트(`C:\Users\<user>\claude-watch\`)에 폴더가 생김 — 사용자가 가장 싫어하는 위치
- 공개 포크이므로 디폴트는 누구나 납득할 플랫폼 표준 위치여야 함
- 사용자별 선호 경로(D:\Work)는 오버라이드로 해결

## 영향

| 항목 | 내용 |
|---|---|
| 코드 | `scripts/library.py` (resolver 추가), `scripts/setup.py` (resolver 재사용), `scripts/watch.py` (help 텍스트) |
| 테스트 | `tests/test_library.py`에 resolver 테스트 추가 |
| 문서 | `README.md`, `SKILL.md`(2곳), `CHANGELOG.md` |
| 데이터 | `C:\Users\<user>\claude-watch\library` (1,069파일/634MB) → `D:\Work\claude-watch\library` 이동 |
| 삭제 | `C:\Users\<user>\claude-watch\_dl\reel.mp4` (구버전 스크래치 잔재 3.6MB) + 빈 상위 폴더 |
| 설정 | `~/.config/claude-watch/.env`에 `CLAUDE_WATCH_LIBRARY=D:\Work\claude-watch\library` 추가 |
| 기존 공개 사용자 | 레거시 폴백으로 무중단 — `~/claude-watch/library` 있으면 계속 사용 |

## 리스크

- 이동 중 중단 시 일부 항목만 이동됨 → robocopy `/MOVE`는 파일 단위 복사-후-삭제라 재실행으로 이어서 복구 가능
- 다른 세션이 동시에 라이브러리에 쓰는 경우 충돌 → 이동 전 watch 파이프라인 미실행 확인
- slug 캐시는 경로 무관 (meta.json의 source_hash 기반) → 이동 후에도 캐시 히트 유지

## 롤백

- 데이터: `D:\Work\claude-watch\library` → `C:\Users\<user>\claude-watch\library`로 역이동
- 코드: 해당 커밋 revert
- 설정: `.env`에서 `CLAUDE_WATCH_LIBRARY` 라인 제거

## 승인

- [x] 사용자 승인 후 실행 — 2026-06-11 구두 승인 2단계: 코드 변경(완료 기준 3종 확정과 함께), 데이터 이동 각각 별도 승인

## 실행 결과 (2026-06-11)

- 코드: resolver(`scripts/library.py`) + `setup.py` 재사용 + `watch.py` help + 테스트 9개 + 문서 3종 완료
- 테스트: 전체 105 passed, 1 skipped(기존 network 마커) — 무회귀 확인
- 데이터: robocopy /MOVE 1,069파일/634.44MB → `D:\Work\claude-watch\library` 이동 완료(실패 0),
  대상에 기존 1항목 있어 최종 18항목
- 설정: `~/.config/claude-watch/.env`에 `CLAUDE_WATCH_LIBRARY=D:\Work\claude-watch\library` 등록,
  실측 resolve 결과 `D:\Work\claude-watch\library` 확인
- 삭제: `C:\Users\<user>\claude-watch\` 완전 제거(reel.mp4 잔재 포함) 확인
- 리뷰: 3렌즈(파이썬·보안·일반품질) 완료, in-scope 지적 전부 반영 — HIGH 2건 수정(공백 env값,
  삭제 봉쇄 가드), 문서 0600 거짓 주장 정정, copy_local 로컬 입력 검증 추가. 최종 114 passed
- 실증: 해석 3시나리오 + 실파이프라인 실행(산출물 D:\Work 착지·홈 루트 미부활) + 통합테스트 2개 통과
- 승인: 2026-06-11 사용자 최종 승인 — 결과 수용 및 계획 문서 포함 전체 커밋
