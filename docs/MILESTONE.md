# 마일스톤 점검과 작업 종료

점검일: 2026-09-15. 브랜치: `feat/tacit-first-contact`.

## 판정

목표는 재용과 성원이 실제 서비스로 암묵지를 주고받는 것이다. **실제 단방향 기술 경로는 통과했고, 양방향 실사용과 설명 품질 확인이 남아 있다.**

| 항목 | 상태 | 근거 |
|---|---|---|
| Slack 앱·명령 연결 | 완료 | 실제 앱 생성, 토큰 검증, Socket Mode 명령 수신 |
| 두 머신의 Agent 연결 | 완료 | 두 사용자 heartbeat, 성원 Agent의 외부 HTTPS 접속 |
| 로컬 자료 기반 Codex 호출 | 구현·검증 | 실제 모델 probe 및 두 단계 모델 smoke 통과 |
| 재용 → 성원 실제 전달 | 처리·알림 완료 | 운영 DB의 원문 Slack 참조, 맥락·해석, `completed`, `notified=1` |
| 성원 → 재용 실제 전달 | 미검증 | 반대 방향의 실제 exchange 없음 |
| 전송 창 | 구현·단위 테스트 완료 | 실제 Slack에서 열기·제출 확인은 남음 |
| 업무 맥락 전달의 유용성 | 사용자 확인 필요 | 양쪽이 원문과 설명을 읽고 맞는지 확인해야 함 |

실제 완료된 전달 ID: `b5620d7c-636b-48ad-acb6-6ba24ec2857f`. 원문·맥락·해석 내용과 자격증명은 Git에 포함하지 않는다. 자세한 검증 범위는 [검증 기록](VERIFICATION.md)에 있다.

종료 점검의 `pytest` 22개와 `ruff check`가 통과했다. 이번 종료 점검에서는 실제 모델을 추가 호출하지 않았다.

## 비용과 종료 상태

- 이 작업에서 AWS 인스턴스, Bedrock 연결, 별도 유료 클라우드 서버를 만들지 않았다. 서버는 재용 머신의 로컬 Python 프로세스였다. 다른 프로젝트나 계정 전체의 청구 내역을 조사한 것은 아니다.
- 외부 연결에 사용한 Cloudflare Quick Tunnel은 무료 개발용 터널이다. [Cloudflare 공식 안내](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
- 재용 머신의 `codex login status`는 `Logged in using ChatGPT`였다. 실제 모델 검증과 메시지 처리는 Codex 사용량을 소모한다. 구독 포함 사용량인지 추가 크레딧인지, 성원 계정의 결제 설정은 확인하지 않았으므로 총비용 0원을 보장하지 않는다. [OpenAI 공식 인증 안내](https://learn.chatgpt.com/docs/auth), [OpenAI 공식 사용량·크레딧 안내](https://learn.chatgpt.com/docs/pricing).
- 종료 전 미완료 exchange가 0건임을 확인했다. 재용 머신의 Slack 커넥터, worker, 터널, 중계 서버를 모두 종료했다. 이 프로젝트의 백그라운드 모델 작업은 더 이상 실행되지 않는다.
- 성원 머신의 worker는 원격으로 종료하지 않았다. 성원님이 해당 터미널에서 `Ctrl+C`로 종료한다. 중계가 내려가면 새 작업을 받을 수 없다.
- Slack 앱 설치·기존 구독·인증 설정은 유지한다. `.tacit/`의 토큰, DB, 캐시, 로그는 재개를 위해 로컬에 보존하며 Git에서 제외한다. 자동 시작은 등록하지 않았다.

## 재개 순서

1. 두 머신에서 `feat/tacit-first-contact`의 변경을 받고 `uv sync --locked`를 실행한다. 기존 설정을 다시 생성하거나 Slack 앱을 새로 만들지 않는다.
2. 재용 머신에서 아래 각 명령을 별도 터미널로 실행한다. 중계와 커넥터는 각각 하나만 띄운다.

```bash
uv run tacit --env-file .tacit/setup/relay.env relay
uv run tacit --env-file .tacit/setup/slack.env slack
uv run tacit --env-file .tacit/setup/owner.env worker
.tacit/bin/cloudflared tunnel --url http://127.0.0.1:8765 --no-autoupdate --protocol http2
```

3. 터널 로그에 출력된 새 HTTPS 주소를 성원님에게 전달하고, 성원 머신의 `peer.env`에서 `TACIT_RELAY_URL`을 갱신한다. 재용 머신의 로컬 주소는 유지한다.
4. 성원 머신에서 `uv run tacit --env-file .tacit/setup/peer.env worker`를 실행한다.
5. `/tacit-status`로 두 사용자 연결을 확인한다. `/tacit-send` 전송 창을 검증하고 성원 → 재용 메시지를 보낸다. 이 단계부터 실제 Slack 메시지 전송과 모델 사용량 소모가 발생한다.
6. 양쪽이 원문 DM과 개인 설명을 읽고, 실제 업무 자료의 어떤 암묵지가 전달됐는지 확인한다. 이 확인으로 첫 마일스톤의 완료 여부를 판정한다.

후속 고도화는 일반 DM 자동 감지, 의미 기반 자료 검색, 대화·클라우드 소스 연동, 지속적 암묵지 관리다. 현재 연결 구현과 구분해 진행한다.
