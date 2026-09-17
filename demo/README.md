# Tacit 발표 데모 시나리오

사람 사이의 Slack 문장은 짧고 자연스럽지만, 양쪽 로컬 자료의 전제가 달라 오해가 생기는
상황을 재현한다. 각 시나리오는 송신자 자료, 수신자 자료, 보낼 메시지, 기대 결과로 구성한다.

| 번호 | 상황 | 핵심 충돌 |
|---|---|---|
| 01 | 실험 성공 판정 | 기준선과 전처리 조건이 다름 |
| 02 | API 배포 합의 | 서로 다른 계약 버전을 보고 있음 |
| 03 | 발표 범위 합의 | 확정 범위와 장기 계획을 혼동함 |

실험은 별도 검증 하네스를 만들지 않고 실제 Tacit 서비스로 진행한다. 두 사용자가 해당
시나리오의 자료 폴더를 자신의 `TACIT_WORKSPACE`로 지정하고 worker를 재시작한 뒤 Slack에서
`/tacit-send`를 사용한다.

```dotenv
# 송신자 머신
TACIT_WORKSPACE=/absolute/path/to/demo/01_experiment_baseline/sender

# 수신자 머신
TACIT_WORKSPACE=/absolute/path/to/demo/01_experiment_baseline/recipient
```

```text
/tacit-send @상대 2포인트 올랐으니 성공으로 발표에 넣어도 되죠?
```

현재 첫 실제 실험은 이미 양쪽에 설정된 `examples/alice`, `examples/bob`의 같은 B17/B24
조건을 사용한다. 나머지 두 시나리오는 상대 worker의 자료 폴더까지 맞춘 뒤 같은 방식으로
실행한다.

첫 실제 실행에서 확인한 범위와 남은 확인은 [실험 기록](ACTUAL_EXPERIMENT.md)에 정리했다.
