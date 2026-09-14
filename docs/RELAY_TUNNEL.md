# 현재 중계 서버 외부 접속

2026-09-15 작업 종료 시 서버와 터널을 중지했다. 아래 주소는 마지막 연결 기록이며 현재 사용 가능한 주소가 아니다. 재개 시 새 주소를 확인한다. [재개 절차](MILESTONE.md#재개-순서).

- 마지막 공개 주소: `https://currently-atlantic-enquiries-zshops.trycloudflare.com`
- 연결 대상: 재용 머신의 `http://127.0.0.1:8765`
- 방식: Cloudflare Quick Tunnel, HTTP/2 연결
- 로컬 실행 파일: `.tacit/bin/cloudflared`
- 로그: `.tacit/logs/tunnel.log`
- 현재 주소 기록: `.tacit/public-url.txt`
- 프로세스 ID 기록: `.tacit/services.json`의 `tunnel`

서버와 터널은 재용 머신에서 실행하는 구성이다. 사용자별 Agent 인증이 적용된다. 서버 원문·맥락을 전달하는 HTTPS 연결은 Cloudflare를 경유하며, Agent끼리의 종단간 암호화는 구현되지 않았다.

## 확인

다음은 당시 검증에 사용한 명령이다. 재개 후에는 새 주소로 바꾼다.

```bash
curl --fail https://currently-atlantic-enquiries-zshops.trycloudflare.com/health
```

정상 결과는 `{"status":"ok","protocol":1}`이다. `/v1/` API에는 별도 Bearer 인증이 필요하다.

## 종료와 재시작

터널을 종료할 때 `.tacit/services.json`에 기록된 PID가 현재 `cloudflared` 프로세스인지 확인하고 그 프로세스에 TERM 신호를 보낸다. 중계 서버와 로컬 Agent는 별도 프로세스다.

터널이 종료된 뒤 새로 실행하려면 저장소 최상위에서 다음 명령을 사용한다.

```bash
.tacit/bin/cloudflared tunnel --url http://127.0.0.1:8765 --no-autoupdate --protocol http2
```

이 명령은 터미널에서 실행되며, 새로 출력되는 HTTPS 주소를 성원님의 `peer.env`와 안내 문서에 반영해야 한다. 재용님 로컬 커넥터와 worker의 주소는 `http://127.0.0.1:8765`를 유지한다.

재부팅 시 자동 시작은 아직 등록하지 않았다. Quick Tunnel은 개발용 임시 연결이며 고정 주소나 가동 시간을 보장하지 않는다. 고정 주소가 필요해지면 계정에 연결한 터널 또는 고정 HTTPS 서버로 전환한다. [Cloudflare 공식 안내](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
