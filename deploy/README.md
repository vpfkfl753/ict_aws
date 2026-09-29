# 클라우드 데모 배포

심사위원이 로컬 설치 없이 Slack 테스트 계정으로 Tacit을 체험하도록 EC2 한 대에 중계 서버, Slack 커넥터, 테스트 계정 A·B의 워커, Caddy(HTTPS)를 Docker로 실행한다. 워커 자료는 `demo-workspaces/`의 합성 데이터만 사용한다. A는 각 시나리오의 송신자 자료, B는 수신자 자료다.

## 준비

- Docker Engine과 Compose 플러그인
- EC2 인스턴스 역할: `bedrock:InvokeModel` 권한(Converse 호출에 사용). 대상 모델이 추론 프로파일(`global.`)이면 해당 프로파일과 기반 모델 리소스를 모두 허용한다. 액세스 키는 쓰지 않는다.
- 컨테이너가 인스턴스 역할을 읽도록 IMDSv2 hop limit을 2로 설정한다.
  `aws ec2 modify-instance-metadata-options --instance-id <id> --http-tokens required --http-put-response-hop-limit 2`
- 보안 그룹 인바운드 80, 443 허용. 80은 인증서 발급에 필요하다.
- 공개 IP로 해석되는 DNS 이름. 예: `203-0-113-10.sslip.io`
- 이미지의 실행 사용자는 UID 1000이다. `deploy/config`의 소유자가 UID 1000이 아니면 `sudo chown -R 1000:1000 deploy/config`를 실행한다.

## 설정 생성

저장소 루트에서 실행한다. 중계·에이전트 토큰은 무작위로 생성되며 화면에 출력되지 않는다. 기존 파일은 덮어쓰지 않는다.

```bash
uv run python deploy/make_config.py \
  --team T0XXXXXXX --user-a U0AAAAAAA --user-b U0BBBBBBB \
  --domain 203-0-113-10.sslip.io \
  --slack-url https://<workspace>.slack.com --repo-url https://github.com/<org>/<repo>
cp deploy/.env.example deploy/.env   # TACIT_DOMAIN을 같은 이름으로 수정
```

생성된 `deploy/config/`에서 빈 값을 채운다.

| 파일 | 채울 값 |
|---|---|
| `slack.env` | `SLACK_BOT_TOKEN`(xoxb), `SLACK_APP_TOKEN`(xapp, `connections:write`) |
| `worker-a.env` | 테스트 계정 A의 `SLACK_USER_TOKEN`(xoxp) |
| `worker-b.env` | 테스트 계정 B의 `SLACK_USER_TOKEN`(xoxp) |

Slack 앱은 저장소의 `slack-manifest.json`으로 만든다. 각 사용자 토큰은 해당 테스트 계정으로 앱을 설치해 발급한다.

## 실행과 확인

```bash
cd deploy
docker compose up -d --build
docker compose ps
docker compose logs -f slack worker-a worker-b
curl https://203-0-113-10.sslip.io/health   # {"status":"ok","protocol":2}
```

`https://<domain>/`은 체험 안내 페이지다. 워커는 Slack 커넥터가 봇 사용자를 등록할 때까지 종료와 재시작을 반복하며, 인증서 발급 직후 몇 번 재시작될 수 있다. 워커와 커넥터는 `https://<domain>`으로 중계에 접속하고, Compose 네트워크 별칭으로 Caddy에 직접 연결된다.

설정을 바꾼 뒤에는 `docker compose up -d --force-recreate <서비스>`로 반영한다.

## 대회 게이트웨이로 전환

`worker-a.env`, `worker-b.env`에서 다음 값을 바꾸고 워커를 재생성한다.

```dotenv
TACIT_BACKEND="openai"
TACIT_OPENAI_BASE_URL="https://<gateway>/v1"
TACIT_OPENAI_API_KEY="<발급받은 키>"
TACIT_MODEL="<모델 별칭>"
TACIT_OPENAI_REASONING_EFFORT="high"
```

```bash
docker compose up -d --force-recreate worker-a worker-b
docker compose run --rm worker-a --env-file /config/worker-a.env doctor --probe
```

`doctor --probe`는 실제 모델을 한 번 호출한다. 키로 부를 수 있는 별칭은 `GET /v1/models`로 확인한다. `TACIT_OPENAI_REASONING_EFFORT`는 요청의 `reasoning_effort`로 전달되며, 비워두면 모델 기본값을 쓴다. LiteLLM 게이트웨이는 Claude에서 이 값을 모델의 effort 설정으로 바꾼다. 사용액과 한도는 `GET /key/info`로 확인한다.

## 주의

- `deploy/config/`와 `deploy/.env`는 Git에서 제외된다. API 키, Slack 토큰, 생성된 설정 파일을 커밋하거나 문서·채팅에 붙여넣지 않는다.
- 테스트 계정이 Slack Home 설정에서 실행기를 `codex`, `kiro`, `opencode`로 바꾸면 컨테이너에 해당 CLI가 없어 실패한다. 데모에서는 빈칸(서버 기본값)을 유지한다.
- 중계 DB와 워커 상태는 Docker 볼륨(`relay-data`, `worker-a-data`, `worker-b-data`)에 남는다. `docker compose down -v`는 기록과 인증서를 모두 삭제한다.
- 데모가 끝나면 EC2 인스턴스를 종료하고 과금 여부를 확인한다.
