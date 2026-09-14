# 첫 연결 구현 검증 기록

검증일: 2026-09-15. 현재 머신의 로그인된 Codex CLI로 검증했다.

## 검증된 것

- 자동 테스트 12개: 양방향 전달, 원문과 맥락 연결, 사용자별 작업 권한, 요청 중복 방지, 임대 만료 재할당, 모델 실패 표시, 기존 원문 재사용, Slack ack 순서, 입력 파싱, 파일 수집 범위·크기 제한, 설정 생성, Slack 사용자 토큰 소유자 검사.
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

- `T08TM41TK4P`에 실제 Slack 앱 생성·설치 및 두 사용자의 OAuth 토큰 발급.
- 실제 Socket Mode 명령 수신, 사람 명의의 1:1 DM 전송, 수신자 봇 DM 표시.
- 성원 머신에서 모델 호출·파일 수집·중계 연결.
- 서로 다른 머신/네트워크에서의 통신과 실제 업무 자료 기반 양방향 소통.

따라서 **첫 마일스톤은 아직 완료가 아니다.** 코드 기반의 동작 경로와 현재 머신의 실제 모델 연결까지 검증한 상태다. 실사용 완료 조건은 `FIRST_CONTACT.md`에 있다.
