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

검증일: 2026-09-15, 2026-09-17. 실제 Codex 호출은 9월 15일에 수행했고, 9월 17일 중앙 운영 검증에서는 추가 모델 호출을 하지 않았다.
9월 15일 Slack/Codex 기록은 재용 머신에서 수행한 결과이며, 다른 머신의 인증·연결 상태가 동일하다는 뜻은 아니다.

## 2026-09-17 중앙 운영 검증

- 공식 standalone ngrok 3.39.11을 설치하고 사용자가 로컬 기본 설정에 authtoken을 등록했다. 토큰은 저장소·서비스 정의에 넣지 않았고 설정 파일은 권한 600으로 제한했다.
- 고정 `https://floral-establish-diffuser.ngrok-free.dev`에서 실제 health 응답, 인증 없는 `/v1/agents`의 401, bridge 인증 조회를 확인했다.
- systemd 사용자 서비스 3개와 target을 설치했다. `off` 후 모든 PID가 0이고 8765 listener가 없으며 target이 disabled인 것, `on` 후 enabled·active로 복구되고 같은 URL과 Slack Socket Mode가 연결되는 것을 확인했다.
- `Linger=yes` 설정을 확인했다. 실제 OS 재부팅은 하지 않았다.
- 원격 long polling의 요청 한도·시간 제한·작업 도착·timeout·인증·기존 즉시 조회 호환성, worker 재시도 backoff, 서비스 생성·반복 설치·기존 파일 보호·on/off 명령을 테스트에 추가했다. 실제 systemd parser로 공백·특수문자 경로의 unit도 검증한다.
- 자동 테스트 41개, `ruff check`, `ruff format --check`, 모의 모델·모의 Slack을 사용하는 실제 TCP smoke를 통과했다.
- ngrok 경유의 180초 대기 전체 구간과 성원 머신의 갱신된 worker는 아직 실제 검증하지 않았다. 성원 머신에서 최신 코드와 고정 주소 적용 후 확인한다.

## 2026-09-15 검증

## 검증된 것

- 당시 자동 테스트 22개: 양방향 전달, 원문과 맥락 연결, 사용자별 작업 권한, 요청 중복 방지, 임대 만료 재할당, 모델 실패 표시, 기존 원문 재사용, Slack ack 순서, 입력 파싱, 파일 수집 범위·크기 제한, 설정 생성, Slack 사용자 토큰 소유자 검사, 앱 생성·중복 생성 방지·실패 처리, 설치 확인, 전송 창 열기·입력 검증·접수·재시도.
- 사용자가 `/tacit-send` 실행 후 사용법 안내가 표시됐다고 보고했다. 해당 요청의 원문은 기록하지 않아 정확한 입력은 확인하지 않았다. 인수가 없거나 수신자를 해석하지 못하면 사용자 선택과 메시지 입력 창을 열도록 수정했다. 창 입력과 전송의 실제 Slack 동작은 후속 사용자 실행에서 확인한다.
- 실제 Slack manifest 검증과 앱 생성 성공: `T08TM41TK4P`의 `A0C1VBWU4N8`. 자격증명은 로컬 `.tacit/setup/slack-app.json`에 저장하고 Git에서 제외했다.
- Slack 설치 인증 확인: 봇 토큰의 워크스페이스와 scope, 재용의 사용자 토큰 소유자와 scope, 앱 토큰의 Socket Mode 인증을 실제 API로 확인했다.
- 실제 Slack Socket Mode WebSocket 연결이 열렸고, 재용·성원 worker 모두 각 사용자 인증으로 중계 서버를 주기적으로 조회하는 것을 확인했다. 성원 worker는 외부 HTTPS 터널을 통해 접속했다.
- 실제 `/tacit-status` 명령 수신 로그와 두 사용자 모두 연결되었다는 사용자 확인을 확보했다. SSH 연결은 현재 통신에 필요하지 않다.
- 종료 전 운영 DB를 재점검하여 재용 → 성원 전달 `b5620d7c-636b-48ad-acb6-6ba24ec2857f`의 `completed`, `notified=1`, 오류 없음 상태를 확인했다. 원문 Slack 채널·메시지 시각이 기록되어 있고 송신 맥락 663자와 수신 해석 862자가 저장되어 있다. 재용 worker의 실제 prepare 완료 로그도 확인했다. 이는 모의 테스트 DB가 아닌 실제 실행 경로의 기록이며, 성원님의 화면 열람·설명 품질 확인을 대신하지 않는다.
- 외부 HTTPS 터널 개통: 공개 주소의 `/health` 정상 응답, 인증 없는 `/v1/agents` 요청의 401 거부, 올바른 인증을 가진 조회 성공을 확인했다. 성원용 `peer.env`에 HTTPS 주소를 반영했다.
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

- 성원님의 화면에서 원문 DM과 봇 설명을 실제로 읽었는지 확인.
- 성원 → 재용 반대 방향의 실전송.
- 성원 머신의 구체적인 자료 수집 범위와 모델 실행 로그 직접 점검. 현재 증거는 원격 Agent의 처리 결과가 인증된 중계 경로로 돌아왔다는 것이다.
- 전송 창의 실제 Slack 열기·제출 동작.
- 실제 업무 자료 기반 양방향 맥락 전달의 유용성과 사용자 확인.

따라서 **실제 단방향 기술 경로는 통과했지만, 첫 마일스톤 전체 완료 판정은 보류한다.** 실사용 완료 조건은 `FIRST_CONTACT.md`에 있다. 종료 시점 상태와 재개 절차는 [마일스톤 점검](MILESTONE.md)에 기록했다.
