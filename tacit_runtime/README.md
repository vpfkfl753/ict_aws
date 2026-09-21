# Tacit: Kiro / OpenCode 실행기

확인일: 2026-09-21. Slack 및 Agent Plane 구현 담당자가 호출하는 로컬 Python 연결 모듈이다.

통합 후 `tacit worker`와 `tacit doctor --probe`에서도 `TACIT_BACKEND=kiro|opencode`로 선택할 수 있다. 기본값 `codex`는 기존 직접 CLI 경로를 사용한다. 워커 연결 코드는 `tacit/provider.py`에 있으며, 각 exchange의 요청마다 새 ACP 세션을 연다.

```text
Slack / Agent Plane
       │
       └─ AgentRuntime(backend=...)
             ├─ kiro     → Kiro CLI ACP → 대회 Kiro 계정
             └─ opencode → OpenCode ACP → 개인 ChatGPT OAuth
```

두 구독을 각각 사용할 수 있도록 실행기를 선택한다. 사용량을 합치거나 한쪽 구독으로
다른 서비스를 호출하지 않는다. Bedrock API는 이 경로에 필요하지 않다.
대회 Kiro 사용 의무/제출 증빙에 관한 규정은 별도로 확인해야 한다.

## 현재 검증 상태

| 항목 | 상태 |
|---|---|
| 프로젝트 내부 OpenCode 설치 | 1.18.30, `.tools/opencode/` |
| 프로젝트 내부 Kiro CLI 설치 | 2.21.4, `.tools/kirocli/bin/`; 공식 manifest SHA256 확인 |
| OpenCode ACP 초기화·세션 생성·모델 지정 | 실제 실행 성공 |
| OpenCode ChatGPT 모델 호출 | 저장된 OAuth 토큰 갱신이 401로 실패; 재로그인 필요 |
| Kiro 로그인 | 대회 계정의 IAM Identity Center 로그인 확인 (2026-09-21) |
| Kiro ACP 실제 모델 응답 | 실제 Slack 명령에서 송신 맥락 생성과 `prepare completed` 확인 |
| 프로토콜/오류/종료 테스트 | 가짜 ACP 프로세스를 사용한 8개 테스트 통과 |

Kiro는 실제 Tacit 송신 경로에서 응답 생성을 확인했다. 수신 방향과 상대 봇 DM의 결과 확인은 별도이며, OpenCode 모델 호출은 이전 401 이후 재검증하지 않았다. [Kiro 실제 검증 기록](../docs/KIRO_VALIDATION.md)을 참고한다.
기존 Codex 로그인 정보는 복사하거나 변경하지 않았다. 전역 실행파일과 셸 설정도 변경하지 않았다.

## 1. 각 구독으로 로그인

프로젝트 루트에서 실행한다.

OpenCode:

```sh
.tools/opencode/node_modules/.bin/opencode auth login --provider openai
```

로그인 방법에서 **ChatGPT Plus/Pro**를 선택하고 현재 Codex를 사용하는 ChatGPT 계정으로
로그인한다. API key 옵션은 별도 API 과금 경로이므로 이 구성에서는 선택하지 않는다.
로그인 정보는 OpenCode가 관리하고, 모듈은 토큰을 읽어 복사하거나 직접 갱신하지 않는다.

Kiro:

```sh
PATH="$PWD/.tools/kirocli/bin:$PATH" .tools/kirocli/bin/kiro-cli login --license pro
```

대회 운영사 메일에 적힌 **본인의 Start URL**, IAM Identity Center 리전 `us-west-2`,
대회 계정을 사용한다. 공지에 서로 다른 조직 URL이 있으므로 임의로 선택하지 않는다.
로그인 후 다음으로 확인한다.

```sh
PATH="$PWD/.tools/kirocli/bin:$PATH" .tools/kirocli/bin/kiro-cli whoami
```

Kiro는 공식 네이티브 CLI의 ACP 연결을 사용한다. 별도 CI/headless API 키 방식의
대회 계정 발급 가능 여부는 확인되지 않아 여기에 의존하지 않는다.
개인 구독을 여러 사용자가 함께 쓰는 중앙 모델 서비스까지 검증한 구성은 아니다.
각 사용자 머신에서 자신의 구독으로 실행하는 구조를 전제로 한다.

## 2. 단일 요청 실행

Python 3.11+ 표준 라이브러리만 필요하다. 기본적으로 프로젝트 내부 실행파일을 찾는다.
다른 설치를 쓰려면 `--executable /absolute/path/to/binary`로 지정한다.

```sh
printf '%s' '도구를 쓰지 말고 TACIT_OK라고만 답해.' | \
  python3 -m tacit_runtime --backend opencode --workspace "$PWD" \
  --model openai/gpt-5.6-sol

printf '%s' '도구를 쓰지 말고 TACIT_OK라고만 답해.' | \
  python3 -m tacit_runtime --backend kiro --workspace "$PWD"
```

위 OpenAI 모델 ID는 설치된 OpenCode가 실제 제공한 모델 목록에서 확인했다.
계정에서 실제 사용 가능한지는 로그인 후 확인해야 한다. Kiro는 계정 기본 모델을 쓰며,
필요하면 해당 런타임의 모델 ID를 `--model`로 명시한다.

stdout은 `backend`, `session_id`, `text`, `stop_reason`을 갖는 JSON이다.
정상 완료(`end_turn`)는 종료 코드 0, 중단/출력 제한 등은 2, 실행 오류는 1이다.
모델의 사고 과정이나 도구 출력은 사용자 답변 텍스트에 섞지 않는다.

## 3. 다른 백엔드 코드에서 호출

```python
from tacit_runtime import AgentRuntime


async def explain(context: str, backend: str, workspace: str):
    model = "openai/gpt-5.6-sol" if backend == "opencode" else None
    async with AgentRuntime(backend, workspace, model=model) as agent:
        result = await agent.prompt(context)
        if result.stop_reason != "end_turn":
            raise RuntimeError(f"Agent stopped: {result.stop_reason}")
        return result.text
```

한 대화를 이어가려면 같은 `async with` 안에서 `prompt()`를 여러 번 호출한다.
사용자/대화별로 별도 인스턴스를 사용한다. 한 인스턴스의 요청은 직렬 처리된다.
다른 사용자와 native session ID를 공유하지 않는다.
실행기를 바꿀 때는 새 인스턴스를 열고 Agent Plane이 보관한 필요한 맥락을 다시 전달한다.
Kiro 세션을 OpenCode 세션으로 변환하거나 모든 원시 대화를 자동 이관하지 않는다.

인증 오류/구독 한도 도달 시 다른 구독이나 유료 API로 자동 전환하지 않는다.
OpenCode 경로는 OAuth 인증 유형과 명시적 `openai/` 모델을 요구하고
`OPENAI_API_KEY`/`OPENCODE_AUTH_JSON` 환경변수의 대체 경로를 제거한다.
외부 OpenCode 플러그인은 `--pure`로 제외한다.

현재 도구 승인 요청은 거절하고, 파일 읽기 계열만 사전 허용한다.
Kiro와 OpenCode의 자체 설정·도구 동작은 별개이므로 **이 모듈 자체가 파일 접근 샌드박스는 아니다**.
상대에게 전달할 암묵지의 소스 선별과 공유 정책은 Agent Plane 담당 범위다.
시간 초과나 Python task 취소 시 로컬 에이전트 프로세스 그룹을 종료한다.
프로세스 종료가 서버 측 모델 연산의 즉시 취소/요금 환불을 보장하지는 않는다.

## 검증

```sh
python3 -m unittest discover -s tests -v
```

두 실행기 선택, 다중 턴/대화 분리, 출력 필터링, 승인 요청 처리,
부분 완료, 오류 시 민감한 공급자 출력 노출 방지, timeout/cancel 종료,
API key의 구독 경로 대체 방지를 검증한다. 실제 계정 인증 테스트를 대체하지 않는다.

## 근거

- [OpenCode ChatGPT 구독 연결](https://opencode.ai/docs/providers/#openai)
- [OpenCode ACP](https://opencode.ai/docs/acp/)
- [Kiro ACP](https://kiro.dev/docs/cli/acp/)
- [Kiro 인증 및 구독 크레딧](https://kiro.dev/docs/getting-started/authentication/)
- [ACP prompt/응답 규격](https://agentclientprotocol.com/protocol/v1/prompt-turn)

연결 모듈은 ACP v1의 `prompt`/`session/update`를 따른다.
2026-09-21 설치된 Kiro CLI 2.21.4에서 이 경로로 실제 응답 수신과 정상 턴 종료를 확인했다.
