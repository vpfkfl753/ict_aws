# 우성원: Tacit 연결 안내

목표는 성원님 머신의 Agent가 로컬 작업 자료를 읽고, 재용님과 Slack으로 대화할 때 필요한 맥락을 교환하도록 연결하는 것입니다.

재용님 쪽에는 Slack 앱, 중계 서버, Slack 커넥터와 재용님 Agent가 준비되어 있습니다. 성원님 쪽에서는 **본인 Slack 사용자 인증과 Codex 로그인을 설정하고 worker 하나를 실행**합니다. 새 Slack 앱이나 중계 서버를 만들 필요는 없습니다.

## 시작 전 재용님에게 받을 것

| 필요한 항목 | 설명 |
|---|---|
| `peer.env` 파일 | 성원님 전용 Agent 인증 설정. 재용 머신의 `/home/jaeyong/ICT/ict_aws/.tacit/setup/peer.env`에 있습니다. Git에는 포함되지 않습니다. |
| 서버 접속 방법 | SSH 호스트·계정·인증 방법 또는 공용 HTTPS 중계 주소. **현재 접속 경로는 아직 확정되지 않았습니다.** |
| Slack 앱 관리 접근 | 아래 기존 앱의 Collaborator 초대. 본인 사용자 토큰을 발급받는 데 사용합니다. |

재용님은 `peer.env`만 별도로 전달합니다. 이 문서와 GitHub에는 토큰 값을 넣지 않습니다. 앱의 `client_secret`, `slack-app.json`, `relay.env`, 재용님 사용자 토큰은 성원님 설정에 필요하지 않습니다.

서버 접속 경로가 아직 없어도 1~4단계까지 먼저 진행할 수 있습니다. 두 머신 간 소통은 5단계의 접속이 되어야 가능합니다.

## 1. 코드와 실행 환경 준비

이 안내의 터미널 명령은 macOS/Linux 또는 Windows의 WSL 환경 기준입니다. Windows에서는 Codex 로그인, 저장소, 설정 파일, worker를 같은 WSL 환경에서 사용하면 경로를 맞추기 쉽습니다.

필요한 도구는 Git, uv, Python 3.11 이상, 로그인 가능한 Codex CLI입니다. 현재 모델 연결은 **Codex CLI**로 구현되어 있습니다.

새로 받는 경우:

```bash
git clone --branch feat/tacit-first-contact https://github.com/vpfkfl753/ict_aws.git
cd ict_aws
uv sync --locked
```

이미 저장소가 있는 경우 저장소 안에서 실행합니다. 작업 중인 변경이 있으면 먼저 보존하고 브랜치를 전환합니다.

```bash
git fetch origin
git switch feat/tacit-first-contact
git pull --ff-only
uv sync --locked
```

저장소가 비공개이므로 GitHub 접근 권한이 필요합니다. HTTPS 인증이 안 되면 본인 GitHub SSH 인증을 사용해 같은 저장소를 받습니다.

## 2. 성원님 전용 설정 파일 배치

이후 모든 명령은 저장소 최상위 `ict_aws/`에서 실행합니다.

```bash
mkdir -p .tacit/setup
```

재용님에게 받은 `peer.env`를 `ict_aws/.tacit/setup/peer.env` 위치에 둡니다. `.tacit`는 숨김 폴더라 파일 탐색기에 바로 보이지 않을 수 있습니다. VS Code를 사용한다면 다음 명령으로 열 수 있습니다.

```bash
code .tacit/setup/peer.env
```

설정은 아래 내용을 확인합니다. `TACIT_AGENT_TOKEN`은 **전달받은 값을 그대로 유지**합니다. 이 표의 설명을 실제 토큰 대신 입력하지 않습니다.

| 항목 | 성원님 설정 |
|---|---|
| `TACIT_OWNER` | `U0ABNAMVB7U` |
| `TACIT_TEAM_ID` | `T08TM41TK4P` |
| `TACIT_AGENT_TOKEN` | 재용님이 생성하여 `peer.env`에 넣은 성원님 전용 값 |
| `TACIT_RELAY_URL` | 5단계에서 확정할 주소 |
| `TACIT_WORKSPACE` | 성원님 머신의 작업 자료 폴더 **절대 경로** |
| `TACIT_STATE_DIR` | 전달받은 `.tacit/U0ABNAMVB7U` 유지 |
| `SLACK_USER_TOKEN` | 3단계에서 발급받을 **성원님 본인**의 `xoxp-...` 토큰 |

각 값의 따옴표는 처음과 끝을 모두 일반 큰따옴표 `"`로 맞춥니다. 예를 들어 `TACIT_OWNER="U0ABNAMVB7U"` 형태입니다.

초기 연결 확인에는 저장소의 `examples/bob/` 폴더를 쓸 수 있습니다. 이 폴더 안에서 `pwd`로 절대 경로를 확인한 뒤 `TACIT_WORKSPACE`에 넣습니다. 실제 소통 단계에서는 성원님의 작업 맥락이 있는 폴더로 바꿉니다.

현재 실행기는 지정 폴더의 텍스트를 최대 32개 파일, 파일당 8,000자, 합계 64,000자까지 모델에 제공합니다. 이번 소통에 사용할 자료가 모인 작은 폴더가 적합합니다. 변경된 환경설정은 worker를 다시 실행해야 반영됩니다.

## 3. 성원님 Slack 사용자 토큰 설정

| 항목 | 값 |
|---|---|
| 워크스페이스 | SERA LAB. (`T08TM41TK4P`) |
| 기존 앱 | [Tacit 앱 관리](https://api.slack.com/apps/A0C1VBWU4N8) |
| 앱 ID | `A0C1VBWU4N8` |
| 성원님 사용자 ID | `U0ABNAMVB7U` |

1. 성원님 Slack 계정으로 로그인하고 기존 Tacit 앱 관리 화면에 들어갑니다. 접근할 수 없으면 재용님에게 앱의 Collaborators 초대를 요청합니다.
2. **OAuth & Permissions**에서 Install/Reinstall to Workspace를 진행합니다. 승인 대상 워크스페이스가 SERA LAB.이고 로그인 계정이 성원님인지 확인합니다.
3. **User OAuth Token**인 `xoxp-...` 값을 확인합니다. 필요한 User Token Scopes는 `chat:write`, `im:write`입니다.
4. 그 값을 성원님 머신의 `peer.env`에 `SLACK_USER_TOKEN="xoxp-..."` 형태로 입력하고 저장합니다.

성원님 worker에는 Bot User OAuth Token(`xoxb-...`)이나 App-Level Token(`xapp-...`)을 넣지 않습니다. 이 토큰들은 재용님 쪽 Slack 커넥터에서 사용합니다. 앱을 재설치한 뒤 재용님에게 완료 사실을 알려 커넥터가 계속 연결되는지 확인합니다.

앱 관리 화면에서 성원님 본인의 User OAuth Token을 얻을 수 없다면 표시되는 화면과 오류 문구만 재용님에게 알려주세요. 다른 사람의 사용자 토큰으로 대신 진행하지 않습니다. 현재 코드는 별도 웹 OAuth 콜백 서버를 제공하지 않습니다.

## 4. Codex 로그인과 파일 기반 응답 확인

```bash
codex login
codex login status
codex exec --help
```

`codex exec --help`에 `--ignore-user-config`, `--ignore-rules`, `--ephemeral` 옵션이 있는지 확인합니다. 없으면 이 구현에서 사용하는 옵션을 지원하는 CLI 버전으로 맞춰야 합니다. 설치 참고: [Codex CLI 문서](https://developers.openai.com/codex/cli/).

설정한 자료를 모델이 해석할 수 있는지 확인합니다.

```bash
uv run tacit --env-file .tacit/setup/peer.env doctor --probe
```

성공 기준은 **실제 파일명과 그 파일에서 확인할 수 있는 내용**이 응답에 나오는 것입니다. 이 검증은 실제 모델을 호출하지만 Slack 메시지를 보내지는 않습니다.

`doctor` 출력의 `SLACK_BOT_TOKEN: missing`, `SLACK_APP_TOKEN: missing`은 성원님 worker 설정에서는 정상입니다. 반면 `SLACK_USER_TOKEN`, `TACIT_AGENT_TOKEN`, `TACIT_OWNER`, `TACIT_TEAM_ID`, `TACIT_WORKSPACE`, `TACIT_RELAY_URL`은 설정되어 있어야 합니다. `doctor`만으로 Slack 사용자 토큰의 유효성을 확인하는 것은 아니며, worker 시작 시 실제 인증 검사가 이루어집니다.

## 5. 재용님 중계 서버에 연결

재용님과 접속 방법을 먼저 확정합니다. **성원님 머신의 `127.0.0.1`은 재용님 머신을 뜻하지 않습니다.** 아래 SSH 터널을 쓰는 경우에만 로컬 주소를 그대로 사용할 수 있습니다.

### SSH 접속이 가능한 경우

아래 `SSH_USER`, `RELAY_HOST`를 재용님에게 받은 값으로 바꿉니다. 이 터미널은 계속 열어둡니다.

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 8765:127.0.0.1:8765 SSH_USER@RELAY_HOST
```

`peer.env`에는 다음을 설정합니다.

```dotenv
TACIT_RELAY_URL="http://127.0.0.1:8765"
```

성원님 로컬의 8765 포트가 이미 사용 중이면 터널의 앞쪽 포트만 8766으로 바꾸고 설정 URL도 8766으로 바꿉니다. 뒤쪽 8765는 재용님 중계 서버 포트입니다.

### HTTPS 주소를 받는 경우

재용님이 제공한 실제 HTTPS 주소를 `TACIT_RELAY_URL`에 입력합니다. 이 경우 SSH 터널은 필요하지 않습니다. 원격 평문 HTTP 주소는 현재 실행기가 거부합니다.

### 연결 확인

설정한 주소 뒤에 `/health`를 붙여 브라우저에서 엽니다. SSH 터널을 사용한다면 `http://127.0.0.1:8765/health`입니다.

정상 응답:

```json
{"status":"ok","protocol":1}
```

이 응답은 네트워크 연결 확인입니다. Agent 인증과 본인 등록은 다음 worker 실행 및 `/tacit-status`에서 확인합니다.

## 6. 성원님 Agent 실행

별도 터미널에서 저장소 최상위로 이동해 실행합니다.

```bash
uv run tacit --env-file .tacit/setup/peer.env worker
```

worker는 성원님 Slack 토큰의 사용자와 워크스페이스를 검증한 뒤 작업을 기다립니다. 아무 로그 없이 대기하는 것은 정상일 수 있습니다. 이 터미널과 SSH 터널을 사용하는 경우 해당 터미널도 유지합니다. worker는 하나만 실행합니다. 종료할 때는 `Ctrl+C`를 사용합니다.

Slack에서 다음을 실행합니다.

```text
/tacit-status
```

성원님과 재용님 모두 `최근 연결됨`으로 표시되면 두 실행기가 연결된 것입니다. 첫 설정에서 연결되었다는 사실을 재용님에게 알려주세요.

## 7. 두 사람이 함께 소통 검증

1. 두 사람이 이번 검증에 사용할 자료 폴더를 정합니다. 예제 검증이면 재용님은 `examples/alice/`, 성원님은 `examples/bob/`로 맞춥니다. 각자 worker를 재시작해 설정을 적용합니다.
2. 재용님이 Slack에서 `/tacit-send @우성원 이번 결과 baseline이랑 비교해봤어?`를 실행합니다. `@우성원`은 Slack 자동완성에서 실제 사용자를 선택합니다.
3. 성원님은 재용님과의 일반 1:1 DM에 원문이 왔는지 확인합니다.
4. 이어서 **Tacit 봇과의 DM**에 성원님의 자료를 반영한 설명이 왔는지 확인합니다. 예제에서는 B17/p2와 B24/p3 차이를 짚는지 확인합니다.
5. `/tacit-receive`로 같은 설명을 다시 조회합니다. 기본값은 가장 최근에 수신한 건입니다.
6. 성원님도 `/tacit-send @이재용 현재 비교는 B24 기준인데 B17 조건으로 다시 맞춰볼까?`처럼 메시지를 보내고, 재용님이 원문과 설명을 받는지 확인합니다.
7. 마지막으로 두 사람의 실제 작업 자료와 실제 질문으로 같은 절차를 반복합니다.

일반 DM에 그냥 입력한 메시지는 현재 자동 분석되지 않습니다. 첫 구현에서는 `/tacit-send`로 보내야 합니다. 원문 DM과 Agent 설명은 비동기로 도착하므로 모델 실행 시간이 걸릴 수 있습니다.

## 막힐 때 전달할 정보

| 증상 | 확인하거나 재용님에게 전달할 내용 |
|---|---|
| `peer.env`를 찾지 못함 | 저장소의 `.tacit/setup/peer.env` 위치, 숨김 폴더 여부 |
| `python-dotenv could not parse` | 해당 줄의 여는/닫는 따옴표가 모두 `"`인지 확인 |
| `SLACK_USER_TOKEN must belong...` | 성원님 계정의 User OAuth Token인지, 워크스페이스가 SERA LAB.인지 확인 |
| `/health`가 열리지 않음 | SSH 연결 오류 또는 받은 HTTPS 주소, 재용님 서버 실행 여부 |
| `Relay unavailable` | 네트워크 연결, `TACIT_RELAY_URL`, 전달받은 Agent 토큰 유지 여부 |
| Codex 옵션/인증 오류 | `codex --version`, 오류 문구, `doctor --probe` 결과 |
| `/tacit-status`가 연결 대기 | worker가 실행 중인지, 올바른 `peer.env`를 사용하는지 확인 |
| 원문만 오고 설명이 안 옴 | Slack 접수 시 표시된 전달 ID와 worker의 실패 단계 |

문제 공유 시 토큰 값과 전체 `.env` 내용은 보내지 않습니다. 필요한 것은 오류 문구, 전달 ID, 사용한 명령, 연결 상태입니다.

## 재용님에게 완료 보고

아래 상태를 알려주면 다음 검증으로 이어갈 수 있습니다.

```text
- 코드 브랜치: feat/tacit-first-contact
- 내 Slack 사용자 토큰 설정: 완료 / 막힘
- Codex 파일 기반 응답 확인: 성공 / 실패
- 중계 /health: 성공 / 실패
- worker 실행: 실행 중 / 실패
- /tacit-status: 두 사람 최근 연결됨 / 그 외 결과
```
