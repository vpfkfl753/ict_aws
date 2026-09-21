# 우성원 에이전트 연결

## v2 업데이트 안내

신규 승인·Home 기능 전환은 [v2 배포 체크리스트](V2_ROLLOUT.md)를 먼저 따른다.
중앙 서버 전환과 시간을 맞춰 기존 worker를 중지하고, main 업데이트·의존성 동기화·본인
Slack 권한 재승인 후 재시작한다. 아래 초기 설치의 `chat:write`, `im:write`만으로는
v2 전체 기능의 권한 검사를 통과하지 못한다. `--protocol 2` 검사로 확인한다.
현재 고정 중계 주소는 `https://floral-establish-diffuser.ngrok-free.dev`이며 SSH 터널은 필요 없다.

## 초기 연결 기록

2026-09-21 갱신: 대회 Kiro 로그인 후 이 머신의 `TACIT_BACKEND=kiro`로 실제 Slack 송신
맥락 생성을 확인했다. [검증 기록](KIRO_VALIDATION.md). 아래 9월 15일 상태는 당시 기록이다.

이 문서는 우성원 머신에서 기존 재용 중계 서버에 연결하는 순서다.
전체 구성은 [첫 연결 가이드](FIRST_CONTACT.md), 실행기 로그인은
[Kiro/OpenCode 가이드](../tacit_runtime/README.md)를 참고한다.

## 현재 확인한 상태 (2026-09-15)

- 브라우저는 기존 Chrome `Profile 1`이다. SERA LAB 워크스페이스에 우성원으로 로그인돼 있다.
- 대상 워크스페이스는 `T08TM41TK4P`, 우성원 ID는 프로젝트 설정상 `U0ABNAMVB7U`다. 발급한 사용자 토큰의 실제 소유자를 API로 대조해야 한다.
- [기존 Tacit 앱](https://api.slack.com/apps/A0C1VBWU4N8/oauth)의 새 설정 화면에서 사용자가 직접 권한을 승인했다. 사용자 토큰을 `auth.test`로 확인해 우성원 `U0ABNAMVB7U`, SERA LAB `T08TM41TK4P`, `chat:write`·`im:write` 범위가 일치함을 검증했다.
- 중계 인증 토큰과 본인 Slack 사용자 토큰을 `/home/user/work/ict_aws/.tacit/setup/peer.env`에 권한 `0600`으로 저장했다. Git 제외를 확인했고 실제 토큰 값은 문서에 기록하지 않는다.
- 사용자가 제공한 HTTPS 중계 주소를 위 설정 파일에 저장했다. `/health`는 HTTP 200과 `status=ok`, `protocol=1`을 반환했다. 이 주소에서는 SSH 터널이 필요하지 않다.
- `codex login status`는 ChatGPT 로그인 상태다. `doctor --probe`의 실제 모델 호출이 예제 `results.md`의 B24/p3 조건을 반환했다.
- `tacit-woosung-worker.service`를 사용자 systemd 임시 서비스로 실행했다. `Relay authenticated; worker ready` 로그와 `active/running`, 자동 재시작 횟수 0을 확인했다.
- 현재 자료 폴더는 연결 확인용 `examples/bob`다. 실제 사용자 간 Slack 원문·맥락 교환 테스트는 아직 수행하지 않았다.

## 무엇을 받아야 하나

| 필요한 것 | 용도 | 준비 방법 |
|---|---|---|
| 우성원 `SLACK_USER_TOKEN` | 우성원 명의 DM 전송 | 우성원 Slack 계정으로 앱 승인 후 발급되는 사용자 OAuth 토큰 |
| 우성원 `TACIT_AGENT_TOKEN` | 중계 서버가 우성원 Agent를 식별 | 재용 중계 서버에 이미 등록된 `peer.env`를 전달받기 |
| `TACIT_RELAY_URL` 또는 SSH 접속 정보 | 두 머신 연결 | 실제 중계 호스트의 HTTPS 주소 또는 SSH 터널 경로 |
| 로컬 에이전트 로그인 | 맥락 생성·해석 | Codex, Kiro, OpenCode 중 선택한 실행기의 본인 계정 |

Slack 사용자 토큰과 Tacit Agent 토큰은 서로 다르다. ChatGPT/Kiro 구독 로그인도 별개다.
우성원 워커에는 재용의 사용자 토큰, 중계 관리자 토큰, Slack 봇/앱 토큰이 필요하지 않다.
봇/앱 토큰은 재용 쪽에서 실행 중인 공용 Slack 커넥터가 관리한다.

## 1. 앱 승인과 본인 토큰 발급

앱 관리 화면에서 접근 거부가 나오면 기존 Collaborator에게 초대를 요청한다.
현재 우성원 세션에서는 새 앱 설정 화면으로 이동한 뒤 재설치 경로에 접근할 수 있었다.
위 앱의 **OAuth & Permissions**에서 우성원 계정으로 설치/승인하고,
본인 **User OAuth Token**을 `.tacit/setup/peer.env`의 `SLACK_USER_TOKEN`에 저장한다.
요구하는 사용자 scope는 `chat:write`, `im:write`다.

일반 OAuth 설치 자체가 앱 Collaborator를 반드시 요구하는 것은 아니다. 관리 화면 접근 없이
설치시키려면 앱 운영자가 OAuth 승인 링크와 code 교환 경로를 제공해야 한다.
현재 저장소는 그 다중 사용자 OAuth 콜백을 구현하지 않았으므로 승인 링크만으로 완료된다고 가정하지 않는다.
PF1 브라우저 제어 허용과 Slack 앱 관리 권한은 별개다.

## 2. 재용 서버에 등록된 설정 받기

재용 쪽에서 이미 생성한 **우성원용 `peer.env`만** 받아
`/home/user/work/ict_aws/.tacit/setup/peer.env`에 놓는다. 새 `tacit.setup`을 실행해 임의의
Agent 토큰을 만들면 기존 중계 서버의 등록값과 맞지 않아 인증되지 않는다.

확인할 값:

```dotenv
TACIT_OWNER=U0ABNAMVB7U
TACIT_TEAM_ID=T08TM41TK4P
# TACIT_AGENT_TOKEN: 재용 서버가 우성원에게 배정한 값 유지
# SLACK_USER_TOKEN: 우성원 계정으로 발급한 값
# TACIT_RELAY_URL: 실제 접속 주소 또는 SSH 터널의 loopback 주소
TACIT_WORKSPACE=/home/user/work/ict_aws/examples/bob
TACIT_STATE_DIR=.tacit/U0ABNAMVB7U
TACIT_BACKEND=codex
```

처음에는 작은 예제 폴더로 연결을 확인한다. 실제 자료 폴더는 이후 지정한다.
Kiro나 OpenCode를 선택할 수도 있으며, OpenCode에는 `TACIT_MODEL=openai/<model>`이 필요하다.
2026-09-21 이 머신의 Kiro 로그인과 실제 송신 응답을 확인했다. OpenCode는 앞선 OAuth 갱신 실패 이후 재검증하지 않았다.
설정 파일은 `chmod 600 .tacit/setup/peer.env`로 제한하고 Git에 넣지 않는다.

## 3. 본인 토큰과 모델 확인

프로젝트 루트에서 실행한다. 아래 Slack 검사는 본인 토큰의 소유자·워크스페이스·scope만
확인하며 메시지 전송이나 Socket Mode 연결을 하지 않는다.

```bash
uv run python -m tacit.check_slack --worker-only --worker-env .tacit/setup/peer.env
uv run tacit --env-file .tacit/setup/peer.env doctor --probe
```

두 번째 명령은 선택한 실행기로 실제 모델 호출을 한다. 실제 사용자 자료 대신 위 예제 폴더가
지정돼 있는지 확인한다. 첫 명령 통과는 중계 서버나 모델 인증까지 확인했다는 뜻이 아니다.

## 4. 중계 연결과 워커 실행

재용이 HTTPS 중계 주소를 제공했다면 그 주소를 `TACIT_RELAY_URL`에 넣는다.
SSH 방식이라면 제공받은 실제 접속 정보로 별도 터미널에서 터널을 유지한다.

```bash
ssh -N -L 8765:127.0.0.1:8765 SSH_USER@RELAY_HOST
```

이 경우 우성원의 `TACIT_RELAY_URL=http://127.0.0.1:8765`로 설정한다.
`SSH_USER@RELAY_HOST`는 자리표시자이며 실제 접속 정보가 필요하다.
재용 쪽 중계 및 Slack 커넥터가 실행 중인 상태에서:

```bash
uv run tacit --env-file .tacit/setup/peer.env worker
```

워커는 대기 중인 작업이 있으면 실제 모델을 호출하거나 우성원 명의의 DM을 전송할 수 있다.
공용 중계/Slack 커넥터를 이 머신에서 중복 실행하지 않는다.

마지막으로 Slack의 `/tacit-status`에서 두 에이전트 접속을 확인하고,
[양방향 통신 완료 조건](FIRST_CONTACT.md#5-첫-통신의-완료-조건)을 점검한다.

## 이 머신에서 실행 중인 워커 관리

현재 실행기는 `tacit-woosung-worker.service`라는 systemd 사용자 임시 서비스다.
터미널을 닫아도 사용자 서비스 관리자가 유지되는 동안 실행되며, 재부팅 자동 시작 설정은 하지 않았다.
설정 파일을 변경했다면 서비스를 재시작한다. 같은 사용자 워커를 별도로 중복 실행하지 않는다.

```bash
systemctl --user status tacit-woosung-worker.service
journalctl --user -u tacit-woosung-worker.service -n 30 --no-pager
systemctl --user restart tacit-woosung-worker.service
# 작업을 중지할 때:
systemctl --user stop tacit-woosung-worker.service
```

재부팅 후에는 위 4절의 워커 실행 명령을 사용한다. Cloudflare 중계 주소가 바뀌면
`peer.env`의 `TACIT_RELAY_URL`도 새 주소로 수정해야 한다.

근거: [Slack OAuth 설치 흐름](https://docs.slack.dev/authentication/installing-with-oauth/),
프로젝트 `tacit/setup.py`, `tacit/relay.py`, `tacit/worker.py`, `tacit/check_slack.py`.
