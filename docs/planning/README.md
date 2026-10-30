# Creative Checker planning

현재 열람 기준은 **[Creative_Checker_Planning.html](Creative_Checker_Planning.html) 한 파일**이다. 첫 화면에는 **10/30 회의 공유안**이 있고, 기획·기능·화면·기술·권한·DB·학습·모델·실험·아키텍처 11개 영역과 공통 업데이트도 모두 포함한다. 왼쪽 목차, 전체 검색, ‘전체 펼쳐 보기’로 이동한다. 현재 버전은 **2026.10.30-r3**이다.

회의 공유안은 현재 진행 상황, 통합문서 핵심, GraphDB 작동 원리·활용, 에이전트 추가 검토, 실험 기록, 결정할 안건, 후속 작업·예산·일정을 나누어 보여준다. 사용자 목표·팀 결정 필요·구현/실험 기록을 구분하며, 실제 참석자·회의 시간·의결 결과를 임의로 기록하지 않는다.

`organized/`의 기존 01~11 경로는 이전 링크 호환용으로 보존한다. 평소에는 통합본 하나를 열고, 내용 수정도 아래 공통 원본에서 수행한다. HTML을 각각 고칠 필요가 없다.

기준은 2026-10-30 프로젝트 통합 초안과 같은 날 사용자가 선택한 **PostgreSQL 원천 + Neo4j 관계 조회**이다. 팀 승인 상태는 별도로 관리한다. 포스터와 숏폼, 세 사람 승인 지점, MCP는 통합 목표이다. Qdrant는 필수 경로에서 제외하고 과거 실험으로 보존한다. 모델·세부 역할 배정·영상 한도·예외 정책은 미정 상태를 유지한다.

## 이번 PostgreSQL 연결 반영

로컬 웹에서 신규 검수의 PostgreSQL 자동 저장·조회를 연결하고 실제 2건·결과 14개를 검증했다. 새로고침·서버 재시작·JSON 사본 없는 DB 조회를 확인했다. 기존 JSON 이력은 보존하며 현재 웹 BackgroundTasks와 별도 PG durable worker를 구분한다. Neo4j 관계 원천·outbox·투영/ready·조회 API·MCP·화면은 향후 구현이다. DB 절에는 17:11 KST 기준 395개 기준·40건 검수·315개 결과와 이관/신규 구분을 추가했다.

## 문서 관리 위치

- `Creative_Checker_Planning.html`: 현재 열람·공유할 단일 HTML. 본문·표·그림·검색·스타일 및 작은 출처 자료를 파일 안에 포함한다.
- `organized/`: 기존 개별 HTML 경로. 통합본과 같은 원본에서 생성하며 링크 호환을 위해 남긴다.
- `content/planning-content.json`: 문서별 검토된 본문. 먼저 이 파일을 고친 뒤 HTML을 생성한다.
- `content/meeting-20261030.json`: 오늘 회의 공유안의 검토된 본문. 회의 후 합의 내용은 날짜·근거와 함께 이 원본 및 관련 Planning에 반영한다.
- `content/baseline.json`: 공통 개정일, 사용자 결정, 통합 초안과 코드 근거의 해시.
- `content/planning.css`: 공통 문서 서식.
- `content/release-manifest.json`: 생성된 HTML의 무결성 정보.
- `sources/`: 읽은 통합 초안의 원본 사본과 작은 실험 증빙. 원래 통합 초안은 수정하지 않았다.
- `archive/planning-before-20261030-r1.zip`: 개정 전 HTML 11개와 원본 해시. 복원할 때 현재 변경도 먼저 보존한다.
- `archive/planning-before-meeting-20261030-r1.zip`: 회의 공유안 추가 전 통합 HTML·생성 코드·버전 기록 보관본.

## 갱신 절차

1. 최신 사용자 결정과 통합 초안의 변경을 읽고, 영향받는 기획·기능·화면·기술·권한·결과 문서를 함께 검토한다.
2. 구현 상태는 코드와 실행 증거로 구분한다. 실험 수치는 날짜·표본·단위·환경·정답 승인 여부·원자료를 보존한다.
3. `planning-content.json`을 수정한다. 실제 재검토 후에만 `baseline.json`의 기준일·개정 번호·출처 해시를 갱신한다.
4. 기존 버전 보관본을 만든 뒤 저장소 루트에서 생성·검사한다. 같은 명령으로 통합본과 개별 호환본이 함께 갱신된다.

```sh
python3 docs/planning/tools/build_planning.py
python3 docs/planning/tools/build_planning.py --check
```

기본 검사는 실행 계정 홈 아래 `SK_AI/최종프로젝트/Creative_Checker_프로젝트_통합문서_20261030_초안.html`이 있으면 함께 대조한다. 다른 위치의 최신 통합문서를 확인하려면 다음처럼 명시한다.

```sh
python3 docs/planning/tools/build_planning.py --check --reference '/실제/최신/통합문서.html'
```

기준 파일·관련 코드가 바뀌면 검사는 갱신 필요로 종료한다. 검토 없이 날짜만 갱신하지 않는다. 생성된 HTML을 직접 편집하면 다음 생성에서 덮어쓰기를 중단하므로 수정 내용을 `planning-content.json`에 먼저 반영한다. 검사 통과는 문서와 근거 파일의 일치를 뜻하며, 서비스 동작·모델 품질·배포 완료를 보증하지 않는다.

마지막으로 브라우저에서 문서 간 이동, 탭·검색, 기능/화면 앵커, 표·그림을 확인한다. 자동 감시·외부 Google Docs/Sheets 동기화는 설정하지 않았다.

현재 저장·조회 실측은 기본 M008 웹 프로필 기준이다. 별도 `backend/creative_checker` 재구축 코드·`start.py api/worker/projection/mcp`의 존재와 실행 구성은 구분하며 해당 DB·실험 수치를 이 집계에 합산하지 않는다.
