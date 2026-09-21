# Kiro 백엔드 실제 검증

확인일: 2026-09-21. 별도 smoke나 모의 중계를 사용하지 않고 실제 Tacit의 Slack 명령,
중계 서버, 우성원 워커를 사용했다.

## 인증 및 실행 구성

- 우성원 앞으로 발송된 대회 운영사 메일에서 본인 조직 주소와 계정을 확인했다.
- 기존 PF1 브라우저의 저장된 비밀번호로 로그인했다. 신규 계정은 만들지 않았다.
- Kiro CLI 2.21.4의 `whoami`가 IAM Identity Center 로그인을 확인했다.
- 우성원 로컬 `peer.env`의 `TACIT_BACKEND`를 `kiro`로 설정했다.
- 자료 폴더는 이 저장소의 `examples/bob`이다. 실제 연구자료는 사용하지 않았다.
- 모델 ID는 지정하지 않았다. 이번 결과는 Kiro 계정 기본 모델의 응답이며 특정 기반 모델을 검증한 결과는 아니다.
- 비밀번호·OAuth 토큰·개인 설정 파일은 저장소에 포함하지 않는다.

## 실제 송신 작업

Slack의 `/tacit-send`로 재용에게 Kiro 전환 확인용 예제 질문을 보냈다.
예제 질문은 B24/p3 대비 +2%p 결과가 B17/p2 조건으로도 비교됐는지 확인하는 내용이다.

- exchange ID: `f7b81153-b8a7-44ca-8e04-7c36181c12cc`
- 실제 원문 DM: `D0ABRUEMTM2`, timestamp `1789959580.400999`
- 11:58:47 KST: `Worker backend=kiro`
- 11:59:39 KST: 해당 작업 `prepare started`
- 11:59:45 KST: `Agent inference started backend=kiro`
- 12:00:08 KST: `Agent inference finished backend=kiro stop_reason=end_turn`
- 12:00:09 KST: 해당 작업 `prepare completed`

실제 생성된 맥락의 주요 내용:

- `results.md`가 B24/p3/split v3를 사용한다고 인용했다.
- +2%p는 B24 기준 결과임을 구분했다.
- B17/p2 조건에서는 재실행하지 않았다는 파일 내용을 반영했다.
- 파일에서 확인되는 사실과 메시지의 Kiro 테스트 언급을 구분했다.

이로써 기존 ACP adapter가 설치된 Kiro의 실제 로그인·세션 생성·응답 이벤트와 동작하고,
Tacit 송신 워커가 Codex 대신 Kiro 응답을 생성·중계한다는 것을 확인했다.
응답은 작업별 로컬 context 캐시에 저장됐다. 생성된 원문 전체는 이 문서에 복제하지 않는다.

## 남은 확인

- 재용 측 최종 해석과 봇 알림의 수신 확인.
- 재용이 새 `/tacit-send` 질문을 보내면 우성원 Kiro 워커의 `interpret` 단계와 봇 DM 표시 확인.
- 시나리오 2·3은 상대 자료 폴더까지 맞춘 뒤 실행. 이번 결과로 완료됐다고 간주하지 않는다.
- 자율 검색, 공유 승인, Agent 추가 대화는 [개발 SSOT](DEVELOPMENT_SSOT.md)의 목표 설계이며 이번 로그인·백엔드 확인과 별개다.

코드 검증: provider/runtime/worker 흐름 관련 28개 테스트와 변경 코드 Ruff 검사 통과.
단위 테스트 결과와 위 실제 Slack 실행 증거를 구분한다.
