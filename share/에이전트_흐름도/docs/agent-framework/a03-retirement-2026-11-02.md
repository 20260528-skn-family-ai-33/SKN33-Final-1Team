# 독립 A03 제거와 최신 문서 정리

2026-11-02 · feat/agent-framework · 현재 작업 트리 · 커밋·push 없음

## 기준과 변경

[프로젝트 통합문서 20261102 정리본](reference/프로젝트_통합문서_20261102.html#section-014)을 정본으로 사용했다. 원본 사본의 SHA-256은 `55cd42fa4f80a2657496c87c1719fe205858d32320495b2b9d6008c2eb5905ce`다.

- 독립 A03 역할·설정 키·포트·factory·단독 CLI·LangGraph/worker 단계·스키마·fixture를 제거했다. 거부 검증용 테스트와 폐기/과거 이력 안내에는 명칭을 남겼다.
- 현재 네 구성 역할은 A02·A04_A05·A06·A07이다. A01·A08은 공통 실행 코드다.
- 기준 승인 시 조건의 원문·인용·해시·버전을 EvidenceBundle로 고정한다. 별도 역할 실행 기록·모델·외부 조회를 만들지 않는다. PostgreSQL 경로에서는 승인·고정 스냅샷·검수 접수를 같이 저장한다.
- 최신 통합문서의 PG→Neo4j→PG 근거 조회는 후속 공통 문맥 준비 기능으로 명시했다. 별도 GraphDB 에이전트 배치는 미확정이다. 현재 함수의 complete는 승인 출처 묶음의 범위이며 관계 조회 완료가 아니다.
- 변경 없는 pass의 직접 최종 승인, 수정 시에만 수정 요청 승인, 새 실행에서 기준 재승인·전체 검사를 문서·흐름도에 반영했다.
- 담당자 가이드·PostgreSQL 실행가이드·전체 계획·개발 README·HTML·예제 안내·CI와 두 흐름도를 맞췄다. 기존 공유용 흐름도의 작은 노드·확대/이동·오른쪽 inspector UI를 유지했다.
- 기존 검증 보고서는 과거 이력으로 표시했고 당시 시험 결과를 현재 결과로 덮어쓰지 않았다. ZIP은 만들지 않았다.

## 계약 전환

JSON 계약은 2.0.0이다. A03 binding·RetrieveRequest·retriever가 없어졌고 EvidenceBundle의 retrieval_id는 context_id, 검증 이슈 retrieval_incomplete는 context_incomplete로 바뀌었다. HTTP 경로 /api/framework/v1은 별도 버전이다. 구버전 JSON/설정은 새 template과 fixture로 교체하며 기존 저장 실행을 새 코드로 자동 변환·재개하지 않는다.

운영 미적용 개발 AF001의 역할 CHECK에서 A03을 제거했다. 이전 해시의 AF001을 이미 적용한 별도 환경은 readiness가 거부한다. 그 환경은 저장 실행을 확인한 후 명시적인 후속 마이그레이션이 필요하다. 운영 DB에는 이번 DDL을 적용하지 않았다.

## 실행 검증

잠금 의존성으로 만든 임시 Python 환경에서 다음을 실행했다.

```sh
python -m pytest tests/agent_framework -q
python -m pytest tests/agent_framework/test_postgres_integration.py -m db_integration -q
python -m backend.agent_framework demo
python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json
python -m examples.agent_framework.file_flow_demo
```

일반 회귀 **414 passed, 8 subtests passed, 16 DB 시험 deselected**. SWIG 라이브러리의 기존 DeprecationWarning 5개가 발생했다. DB 시험은 별도 로컬 PostgreSQL 17.11 컨테이너에서 **16 passed**였다. 기존 M008·인증 fixture가 매번 무작위 creative_checker_test_* DB를 생성·제거했다. 업무 DB·사용자 자료·.env를 사용하거나 변경하지 않았다.

A03 설정 거부, 네 역할 port, 승인 시 근거 고정·문맥 해시, 위조 인용·다른 문서 거부를 직접 검사했다. demo는 부모 A02/A04_A05/A06/A07 완료와 자식 A02 reused + 세 검수 역할 완료를 확인했다. check-flow passed=true. 실제 파일 예제도 임시 PDF·PNG의 검증과 새 버전 재검수를 통과했다. 이 실행들의 승인자는 스크립트 테스트 값이다.

Graphify 지도는 현재 Python 25파일에서 다시 추출했다. 독립 검색 코드 삭제에 따른 노드 감소를 확인한 후 재생성했다. 정적 검사에서 HTML 22개·상대 링크 466개·JavaScript 문법 21건·내장 JSON 7건을 확인했고 오류는 없었다. 두 흐름도에서 A03 노드·역할 필터가 없는지 확인했다. 브라우저 실렌더링과 실제 Windows 장치 실행은 미검증이다.

## 후속 작업

실제 PDF/Qwen·통합 검사·보고 어댑터, 공통 PostgreSQL→Neo4j→PostgreSQL 관계 문맥 준비, front-v2 업무 연결, 운영 DB 적용·배포·백업 복원은 후속 작업이다. 현재 테스트 결과가 이 기능들의 실제 연결·성능·운영 배포를 증명하지 않는다.
