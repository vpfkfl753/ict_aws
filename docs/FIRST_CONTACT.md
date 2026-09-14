# 실제 두 사용자 첫 연결

## 참여자

| 항목 | 값 |
|---|---|
| Slack 워크스페이스 | `T08TM41TK4P` |
| 이재용, 현재 머신 | `U09F4SENS3X` |
| 우성원, 동료 머신 | `U0ABNAMVB7U` |

완료 조건은 두 사람이 실제 Slack 원문과 Agent 설명을 확인하는 것이다. 아래에는 아직 수행하지 않은 실사용 체크도 포함되어 있다.

## 1. Slack 앱 생성

현재 팀의 Tacit 앱은 **생성 완료**다: [`A0C1VBWU4N8`](https://api.slack.com/apps/A0C1VBWU4N8).
이 앱을 사용할 때는 새로 생성하지 않고 아래 설치·토큰 설정 단계부터 진행한다.

1. [Slack 앱 관리](https://api.slack.com/apps)에서 **Create New App → From an app manifest**를 선택한다.
2. `T08TM41TK4P` 워크스페이스를 선택하고 저장소의 `slack-manifest.json`을 입력한다.
3. 앱 생성 후 **Basic Information → App-Level Tokens**에서 `connections:write` 권한을 가진 토큰을 만든다. `xapp-...` 토큰을 `.tacit/setup/slack.env`의 `SLACK_APP_TOKEN`에 설정한다.
4. **OAuth & Permissions → Install to Workspace**로 설치한다. Bot User OAuth Token `xoxb-...`를 같은 파일의 `SLACK_BOT_TOKEN`에 설정한다.
5. User OAuth Token `xoxp-...`는 설치한 사용자 본인의 `.tacit/setup/owner.env` 또는 `peer.env`의 `SLACK_USER_TOKEN`에 설정한다. 토큰 소유자는 worker 시작 시 `auth.test`로 검증한다.
6. 우성원을 앱의 **Collaborators**에 추가하고, 우성원도 본인 Slack 계정으로 앱 설치/승인을 진행하여 **자기 계정의 User OAuth Token**을 얻는다. 재용의 사용자 토큰을 성원 설정에 넣으면 실행이 거부된다.
7. Socket Mode가 켜졌고 `/tacit-send`, `/tacit-receive`, `/tacit-status`가 등록되었는지 확인한다. 별도의 DM 이벤트 구독은 필요하지 않다.

앱 관리 화면에서 두 번째 사용자 토큰을 발급할 수 없는 조직 정책이라면 앱 관리자에게 사용자 OAuth 설치 경로를 요청해야 한다. 이 저장소는 다중 사용자 웹 OAuth 콜백 서버를 구현하지 않는다. 필요한 사용자 scope는 `chat:write`, `im:write`이며 봇의 권한과 별개다.

`/tacit-send`의 원문을 사람 명의로 보내는 데 사용자 토큰을 사용한다. 봇 토큰만으로 사람 간 DM을 보내는 것으로 대체하지 않는다. 자동 결과 설명은 Tacit 봇이 수신자와의 DM으로 전송한다.

## 2. Agent 호출 확인

`.tacit`는 숨김 폴더다. 프로젝트 루트에서 다음 명령으로 두 설정 파일을 편집기에 열 수 있다.

```bash
code .tacit/setup/slack.env .tacit/setup/owner.env
```

토큰을 입력한 뒤 다음 명령으로 워크스페이스·사용자 소유자·scope·Socket Mode 인증을 검증한다. 메시지를 보내지 않으며 실제 WebSocket 연결을 유지하지 않는다.

```bash
uv run python -m tacit.check_slack
```

수신자 설정을 확인하려면 `--worker-env .tacit/setup/peer.env`를 지정한다.

각 머신에 선택한 실행기를 설치하고 자신의 계정으로 로그인한다. 기본값은 Codex이며 `codex login`을 사용한다. 대회 Kiro 구독은 `TACIT_BACKEND=kiro`, ChatGPT 구독의 OpenCode 연결은 `TACIT_BACKEND=opencode`로 선택한다. OpenCode에는 `TACIT_MODEL=openai/<model>`도 설정한다. 자세한 로그인 방법은 [실행기 가이드](../tacit_runtime/README.md)를 따른다. 설정의 `TACIT_WORKSPACE`를 해당 머신에 실제 존재하는 폴더로 바꾼다.

```bash
uv run tacit --env-file .tacit/setup/owner.env doctor --probe
```

성원은 `peer.env`를 사용한다. `--probe`는 선택한 실행기로 지정 폴더의 텍스트를 전달하고 실제 모델이 그 파일 근거로 응답하는지 확인한다. Slack 메시지는 보내지 않는다. 파일명과 그 파일에 실제 존재하는 내용을 반환해야 한다. 네이티브 CLI 버전·인증·모델 호출·파일 수집을 이 단계에서 검증한다.

## 3. 두 머신이 같은 중계 서버에 접속

중계 서버 프로세스는 재용 머신, 팀의 개발 서버 또는 호스팅 환경 중 한 곳에서 실행한다. 원격에서 직접 사용할 때에는 HTTPS로 노출한다. 원격 평문 HTTP 주소는 실행기가 거부한다.

가장 먼저 확인할 수 있는 방식은 **SSH 터널**이다. 성원 머신에서 재용 머신 또는 중계 호스트에 SSH 접속이 이미 가능하다면:

```bash
ssh -N -L 8765:127.0.0.1:8765 SSH_USER@RELAY_HOST
```

이 경우 성원 `peer.env`의 `TACIT_RELAY_URL`도 `http://127.0.0.1:8765`로 둔다. 로컬 HTTP가 SSH로 암호화되어 중계 호스트의 loopback 서버에 연결된다. 다른 앱이 8765를 사용하면 터널의 첫 포트를 바꾸고 설정도 맞춘다.

SSH 접속 경로가 없다면 공통 HTTPS 주소를 제공하는 호스트나 HTTPS 터널을 준비한 뒤, 원격 클라이언트들의 `TACIT_RELAY_URL`을 그 주소로 변경한다. 배포 계정과 호스트는 아직 지정되지 않았으며 자동으로 외부 배포하지 않는다.

중계 서버의 공개 주소는 Slack 이벤트 수신 주소가 아니다. Slack 커넥터는 Socket Mode로 Slack에 접속한다. 두 Agent 사이의 맥락은 Slack을 통과하지 않는다.

## 4. 프로세스 시작

중계 호스트:

```bash
uv run tacit --env-file .tacit/setup/relay.env relay
```

Slack 커넥터는 팀에서 하나만:

```bash
uv run tacit --env-file .tacit/setup/slack.env slack
```

재용 머신:

```bash
uv run tacit --env-file .tacit/setup/owner.env worker
```

성원 머신:

```bash
uv run tacit --env-file .tacit/setup/peer.env worker
```

Slack에서 `/tacit-status`로 두 Agent가 최근 연결되었는지 확인한다. 한쪽이 꺼져 있으면 작업은 대기한다. 동일 사용자의 worker를 여러 개 켜지 않는다.

## 5. 첫 통신의 완료 조건

- [ ] 재용이 `/tacit-send @우성원 이번 결과 baseline이랑 비교해봤어?`를 실행한다.
- [ ] 성원과의 기존 1:1 DM에 재용 명의의 원문이 도착한다.
- [ ] 재용 worker에 `prepare completed`가 기록된다.
- [ ] 성원 worker에 `interpret completed`가 기록된다.
- [ ] 성원의 Tacit 봇 DM에 **재용의 자료와 성원의 자료 차이를 반영한 설명**이 도착한다.
- [ ] 성원이 `/tacit-receive`로 같은 결과를 다시 조회한다.
- [ ] 성원이 메시지를 보내고 재용이 설명을 받는 반대 방향도 성공한다.
- [ ] 원문에 쓰지 않은 내용이 실제 각자 자료에서 나왔는지 두 사람이 확인한다.

예시 확인용 자료는 `examples/alice/`, `examples/bob/`에 있다. 재용 쪽 baseline은 B17/p2, 성원 쪽은 B24/p3이다. 예시로 연결을 확인한 다음 **실제 두 사람의 작업 자료와 질문**으로 같은 절차를 반복한다.

## 장애 구분

| 증상 | 확인할 것 |
|---|---|
| Slack 명령을 찾지 못함 | 앱 설치, 워크스페이스, manifest 명령 등록 |
| 명령은 접수되지만 원문이 안 옴 | 송신 worker 접속, 사용자 토큰 소유자·scope |
| 원문만 오고 설명이 안 옴 | 두 worker 로그의 exchange ID, 선택 실행기의 probe, 수신 worker 접속 |
| `prepare_failed` / `interpret_failed` | 해당 머신의 실행기 인증·폴더·모델 연결, 수정 후 명령 재실행 |
| 중계 재시작 뒤에도 대기 | 동일 SQLite 경로 사용 여부, 종료된 작업의 10분 lease 만료 |
| 설명은 조회되지만 봇 DM이 안 옴 | Slack 커넥터 실행, 봇 토큰, `chat:write`·`im:write` |

로그는 exchange ID와 단계 중심이며 토큰·로컬 원문은 기록하지 않는다. 실패 후 같은 명령을 새로 입력하면 새 DM이 전송된다.

## 다른 워크스페이스에 앱 생성

Slack App Configuration Token을 `slack.env`의 `SLACK_CONFIG_TOKEN`에 설정한 뒤 자동 생성 도구를 사용할 수 있다. 기존 팀 앱에는 다시 실행할 필요가 없다.

```bash
uv run python -m tacit.provision validate
uv run python -m tacit.provision create
```

생성 도구는 manifest를 먼저 검증하고 생성된 앱 ID와 자격증명을 `.tacit/setup/slack-app.json`에 비공개 파일로 저장한다. 재실행하면 기록된 앱을 반환한다. 생성 요청 도중 네트워크가 끊기면 중복 앱 생성을 막기 위해 중단하므로 Slack 관리 화면과 로컬 기록을 확인한다. 구성 토큰은 최초 앱 관리에만 필요하고 서비스 실행에는 사용하지 않는다.

공식 근거: [manifest로 앱 생성](https://docs.slack.dev/reference/methods/apps.manifest.create/), [Socket Mode 연결 확인](https://docs.slack.dev/reference/methods/apps.connections.open/).
