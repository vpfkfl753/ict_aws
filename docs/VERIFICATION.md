# 첫 연결 구현 검증 기록

## Slack 설정 도구 통합 검증 (2026-09-15)

- first-contact 브랜치의 `1e174a9`를 통합했다. 실행기 선택 안내와 Slack 설정·인증 점검 안내를 함께 유지했다.
- `uv run pytest -q`: 34개 통과.
- `uv run ruff check tacit tacit_runtime tests scripts` 및 `git diff --check`: 통과.
- `uv run python scripts/smoke.py`: 실제 loopback HTTP와 별도 워커 2개로 완료. 모델·Slack 전송은 모의 처리다.
- 이번 통합에서는 Slack 앱을 생성하거나 실제 Slack API·모델을 호출하지 않았다. 아래 실제 Slack 확인 결과는 재용 머신에서 작성한 브랜치의 검증 기록이다.

## Kiro/OpenCode 통합 검증 (2026-09-15)

- 두 작업 브랜치를 병합하고 `.gitignore` 규칙을 통합했다.
- `TACIT_BACKEND`로 Codex/Kiro/OpenCode를 선택하는 워커와 doctor 경로를 연결했다.
- `uv run pytest -q`: 28개 통과. Kiro → OpenCode 및 반대 방향의 모의 ACP 교환, 소스 전달, 응답 검사, 세션 분리, 인증 환경변수 제외를 포함한다.
- `uv run ruff check tacit tacit_runtime tests scripts`: 통과.
- `uv run python scripts/smoke.py`: 실제 TCP HTTP 서버와 별도 워커 프로세스 2개로 완료. 모델 및 Slack은 모두 모의 처리했고 외부 메시지는 보내지 않았다.
- wheel 빌드 성공. `tacit`와 `tacit_runtime`이 모두 포함되는 것을 확인했다.
- OpenCode의 저장된 OAuth 토큰은 갱신 시 401 오류가 발생했고, Kiro는 미로그인 상태다. 두 실행기의 실제 구독 모델 호출 성공은 아직 검증하지 못했다.

아래는 first-contact 브랜치에서 수행한 기존 Codex 검증 기록이며, 이번 통합에서는 유료/구독 모델 호출을 재실행하지 않았다.

## first-contact 브랜치의 Codex 및 Slack 검증 기록

검증일: 2026-09-15. 재용 머신에서 수행하고 first-contact 브랜치에 기록한 결과다. 이 기록을 병합한 것이며, 다른 머신의 Slack 인증이나 연결 상태가 동일하다는 뜻은 아니다.

## 검증된 것

- 자동 테스트 18개: 양방향 전달, 원문과 맥락 연결, 사용자별 작업 권한, 요청 중복 방지, 임대 만료 재할당, 모델 실패 표시, 기존 원문 재사용, Slack ack 순서, 입력 파싱, 파일 수집 범위·크기 제한, 설정 생성, Slack 사용자 토큰 소유자 검사, 앱 생성·중복 생성 방지·실패 처리 및 설치 확인.
- 실제 Slack manifest 검증과 앱 생성 성공: `T08TM41TK4P`의 `A0C1VBWU4N8`. 자격증명은 로컬 `.tacit/setup/slack-app.json`에 저장하고 Git에서 제외했다.
- Slack 설치 인증 확인: 봇 토큰의 워크스페이스와 scope, 재용의 사용자 토큰 소유자와 scope, 앱 토큰의 Socket Mode 인증을 실제 API로 확인했다.
- 실제 Slack Socket Mode WebSocket 연결이 열렸고, 재용 worker가 중계 서버에 접속하는 것을 확인했다. 성원 worker는 아직 접속 전이다.
- `uv run ruff check tacit tests scripts`: 통과.
- `tacit doctor --probe`: 실제 Codex 모델이 로컬 실행기가 읽은 예제 파일명과 B17의 전처리 조건을 반환했다.
- `uv run python scripts/smoke.py --live-model`: 실제 TCP HTTP 서버와 별도 worker 프로세스 2개, 실제 Codex 호출 2단계를 거쳐 완료했다. 이 검증의 Slack 전송은 모의 처리였으며 실제 사용자에게 메시지를 보내지 않았다.
- 실제 모델은 송신 자료의 B17/p2와 수신 자료의 B24/p3 차이를 찾아냈으며, 수신 자료의 정확도 2포인트 개선이 B17 대비 동일 조건 비교를 의미하지 않는다고 설명했다.

재현 명령:

```bash
uv sync --locked
uv run pytest -q
uv run ruff check tacit tests scripts
uv run python scripts/smoke.py --live-model
```

마지막 명령에는 Codex 로그인이 필요하며 실제 모델 호출이 발생한다. `--live-model` 없이 실행하면 모델도 모의 처리한다. 어느 쪽도 실제 Slack을 호출하지 않는다.

## 발견하고 처리한 환경 문제

Codex 모델 연결은 되었지만 내부 파일 읽기 명령이 현재 머신의 sandbox 초기화 오류로 실패했다. 로컬 Python 실행기가 지정 폴더의 텍스트를 읽어 모델 입력에 넣도록 변경했다. 읽기 전용 sandbox를 해제하지 않았다. 이 단계의 파일 수집은 제한된 텍스트 스캔이며 Agent의 자율적인 전체 머신 탐색이나 클라우드 연동을 구현한 것은 아니다.

테스트에서 설치된 FastAPI/Starlette 의존성의 deprecation warning 2개가 발생했다. 테스트 실패는 없었다.

## 아직 검증하지 않은 것

- 성원 사용자의 OAuth 토큰 연결 검증.
- 실제 Socket Mode 명령 수신, 사람 명의의 1:1 DM 전송, 수신자 봇 DM 표시.
- 성원 머신에서 모델 호출·파일 수집·중계 연결.
- 서로 다른 머신/네트워크에서의 통신과 실제 업무 자료 기반 양방향 소통.

따라서 **첫 마일스톤은 아직 완료가 아니다.** 코드 기반의 동작 경로와 현재 머신의 실제 모델 연결까지 검증한 상태다. 실사용 완료 조건은 `FIRST_CONTACT.md`에 있다.
