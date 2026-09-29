# Tacit Agent API 계약 v2

- 적용 버전: v2.1, 9월 20일 배포 후보
- endpoint: `POST /v2/context/send`
- 수신자 필드: `recipient_agent_id`
- 인증: `Authorization: Bearer <agent-token>`
- `evidence_version`과 `consent_scope`는 필수다.
- v1 payload의 `recipient` 필드는 더 이상 받지 않는다.
