# Agent Plane protocol v1

실행 구성은 relay 하나, Slack connector 하나, 사용자별 worker 하나다. HTTPS JSON API를 사용하며 Slack 토큰과 모델 로그인 정보는 worker/connector에 둔다. 중계 서버에는 사용자 ID별 Agent 인증 토큰과 connector 인증 토큰을 등록한다.

## 상태 전이

```text
prepare_pending -> prepare_running -> interpret_pending -> interpret_running -> completed
                         |                                      |
                    prepare_failed                         interpret_failed
```

`prepare_running`에서는 원문 DM의 `source`를 먼저 기록해야 `context`를 제출할 수 있다. sender만 prepare 작업을, recipient만 interpret 작업을 가져올 수 있다. 실행 중 작업에는 무작위 lease ID와 600초 만료 시각이 있다. 완료·실패·source 변경에는 사용자 인증과 현재 lease 둘 다 필요하다.

Slack `team_id:trigger_id`를 요청 키로 사용한다. 같은 키의 재접수는 같은 exchange를 반환한다. 같은 키를 다른 메시지에 재사용하면 409를 반환한다.

## API

모든 `/v1/` 경로는 `Authorization: Bearer TOKEN`이 필요하다.

| 메서드와 경로 | 호출자 | 동작 |
|---|---|---|
| `GET /health` | 제한 없음 | 상태와 프로토콜 버전 |
| `GET /v1/agents` | connector | 등록 사용자와 마지막 poll 시각 |
| `POST /v1/exchanges` | connector | `request_key`, `sender`, `recipient`, `text`로 작업 등록 |
| `GET /v1/exchanges/latest?owner=...&exchange_id=...` | connector | 해당 수신자의 최근 또는 특정 결과 조회 |
| `POST /v1/work/claim?wait_seconds=180` | worker | 자기 작업 하나를 임대하거나 최대 대기 후 JSON null 반환 |
| `POST /v1/work/{id}` | worker | `lease`, `action`, `value`로 단계 결과 제출 |
| `GET /v1/notifications` | connector | 아직 알리지 않은 완료·실패 결과 |
| `POST /v1/notifications/{id}/ack` | connector | Slack 전송 완료 기록 |

`action`은 `source`, `context`, `result`, `error` 중 하나다. `source`의 value는 `{"channel":"D...","ts":"..."}`이며 나머지는 문자열이다. 현재 모델 출력은 자연어 패킷이며 구조화된 지식 스키마는 후속 고도화 범위다.

`wait_seconds`는 선택 항목으로 0~180초이며 생략하면 기존처럼 즉시 반환한다. 대기 중 서버는 약 1초마다 할당 가능한 작업을 확인하며, HTTP 연결 종료도 감지한다. 클라이언트의 해당 요청 timeout은 대기 시간보다 길어야 한다. 현재 CLI worker는 원격 URL에 180초, loopback에 0초를 기본 적용하며 `TACIT_CLAIM_WAIT_SECONDS`로 조절할 수 있다. 인증된 대기 중에는 heartbeat를 갱신한다. 전송 장애 시 재시도 간격은 5초에서 최대 300초까지 늘어나고 연결 성공 후 초기화된다.

## 저장과 재시도

서버는 SQLite에 교환 상태를 저장한다. 작업 할당은 SQLite 쓰기 트랜잭션에서 수행한다. 프로세스가 죽어도 임대 만료 후 재할당할 수 있다. worker는 Slack 원문 전송 결과와 모델의 최종 출력을 `.tacit/` 아래 캐시하여 서버 접속 실패 후 재사용한다.

Slack과 SQLite 사이에는 분산 트랜잭션이 없다. 원문 전송 성공 직후 캐시를 쓰기 전에 프로세스가 종료되거나, 알림 성공 직후 ack가 유실되면 중복 전송 가능성이 있다. 현재 단계에서는 이를 감수하며 전달 ID로 같은 작업을 식별한다. 최종 결과 알림은 connector 단일 인스턴스를 전제로 한다.

`context`에는 송신자가 전달하는 맥락, `result`에는 수신자가 로컬 자료를 결합해 만든 설명이 들어간다. sender worker는 수신자의 완료 결과를 조회하는 API를 갖지 않는다. connector는 Slack 사용자 ID와 워크스페이스를 검증하고 수신자에게만 결과를 표시해야 한다.
