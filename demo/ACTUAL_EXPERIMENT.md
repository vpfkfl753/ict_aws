# 실제 Tacit 실험 기록

실행일: 2026-09-17 KST

## 사용한 경로

- 실제 SERA LAB Slack `/tacit-send`
- 실제 고정 ngrok 중계 서버
- 우성원 worker의 실제 Codex 로그인
- 송신자 자료: `examples/bob/results.md` (B24, preprocessing p3)
- 수신자 자료: 재용 worker에 설정된 `examples/alice/experiment.md`를 전제로 함

별도 모의 중계나 별도 평가 하네스는 사용하지 않았다.

## 보낸 메시지

```text
/tacit-send @이재용 현재 비교는 B24 기준인데 B17 조건으로 다시 맞춰볼까요?
```

## 관찰 결과

- Slack 명령이 접수됐고 전달 ID `56b36628-78ac-4a41-ae17-ef5b44fa3259`가 생성됐다.
- 우성원 명의의 원문 DM이 재용에게 도착했다.
- 우성원 worker에서 같은 ID의 `prepare started`와 `prepare completed`를 확인했다.
- Agent 설명을 보기 전 재용은 일반 DM에서 `B24 B17이 대체뭐예요`라고 반응했다.

수신자에게 데모 전제를 설명하지 않은 상태에서 보낸 질문이므로 이 반응만으로 제품의 유용성이나
실패를 판단할 수 없다. 재용 측 Tacit 봇 DM의 수신 여부와 설명 내용은 재용 화면에서
확인해야 하므로 아직 성공으로 확정하지 않는다.

## 다음 확인

1. 재용의 Tacit 봇 DM에 B24/p3와 B17/p2의 차이가 설명됐는지 확인한다.
2. 설명이 왔다면 근거 파일과 성공 판정 한계를 정확히 구분했는지 확인한다.
3. 나머지 API 계약·발표 범위 시나리오는 양쪽 worker의 `TACIT_WORKSPACE`를 해당 폴더로
   맞춘 뒤 같은 `/tacit-send` 경로로 실행한다.
