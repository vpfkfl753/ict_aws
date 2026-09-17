# 중앙 서버와 고정 ngrok 주소

2026-09-17 기준 중앙 서버는 재용 머신에 있다. 이전 Cloudflare Quick Tunnel은 더 이상 사용하지 않는다.

- 고정 공개 주소: `https://floral-establish-diffuser.ngrok-free.dev`
- 내부 중계 주소: `http://127.0.0.1:8765` (80번 포트가 아님)
- 중앙 구성: 중계 서버 + Slack Socket Mode 커넥터 + ngrok
- 별도 구성: 각 사용자 머신의 worker와 Codex 로그인

## 일상 명령

설치 후 어느 디렉터리에서나 실행한다. 다음은 각각 독립적인 명령이다.

```bash
tacit-server on
tacit-server off
tacit-server status
tacit-server restart
tacit-server logs
tacit-server logs --follow
```

| 명령 | 효과 |
|---|---|
| `on` | 중앙 서비스 3개 시작, 자동 시작 활성화, 공개 health와 인증 확인 |
| `off` | 중앙 서비스 3개 종료, 자동 시작 비활성화. 다음 부팅에도 꺼진 상태 유지 |
| `status` | 서비스별 상태·PID·재시작 횟수와 target의 자동 시작 여부 |
| `restart` | 중앙 서비스 재시작, 자동 시작 활성화, 공개 경로 확인 |
| `logs` | 중앙 서비스들의 최근 journal 로그. `--follow`로 실시간 조회 |

`on`을 다시 실행해도 정상 프로세스는 중복 생성하지 않는다. 준비 검증에 실패하면 명령은 실패하지만 서비스는 켜진 채 재시도할 수 있다. `logs`로 원인을 확인하거나 `off`로 종료한다. Slack의 실제 WebSocket 연결은 로그 또는 `/tacit-status`로 확인한다.

서비스는 비정상 종료 시 10초 후 재시작한다. worker는 중앙 묶음에 없으며 위 명령으로 시작·종료하지 않는다. 중앙 서버를 끌 때 진행 중인 작업은 중단될 수 있고, 재개 시 임대 만료·재시도 규칙이 적용된다.

## 최초 설치 또는 중앙 머신 이전

Linux와 사용자 systemd를 전제로 한다. 저장소와 `.venv`, 기존 `.tacit/setup/relay.env`, `slack.env`가 필요하다. `uv sync --locked` 후 실행한다.

ngrok은 [공식 설치 안내](https://ngrok.com/download/linux)를 따른다. 현재 머신은 공식 standalone 바이너리를 `~/.local/bin/ngrok`에 설치했다. Apt 설치 버전도 PATH에 있으면 같은 방식으로 사용할 수 있다.

토큰은 채팅이나 저장소에 넣지 않는다. 터미널에서 숨김 입력한다:

```bash
ngrok config add-authtoken "$(systemd-ask-password 'ngrok authtoken:')"
chmod 600 ~/.config/ngrok/ngrok.yml
```

저장소 최상위에서 서비스와 실행 명령을 설치한다:

```bash
uv run tacit-server install --url https://floral-establish-diffuser.ngrok-free.dev
loginctl enable-linger "$USER"
tacit-server on
```

`loginctl`이 권한 오류를 반환하면 관리자가 `sudo loginctl enable-linger "$USER"`를 한 번 실행한다. 현재 재용 머신은 `Linger=yes`를 확인했다. 이 설정은 로그인하지 않아도 사용자 서비스 관리자를 시작하고 로그아웃 후에도 유지한다. 실제 재부팅은 이번 검증에서 수행하지 않았다.

`~/.local/bin`이 PATH에 없다면 `~/.local/bin/tacit-server`를 사용한다. 설치는 서비스를 즉시 시작하지 않으며, 기존 앱이나 토큰을 새로 만들지 않는다. 코드 변경 후에는 `tacit-server restart`로 실행 중인 서비스에 반영한다. 저장소나 가상환경을 이동한 경우 설치를 다시 실행해야 하며, 다른 위치를 가리키는 기존 launcher는 자동으로 덮어쓰지 않는다.

## 저장 위치와 주의점

- `~/.config/systemd/user/tacit-central.target`: 중앙 묶음과 자동 시작 설정
- 같은 디렉터리의 `tacit-{relay,slack,ngrok}.service`: 프로세스 정의
- `~/.local/bin/tacit-server`: 저장소 `.venv/bin/tacit-server`를 가리키는 링크
- `.tacit/setup/server.json`: 공개 URL·ngrok 실행 파일 위치 (Git 제외)
- `~/.config/ngrok/ngrok.yml`: ngrok 인증 설정 (Git 제외, 권한 600)
- `.tacit/relay.sqlite3`: 기존 전달 DB. on/off로 삭제하지 않는다.
- 로그: `journalctl --user`. 이전 `.tacit/services.json`의 수동 PID 관리는 사용하지 않는다.

중계와 Slack 커넥터는 한 개씩만 실행한다. systemd를 사용하는 동안 수동 `tacit relay`, `tacit slack`, `ngrok http`를 함께 실행하지 않는다. 성원 worker는 공개 주소, 중앙 머신 내부 커넥터·worker는 loopback 주소를 사용한다.

ngrok에는 `--inspect=false`를 적용해 로컬 HTTP 요청 내용 수집을 끈다. HTTPS는 ngrok을 경유하며 Agent 간 종단간 암호화는 아니다. `/health`는 공개되지만 `/v1/` 경로는 기존 Bearer 인증을 요구한다. 서비스 파일에는 토큰 값을 넣지 않는다.

## 성원 머신에 반영

최신 코드를 받은 뒤 성원 머신의 `peer.env`에서 다음 값만 갱신하고 worker를 재시작한다. 기존 토큰·자료 폴더는 유지한다.

```dotenv
TACIT_RELAY_URL="https://floral-establish-diffuser.ngrok-free.dev"
```

```bash
uv run tacit --env-file .tacit/setup/peer.env worker
```

중앙 서비스의 재시작마다 이 주소를 바꿀 필요는 없다. 성원 머신에는 ngrok을 설치하지 않는다.

## 한도와 검증

ngrok 무료 계정에는 지정 개발용 도메인 1개와 월 HTTP 요청 20,000회·전송량 1GB 한도가 안내되어 있다. 계정의 실제 플랜·사용량은 대시보드에서 확인한다. [ngrok 공식 한도](https://ngrok.com/docs/pricing-limits/free-plan-limits).

기존 2초 폴링은 원격 worker 하나만으로 하루 약 43,200회 요청을 만들 수 있었다. 최신 코드는 원격 worker가 요청 하나를 최대 180초 유지한다. 유휴 worker 하나의 30일 기본 대기 요청은 약 14,400회 이내이며, 메시지 처리·장애 재시도·기타 트래픽은 추가된다. 로컬 커넥터·worker는 loopback으로 접속하므로 ngrok을 사용하지 않는다. 무료 한도를 보장하거나 유료 플랜으로 자동 전환하는 기능은 없다.

고정 주소는 같은 계정의 지정 도메인을 계속 쓰는 의미다. 머신 전원·인터넷 연결·계정 한도와 무관하게 영구 가동되는 호스팅은 아니다. 새 유료 구독이나 유료 도메인은 신청하지 않았다.

```bash
curl --fail -H 'ngrok-skip-browser-warning: 1' https://floral-establish-diffuser.ngrok-free.dev/health
```

정상 결과는 `{"status":"ok","protocol":1}`이다. 2026-09-17에는 실제 off 후 모든 PID와 8765 listener가 사라지는 것, on 후 같은 공개 주소로 health·401 인증 거부·인증된 조회·Slack Socket Mode 재연결이 성공하는 것을 확인했다.
