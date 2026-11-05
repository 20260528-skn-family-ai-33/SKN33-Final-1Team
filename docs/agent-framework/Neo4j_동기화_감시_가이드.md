# Neo4j 동기화 지연·연속 실패 감시

2026-11-04 · 개발용 Docker Linux · Mac/Windows 공통 · AF005 유지

## 감시 범위

기존 Neo4j·projection worker 옆에 독립 `monitor` 서비스를 실행한다. PostgreSQL의 graph_state·미처리 outbox·최근 근거 조회 기록과 Neo4j의 실제 프로젝트 revision을 읽는다. 업무 DB와 그래프를 수정하거나 원장 revision을 올리지 않는다. 새 PG 마이그레이션·추가 모델·Aura API를 사용하지 않는다.

| 조건 | 기본 결과 |
|---|---|
| 미처리 outbox의 가장 오래된 항목이 120초 이상 | warning / SYNC_LAGGING |
| projection worker 연속 실패 3회 이상 | critical / CONSECUTIVE_SYNC_FAILURES |
| PostgreSQL에서 반영 완료인데 실제 그래프가 없음 | critical / GRAPH_PROJECTION_MISSING |
| 실제 그래프 revision이 확인된 applied revision보다 낮음 | critical / GRAPH_BEHIND_CONFIRMED_REVISION |
| PostgreSQL·Neo4j 조회 실패 | critical, 확인 불가 값은 null |
| 필요한 revision은 있으나 미처리 outbox가 없음 | critical / PENDING_REVISION_WITHOUT_OUTBOX |
| 최근 15분 SQL 대체가 3회 이상이고 조회의 50% 이상 | warning / FREQUENT_SQL_FALLBACK |
| 감시 파일이 60초 이상 갱신되지 않음 | unknown / MONITOR_SNAPSHOT_STALE_OR_MISSING |

15초마다 확인한다. 새 변경이 아직 반영 중이어도 120초 미만이면 정상 대기일 수 있다. 관측 도중 새 동기화가 완료돼 Neo4j revision이 먼저 높아진 경우는 오류로 취급하지 않는다. 최근 SQL 대체 경고는 15분 동안의 이력이므로 Neo4j 연결이 복구된 뒤에도 해당 창 동안 남을 수 있다.

한 번에 최대 500개 프로젝트를 관측하며 이를 넘으면 범위 제한 경고를 남긴다. 현재 PostgreSQL에 등록된 그래프 상태만 비교하고, 원장에 없는 기존 그래프 자료는 삭제하지 않는다.

## 실행

최초 또는 코드 변경 후 새 이미지를 빌드한다. Mac은 `python3`, Windows PowerShell은 `py -3.12`를 사용한다.

```text
python3 scripts/framework_desktop.py build --image creative-checker-framework:graph-monitor-20261104
python3 scripts/framework_graph_runtime.py up
```

이 작업 이후 기본 이미지는 위 graph-monitor 태그다. 원본 env·Neo4j 데이터/로그 볼륨·PostgreSQL은 유지한다. 새 `framework-monitor` named volume에는 운영 감시의 최신 JSON만 저장한다. 파생 설정은 기존 `.runtime/framework-graph-runtime/`에 보존한다. PostgreSQL AF005와 그래프 제약은 기존 가이드에 따라 이미 명시 초기화돼 있어야 한다.

## 상태 확인

Mac:

```bash
python3 scripts/framework_graph_runtime.py status
python3 scripts/framework_graph_runtime.py monitor
```

Windows:

```powershell
py -3.12 scripts/framework_graph_runtime.py status
py -3.12 scripts/framework_graph_runtime.py monitor
```

`status`는 컨테이너 3개 상태와 최신 synchronization 요약을 반환한다. `monitor`는 새로 한 번 조회해 프로젝트별 수치·실제 revision·경고 원인을 자세히 보여준다. 경고·오류·알 수 없음은 실패 종료 코드로 구분한다.

정상 synchronization 요약 예:

```json
{
  "state": "healthy",
  "pending_projects": 0,
  "pending_events": 0,
  "max_pending_seconds": 0,
  "recent_sql_fallbacks": 0,
  "alerts": []
}
```

서비스의 healthy는 프로세스·접속 또는 감시 루프가 작동함을 뜻한다. monitor 컨테이너가 healthy여도 업무 동기화에는 warning/critical이 있을 수 있으므로 **synchronization.state도 확인**한다. 조회가 실패한 수치를 0으로 표시하지 않는다.

## 기록과 경고

시작·상태/경고 변경 시 `framework_graph_monitor_changed`를 로컬 Docker 로그에 남긴다. 동일 경고의 나이·실패 횟수만 변하면 반복하지 않고, 정상 복구 시 healthy 변경을 남긴다. 별도 5분 heartbeat 로그는 감시 생존 확인용이다. 원문·모델 응답·암호·연결 예외 원문을 로그에 포함하지 않는다.

```text
docker logs --tail 50 creative-checker-agent-framework-monitor-1
```

로그는 최대 5MB 파일 3개로 순환한다. 최신 상태는 전용 볼륨의 `/data/monitor/latest.json`에 원자적으로 저장하며 재시작 후에도 읽을 수 있다. 이것은 승인·검수 업무 이력 원장이 아니라 운영 관측 정보다.

## 구현과 경계

`graph_monitor.py`의 PostgreSQL 조회는 READ ONLY 트랜잭션을 사용하며, Neo4j는 고정된 revision 조회만 수행한다. PostgreSQL 자격증명은 기존 app 계정을 재사용하고 owner·모델·웹 비밀은 전달하지 않는다. 새 역할 계약이나 독립 A03을 추가하지 않았다.

이 단계는 감지·상태 표시·로컬 기록이다. 자동 그래프 재구축·자료 삭제·모델 재호출·사용자 승인·외부 메일/Slack 통지는 수행하지 않는다. 장애를 감지해도 창작물의 위반이나 검수 성공으로 바꾸지 않는다. 컨테이너 종료 시 재시작은 기존 unless-stopped 정책을 유지한다.

Windows에서도 실제 실행은 Linux 컨테이너이며, Windows 실기기·원격 CI 실행 결과와 Mac 로컬 검증은 구분한다.

## 11/04 적용·검증 기록

- 기존 계약·CLI·근거 검색·실행기 회귀 **924 passed, 8 subtests**.
- 새 시험용 PostgreSQL·Neo4j·Linux 컨테이너에서 **21 passed**. 실제 지연·실패·그래프 누락 감지와 원장 revision 미변경을 확인했다.
- 독립 monitor 프로세스가 정상 → 지연 경고 → 정상 복구를 기록하고 같은 경고를 매 주기 반복하지 않는 것을 검증했다.
- 새 개발 이미지 `creative-checker-framework:graph-monitor-20261104`, SHA `627fbd19f846b4b53fe6e326afb80f0d1c7dac30cebc73111b5684894178c3d1`을 사용했다. 이전 이미지는 보존했다.
- 실제 개발 서비스 3개가 모두 healthy이고 synchronization healthy, 미처리 0건·경고 0건을 확인했다. 시작 로그 한 건 이후 정상 상태를 반복 출력하지 않았다. 최신 관측 파일은 계속 갱신됐다.
- 기존 PostgreSQL 실행 9건·승인 0건 등 전체 프레임워크 테이블, Neo4j 전체 209노드·247관계와 원래 개발 Compose의 해시가 적용 전후 동일했다. 추가된 것은 별도 운영 감시 상태 볼륨이다.
- 실제 사용자 자료의 승인·모델 호출·그래프 재구축·삭제·PG 마이그레이션은 수행하지 않았다. 기존 API·원문·모델 설정도 유지했다.

Mac Docker Linux에서 실제 적용했다. Windows 공통 실행 경로·계약 CI는 준비했으며 Windows 실기기와 원격 CI 실행은 확인하지 않았다. 비공개 검증 원본은 `.runtime/neo4j-monitor-20261104/`, `.runtime/graph-monitor-integration-final-20261104.log`, `.runtime/graph-monitor-unit-20261104.log`에 보존한다.

## 후속 자동 복구 적용

현재 공통 실행기의 기본 이미지는 `creative-checker-framework:graph-recovery-20261104`이며 `up`은 서비스 4개를 시작한다. 위 기록의 이전 단계 검증 수치는 당시 결과다. 현재 실행·복구 정책은 [프로젝트 자동 복구 가이드](Neo4j_프로젝트_자동복구_가이드.md)를 따른다. monitor 자체는 계속 읽기 전용이고, 별도 recovery 서비스가 복구 요청을 담당한다.
