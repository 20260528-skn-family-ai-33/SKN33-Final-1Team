# Neo4j 프로젝트별 그래프 자동 복구

2026-11-04 · Mac/Windows 공통 Docker Linux · 기존 PostgreSQL AF005 유지

## 복구 대상과 원장

`recovery` 서비스가 PostgreSQL에 등록된 프로젝트의 관계 스냅샷과 실제 Neo4j의 revision·노드·관계 해시를 비교한다. 기존 `monitor`는 계속 읽기 전용이다. 복구 서비스는 승인·검수 원장을 변경하지 않고 `graph.recovery_requested` outbox 이벤트만 기록한다. 기존 projection worker가 해당 프로젝트의 **AF 전용 파생 노드**를 재구축한다. 독립 A03·벡터 DB·Aura·새 모델·PG 마이그레이션은 추가하지 않는다.

PostgreSQL의 문서 버전·입력·단계 출력·기준 버전·승인 요청/내용·고정 근거의 저장 해시와 연결을 확인한다. 원본 파일의 바이트 재검사는 기존 검수 근거 준비 경계에서 계속 수행하며, 복구 서비스가 파일을 생성하거나 승인 기준을 바꾸지 않는다. 구버전/미등록 문서 등 원장을 확인할 수 없는 이력은 자동 변환하지 않는다.

## 동작 정책

| 상황 | 처리 |
|---|---|
| 노드/관계/프로젝트 projection 유실 또는 내용 불일치 | 동일 이상을 두 번 확인하고 원장 재검증 후 복구 요청 |
| 두 확인 사이 간격 | 최소 30초. 15초 주기이며 프로젝트가 많으면 더 길어질 수 있음 |
| 정상 outbox 대기 또는 활성 projection lease | 기존 worker를 기다림 |
| Neo4j 접속 불가 | 덮어쓰지 않고 연결 복구 후 다시 관측 |
| PG 원장 해시/참조 불일치 | manual_review, 자동 재구축 차단 |
| 안정된 PG revision보다 앞선 그래프 | manual_review, 자동 재구축 차단 |
| 외부 label·다른 종류/다른 프로젝트 연결 | manual_review, 기존 연결 보존 |
| 프로젝트별 반복 복구 | 요청 후 300초 대기, 최근 1시간 최대 3회 |
| PG에 없는 과거/실험 그래프 프로젝트 | 조회·삭제·재구축 대상에서 제외 |

한 주기에 5개 프로젝트를 ID 순서로 순환한다. 500개에서 잘라 동일 프로젝트만 반복하지 않는다. 이상 확인 상태는 전용 볼륨에 저장하며 프로세스 재시작 후 이어서 확인한다. 24시간 넘은 운영 상태는 다시 확인을 시작한다. 복구 요청은 PG graph_state 행 잠금 안에서 revision·해시·진행 상태를 재확인해 중복 요청을 막는다. **Neo4j 통신 중 PG 트랜잭션을 유지하지 않는다.**

요청 revision, 이상 종류, 기대/관측 메타데이터 해시는 outbox에 남긴다. 정상 업무 변경과 마찬가지로 실제로 전달한 revision까지만 완료 처리한다. 그래프 쓰기 후 완료 기록 전에 worker가 중단돼도 동일 내용을 확인한 뒤 재처리할 수 있다. 같은 revision인데 내용이 다르면 반영 완료로 인정하지 않는다.

## 실행

개발용 원본 env·초기화 절차는 [분리 가이드](Neo4j_개발시험_분리_가이드.md)와 [자동 실행 가이드](Neo4j_자동실행_재시작_가이드.md)를 따른다. 현재 기본 이미지는 아래 recovery 태그다.

Mac, 프로젝트 폴더에서:

```bash
python3 scripts/framework_desktop.py build --image creative-checker-framework:graph-recovery-20261104
python3 scripts/framework_graph_runtime.py up
python3 scripts/framework_graph_runtime.py status
```

Windows PowerShell, 프로젝트 폴더에서:

```powershell
py -3.12 scripts/framework_desktop.py build --image creative-checker-framework:graph-recovery-20261104
py -3.12 scripts/framework_graph_runtime.py up
py -3.12 scripts/framework_graph_runtime.py status
```

Docker Desktop은 Linux 컨테이너 모드여야 한다. Mac/Windows 호스트는 Python 표준 라이브러리 실행기만 사용하고 DB/Neo4j 드라이버는 컨테이너 안에서 실행한다. Windows 실기기 검증 완료를 뜻하지 않는다.

`status`는 서비스 4개와 `synchronization`, `recovery`를 반환한다. 컨테이너 healthy와 업무 상태는 별개다. 정상 시 두 상태가 healthy, `recovery.confirming_projects=0`, `issues={}`다. 복구 요청 중에는 warning/requested가 나타날 수 있다. 상태 조회 자체가 복구나 모델 호출을 새로 실행하지 않는다.

```text
docker logs --tail 50 creative-checker-agent-framework-recovery-1
```

운영 파일은 `framework-recovery` named volume의 `/data/recovery/latest.json`이다. 원문/키/예외 전문 대신 상태·프로젝트 ID·고정 사유를 로컬 로그에 남긴다. 로그는 5MB 파일 3개로 순환한다. 원래 Neo4j 데이터·로그 볼륨과 포트 57431/57631은 그대로 쓴다. 복구용 서비스에도 app DB 계정과 그래프 접속 정보만 전달한다. owner·웹·모델 키는 제외한다.

## 수동 확인이 필요한 경계

새 Neo4j 볼륨 또는 제약까지 삭제된 DB는 **명시적인 graph-init**을 먼저 해야 한다. 자동 초기화/마이그레이션을 하지 않는다. 연결 불가·스키마 미준비는 데이터 유실로 간주해 덮어쓰지 않는다. 건강 검사 실패 자체가 Docker 재시작을 유발하지는 않으며 프로세스 종료 시 unless-stopped로 재시작한다.

`manual_review`는 운영 원장/관계 확인이 필요하다는 뜻이다. 창작물의 위반, 사용자 승인, 모델 결과로 바꾸지 않는다. 현재 모델 실행의 재시도·검수 최종 승인·메일/Slack 통지·AWS 운영 연결은 수행하지 않는다. 실제 전시 PDF는 미정 항목을 가진 미승인 초안으로 유지한다.

`graph-recovery --once`는 복구 가능한 **한 주기를 실제 수행하는 관리 명령**이다. 단순 상태 확인은 `graph-recovery-status` 또는 위 runtime status를 사용한다. `--project-id`는 관리자가 특정 PG 프로젝트로 범위를 제한할 때만 사용한다.

## 검증 방법

```text
python3 scripts/framework_graph_test.py
```

Windows에서는 `python3` 대신 `py -3.12`를 사용한다. 매번 별도 PG·Neo4j·Linux 시험 컨테이너를 만들며 소유 표식이 일치하는 시험 자원만 정리한다. 유실/변조 주입은 이 격리 시험에서만 수행한다.

최종 실행 결과와 개발 환경 보존 확인은 아래 적용 기록에 적는다. 기존 원문·승인·실제 사용자 그래프를 손상시켜 복구 성공을 시험하지 않는다.

## 11/04 적용·검증 기록

- 프레임워크·공통 실행기 회귀: **950 passed, 8 subtests**. DB 마커 132개는 이 명령에서 제외됐으며, 아래 별도 격리 시험과 합산하지 않는다.
- 실제 격리 PostgreSQL·Neo4j·Linux 컨테이너: **31 passed**. 메타데이터 유실/변조 복구, 30초 미만 차단, 요청 중복 방지·대기/횟수 제한, 원장 변조·앞선 revision·외부 연결 차단, 동기화와의 경합, 동일 revision 불일치의 잘못된 완료 방지를 확인했다.
- 실제 CLI의 한 주기 상태 저장 → 프로세스 재시작 → 확인된 요청 → projector 반영 → 재시작 후 정상 조회를 확인했다. 확인 시간 경과는 시험 소유 운영 파일로 모사했으며 업무 승인/모델 응답은 명시적인 합성 시험 입력이다.
- 다른 프로젝트/과거 그래프를 유지하고 현재 프로젝트만 재구축했다. 기존 승인·문서·기준·실행 이력은 복구 전후 같았다. 시험 컨테이너·DB·볼륨·이미지는 소유 표식을 확인한 후 정리됐다.
- 개발 이미지: `creative-checker-framework:graph-recovery-20261104`, SHA `9b7a7b6eca5f420a5da9487daf061d90b20c32e0ddc32fcd6c467668e4513f39`. 이전 runtime/monitor 이미지와 설정 사본을 보존했다.
- 현재 개발 프로젝트의 원장 해시 검증 후 적용했다. 서비스 4개 모두 healthy, synchronization/recovery healthy, 미처리·경고·확인 중 이상 0건이다. 정상 자료여서 복구 요청은 새로 생성되지 않았다.
- PostgreSQL 실행 9개·승인 0개·원문/버전/단계/outbox/근거를 포함한 전체 프레임워크 기록, Neo4j 전체 **209노드·247관계**, 원래 Compose 해시가 적용 전후 동일하다. 기존 Neo4j `/data`·`/logs` 볼륨 이름도 유지됐다. 새 운영 상태 볼륨만 추가했다.
- 기존 API·RunPod·원문 파일·미승인 전시 PDF·front-v2·공통 역할 계약 3.0.0은 유지했다. 실제 사용자 그래프의 유실 주입은 하지 않았다. Windows Docker Desktop 실기기·원격 CI·AWS는 미검증이다.

비공개 보존 증거와 이전 서비스 로그는 `.runtime/neo4j-recovery-20261104/`, 검사 출력은 `.runtime/graph-recovery-integration-20261104.log`와 `.runtime/graph-recovery-regression-20261104.log`에 둔다. 이 파일은 자격증명/개인 운영 정보를 포함할 수 있는 로컬 점검 자료이므로 Git 공유 대상에 넣지 않는다.
