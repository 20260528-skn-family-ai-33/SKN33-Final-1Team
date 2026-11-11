# Creative Checker 문서

| 목적 | 기준 문서 |
| --- | --- |
| 제품 범위와 최신 결정 | [프로젝트 공통 맥락](프로젝트_공통맥락.md), [원문 채팅 색인](프로젝트_채팅_색인.md) |
| 실행과 환경 구성 | [프레임워크 안내](agent-framework/README.md), [Mac·Windows 공통 실행](agent-framework/Mac_Windows_공통실행_가이드.md), [환경 설정](environment.md) |
| 포스터 검수 | [A02 기준 추출](agent-framework/A02_실행_사용가이드.md), [검수·재검수](agent-framework/포스터_검수재검수_가이드.md), [로고 API](poster-logo-flow-api.md) |
| 영상 검수 | [포스터·영상 병행 실행](agent-framework/영상_포스터_로컬_병행실행.md), [영상 자막 엔진·기준 추출](agent-framework/영상_자막_Qwen3VL_연결가이드.md), [영상 로고 API](video-logo-flow-api.md) |
| 데이터와 그래프 | [PostgreSQL 실행](agent-framework/에이전트_프레임워크_PostgreSQL_실행가이드.md), [Neo4j 근거 검색](agent-framework/Neo4j_근거검색_RAG_연결가이드.md), [자동 복구](agent-framework/Neo4j_프로젝트_자동복구_가이드.md) |
| AWS 운영과 인수인계 | [11월 10일 운영 기록](agent-framework/AWS_인프라_운영현황_20261110.md), [구축 설정](agent-framework/AWS_구축_설정_인수인계_20261110.md), [11월 11일 로컬 조치와 수용 확인](agent-framework/AWS_TEST_로컬_조치_20261111.md) |
| 평가 입력과 시나리오 | [포스터 3종 단계별 시험 기록](poster-baselines/2026-11-03/workflow/진행현황.json), [프로젝트 계획](planning/) |

## 원본과 생성본

- 실행·운영 가이드는 위의 Markdown 원본을 수정한다. 같은 이름의 HTML은 배포하거나 열람할 때 사용하는 사본이다.
- [업무 흐름도](agent-framework/flow.html)와 [소스 뷰어](agent-framework/source.html)는 [흐름 데이터](agent-framework/flow-data.json)와 저장소의 소스에서 생성한다. 원본이나 계약을 변경하면 `uv run python -m scripts.build_agent_flow_html`로 갱신한다.
- [OpenAPI와 JSON 스키마](../examples/agent_framework/)는 프레임워크의 API와 모델에서 `uv run python -m scripts.export_agent_framework`로 생성한다.
- 운영 기록과 평가 결과는 기재한 커밋, 환경, 입력과 실행 날짜에 적용한다. 현재 범위는 공통 맥락과 실제 실행 결과에서 확인한다.
