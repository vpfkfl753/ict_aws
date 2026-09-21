# v2 통합 및 운영 전환

## 보존 기준점

- 기존 운영 코드: `65e6bae`, Claude 연결 감시 수정 보존: `2ac2b18`.
- 보존 브랜치: `fix/slack-connection-watchdog` (원격에도 보존).
- 통합 시작점: `origin/main`의 `5912f3d`.
- 통합 브랜치: `integrate/main-watchdog-v2`.
- 통합 작업 디렉터리: `/home/jaeyong/ICT/ict_aws-integration`.
- 운영 디렉터리: `/home/jaeyong/ICT/ict_aws`. systemd는 이 경로를 사용한다.

통합 브랜치는 승인·검색·Kiro/OpenCode·Home 기능을 유지하고 연결 감시 기능을 이식한다.
원격 워커는 180초 작업 대기에 로컬 감사 요청을 합쳐 유휴 요청을 줄인다.
슬래시 명령의 대화 판별 실패는 DM 전송으로 대체하지 않고 접수를 중단한다.

## 이번 실행의 상태 (2026-09-21)

- Claude 수정 보존·푸시와 별도 worktree 이식 완료.
- 자동 테스트 107개, lint, 공백 검사 통과. 실제 Slack 메시지·모델 호출은 하지 않았다.
- 운영 중앙은 protocol 1을 유지한다. 점검 시 기존 작업 4개는 모두 완료 상태이고 양쪽 worker가 연결돼 있었다.
- 사전 점검에서 현재 봇 및 재용 사용자 토큰의 `im:read`, `im:history` 누락을 확인했다.
- 기존 앱 설정 토큰은 `token_expired`다. 토큰 갱신 또는 관리자 화면의 manifest 적용이 필요하다.
- Git SSH 푸시는 가능하지만 PR 생성·병합용 GitHub API 인증은 별도 필요하다.
- PR 병합, Slack 재승인, 성원 worker 전환을 마치기 전까지 운영 코드를 교체하지 않는다.

## 전환 전 필수 조건

### 추가 통합 검토 (성원 머신, 2026-09-21)

- 재용의 `99aaa51` 통합 변경을 기준으로 테스트 107개를 다시 확인했다.
- 연결 감시는 실제 pong 수신 진전으로 판단하도록 수정했다. 연결 재시도만 반복하거나
  오래된 pong 기록만 남은 상태는 정상으로 간주하지 않는다.
- 종료 로그를 별도 daemon 스레드로 분리해 logging 잠금이 프로세스 재시작을 막지 않게 했다.
- 신규 회귀 테스트 4개를 포함해 전체 111개 통과, Ruff·공백 검사 통과.
- 이 결과는 코드 검증이다. 운영 서버 반영·Slack 권한 승인·실제 v2 왕복은 아래 절차대로
  별도 진행해야 한다. 기존 보존 브랜치는 삭제하지 않는다.

### 운영 전환 체크

- PR 검토·병합 후 정확한 배포 커밋을 기록한다. 운영 폴더에서 실험용 브랜치를 전환하지 않는다.
- 양쪽 사용자가 작업 중지와 재시작을 조율한다. 원격 워커 접속 기록만으로 업데이트 여부를 판단하지 않는다.
- 진행 중인 v1 작업이 없음을 확인한다. v1 작업은 v2 워커로 자동 인계되지 않는다.
- 새 Slack manifest 반영, 워크스페이스 승인, 각 사용자 자신의 토큰 재승인을 완료한다.
- 봇·사용자 토큰에 `im:read`, `im:history`가 필요하다. 토큰을 Git이나 채팅에 넣지 않는다.
- 중앙의 앱 설정 토큰이 만료됐다면 갱신하거나 Slack 관리 화면에서 manifest를 직접 적용한다.

권한 확인 명령은 메시지나 모델 호출을 하지 않는다. 중앙에서:

```bash
uv run python -m tacit.check_slack --protocol 2
```

성원 머신에서는 본인 토큰만 확인한다:

```bash
uv run python -m tacit.check_slack --protocol 2 --worker-only --worker-env .tacit/setup/peer.env
```

이 검사는 토큰 신원·scope 확인이다. manifest의 이벤트 구독과 Home 설정은 별도로 확인한다.

## 배포 순서

1. 두 사용자 모두 신규 전송을 멈추고 진행 중인 작업을 끝낸다.
2. 양쪽 worker를 중지한다. 성원 머신의 기존 서비스명은 `tacit-woosung-worker.service`다.
   재용 머신에서 터미널로 실행한 worker는 해당 터미널에서 Ctrl+C로 종료한다.
3. 중앙을 `tacit-server off`로 멈추고 SQLite와 `.tacit` 설정을 개인 백업한다.
   SQLite는 `sqlite3.Connection.backup` 또는 SQLite `.backup`으로 복사한다.
   백업과 설정 파일은 `0600`, 백업 디렉터리는 `0700`으로 제한한다.
4. 원래 운영 디렉터리가 깨끗한지 `git status --short`로 확인하고 아래 순서로 main을 맞춘다.
   로컬 변경이나 다른 로컬 커밋 때문에 fast-forward가 실패하면 중단하고 보존부터 한다.

```bash
git fetch origin
git switch main
git merge --ff-only origin/main
uv sync --locked
```

5. 새 Slack manifest와 권한이 적용됐는지 재확인한 후 중앙에서 `tacit-server on`을 실행한다.
   `/health`의 protocol 2, 커넥터의 Socket Mode 연결과 bot user 등록을 확인한다.
6. 양쪽 머신에서도 위 Git·의존성 업데이트를 마친 뒤 worker를 시작한다.
   기존 env의 본인 ID, Agent 토큰, 선택한 실행기와 로컬 로그인은 유지한다.
   중앙 머신의 relay URL은 loopback, 성원 머신은 고정 ngrok URL을 유지한다.
   검색 폴더와 `TACIT_ALLOWED_ROOTS`를 확인하고 실제 검증은 작은 예제 폴더부터 시작한다.

```bash
# 재용 머신
uv run tacit --env-file .tacit/setup/owner.env worker
# 성원 머신: 기존 서비스가 남아 있다면 아래 한 개만 실행
systemctl --user restart tacit-woosung-worker.service
# 기존 임시 서비스가 없다면, 대신 터미널에서 아래 한 개만 실행
uv run tacit --env-file .tacit/setup/peer.env worker
```

중앙 서버 업데이트가 먼저다. 수정된 worker는 결합 조회 API가 있는 서버가 필요하다.
같은 사용자 worker를 서비스와 터미널에서 중복 실행하지 않는다.

## 실제 완료 기준

- 양쪽 worker가 새 커밋에서 실행되고 v2 작업을 처리한다.
- Slack Home과 전송 모드 선택이 열린다.
- 재용 → 성원: 승인 전에는 맥락이 상대에게 전달되지 않고, 재용 본인이 승인한 후 성원이 해석 결과를 받는다.
- 성원 → 재용: 같은 절차를 반대 방향으로 확인한다.
- Agent 전용 요청은 상대 사람 DM에 원문이 전송되지 않는다. 응답자 승인 이후에만 답변이 돌아온다.
- 취소·수정 후 승인·내 로컬 감사 기록도 확인한다. 다른 사용자를 대신해 승인하지 않는다.
- 실제 작업 ID와 최종 상태를 기록한다. 모의 테스트 통과와 실제 Slack·모델 성공은 구분한다.

## 롤백

새 작업 접수를 멈추고 양쪽 worker와 중앙을 중지한다. 실패 시점의 DB도 먼저 별도로 보존한다.
운영 폴더를 보존 브랜치 `fix/slack-connection-watchdog`로 전환하고 `uv sync --locked` 후 중앙과
v1 worker를 재시작한다. v2 테이블은 기존 v1 코드에서 사용하지 않지만 v2 작업은 처리되지 않는다.
백업 DB 복원은 백업 이후 생성된 작업과 승인을 잃으므로 자동으로 수행하지 않는다.
필요한 경우 양쪽 사용자와 확인하고 DB 및 SQLite 부속 파일을 일관되게 교체한다.
Slack manifest를 되돌리더라도 이미 보낸 메시지나 사용자가 승인한 전달을 취소하는 것은 아니다.
