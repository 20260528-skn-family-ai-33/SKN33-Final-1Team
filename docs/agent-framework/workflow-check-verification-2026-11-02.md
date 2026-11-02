> 이전 검증 이력: 이 보고서는 독립 A03 제거·계약 2.0.0 적용 전의 코드와 시험 결과다. 현재 구조와 최신 검증은 [A03 제거 정리](a03-retirement-2026-11-02.md) 및 [담당자 가이드](에이전트_프레임워크_가이드.html)를 따른다.

# 설정 기반 전체 흐름 검증 — 2026-11-02

## 구현 범위

`feat/agent-framework`에 `check-flow --config ... --case ...` 명령과 `FlowCheckCase` 계약을 추가했다. 팀 설정의 다섯 역할을 한 번씩 초기화하고 기존 `Workflow`로 다음 여섯 동작을 실행한다.

1. 최초 실행 생성 및 A02 추출.
2. 테스트 조건 승인 및 A03→A04_A05→A06→A07 검사·보고.
3. 테스트 수정 승인.
4. 같은 PDF·조건 버전, 새 실행·포스터 버전으로 재검수 생성. A02 조건 초안은 재사용.
5. 수정본의 테스트 조건 승인 및 전체 검사·보고.
6. 수정본이 `pass`이면 정상 최종 테스트 승인.

오류·결과 불명·기대 불일치가 나면 나머지 동작을 건너뛴다. 자동 재시도나 예외 승인은 하지 않는다. 기존 사람 승인·기계 판정·실행 상태를 유지하며 원본/자식 이력과 A02 재사용 출처를 확인한다.

사례 JSON·구성은 factory 실행 전에 검증한다. 입력·구성·스냅샷의 정규 JSON SHA-256과 동작별 상태·판정·역할 시간·오류를 보고서로 제공하며 원문·프롬프트·역할 출력 본문·승인 사유는 포함하지 않는다. 초기화 실패는 기존 출력 보고서를 유지한다.

## 실행 결과

프로젝트 루트의 기존 `.venv`를 사용했다.

```bash
.venv/bin/python -m backend.agent_framework check-flow --config examples/agent_framework/team_config.synthetic.json --case examples/agent_framework/workflow.synthetic.json --output docs/agent-framework/workflow-check-result-2026-11-02.json
.venv/bin/python -m examples.agent_framework.file_flow_demo
.venv/bin/python -m pytest tests/agent_framework backend/tests/test_c09_contracts_storage.py -q
```

| 확인 | 결과 |
|---|---|
| 기본 합성 설정·사례 | 여섯 동작 기대 일치, CLI 종료 0 |
| 생성 PDF·실제 PNG 두 버전 + 설정 factory | 여섯 동작 기대 일치, 최초 `fail`→수정본 `pass`→`final_approved` |
| 전체 회귀 | **487 passed, 9 subtests passed** |
| 신규 전체 흐름 검사 | **36개 통과** |
| 독립 임시 복사본 | 허용 목록의 파일만 복사한 환경에서 파일 전체 흐름 예제 성공 |
| 정적 공유 경로 검사 | Windows 파일 이름·상대 링크 통과, 자동 외부 HTML 자원 0개 |

테스트는 설정 factory의 역할별 1회 초기화, 최초 5회·수정본 4회 호출, 각 단계의 실패와 결과 불명, 최초/수정본 기대 판정 불일치 시 중단, 구체적 계약 오류, 비공개 본문 제외, 잘못된 사례·출력 경로 검증, 미설정 역할의 명시적 실패, 기존 보고서 보존을 확인했다. 개발 API도 TestClient와 설정 factory로 같은 승인·재검수 여섯 동작을 확인했다.

생성 파일 예제는 기존 `services.store_file`·`LocalStorage`·파일 버전 모델을 격리 SQLite와 임시 폴더에서 사용한다. PDF에서 `Display exactly 3 green marks.`를 읽어 조건을 만들고, 포스터의 정해진 픽셀 위치를 세어 2개와 3개를 구분한다. 실제 바이트·파일 연결·해시를 검증한 뒤 콜백을 호출한다. 모델 호출은 0회이며 검색·보고와 승인은 합성 테스트 입력이다. 임시 데이터는 종료 후 정리된다.

저장한 실행 결과:

- [기본 설정 전체 흐름 보고서](workflow-check-result-2026-11-02.json)
- [생성 파일 전체 흐름 보고서](file-flow-demo-result-2026-11-02.json)

기존 pytest-asyncio 설정 경고 2개와 PyMuPDF SWIG 경고 5개가 발생했으며 테스트 실패는 없었다. 스키마·합성 사례·개발 HTML 안내·소스 뷰어·AST 지도를 재생성했다. CI에 두 명령을 추가했으며 원격 실행 결과는 확인하지 않았다. `git diff --check`는 통과했다.

## 검증 경계와 보존

`passed`는 명시한 개발 기대와의 일치다. 운영 사용자 인증·사람 승인·Qwen 정확도·PostgreSQL/Neo4j 실서비스 연결·영속 worker·재시작 복구를 검증한 결과가 아니다. 사례에 실제 담당 factory를 지정하면 그 코드의 모델·DB 접속이 실행될 수 있으므로 별도 개발 사례와 환경을 사용한다.

Windows 실제 실행, 브라우저 렌더링, 새 가상환경 설치는 확인하지 않았다. 독립 복사본은 같은 기존 가상환경을 사용했다.

기존 front-v2·운영 DB·업로드·환경 파일과 공유 ZIP은 변경하지 않았다. 공유 `에이전트_흐름도/index.html`의 SHA-256은 `ac071cbb1c59611b026af89cbe5f532730421702ea48a8097da8a5fceba687ef`로 유지됐다. 커밋·푸시는 수행하지 않았다.

명령과 사례 계약은 [전체 흐름 검증 가이드](README.md#설정한-어댑터의-전체-흐름-검증)를 참고한다.
