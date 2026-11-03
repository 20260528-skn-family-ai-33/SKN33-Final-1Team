# 코드 구조와 명명 규칙

새 코드는 아래 책임 구분에 맞춰 추가합니다. 실행 프로필은 서로 다른 API 계약을 사용하므로 해당 프런트엔드와 백엔드를 함께 실행합니다.

## 실행 프로필

| 프로필 | 백엔드 | 프런트엔드 | 테스트 |
| --- | --- | --- | --- |
| 기존 이미지·PDF·영상·모델 테스트 | `backend/app/api.py` (`web_postgres.py`로 DB 연동) | `frontend/src/pages/` | `tests/app/` |
| 기존 PostgreSQL v1 | `backend/app/main.py` | `frontend/src/v1/` | `tests/app/` |
| 재구축 업무 | `backend/creative_checker/api.py` | `frontend/src/rebuild/` | `tests/creative_checker/` |
| 공통 에이전트 프레임워크 | `backend/agent_framework/` | 별도 프레임워크 API | `tests/agent_framework/` |

기존 실행 명령은 [README](../README.md)의 프로필 표와 [실행가이드](../실행가이드.md)를 따릅니다. `start.py`, `run.sh`, `scripts/run_v1.py`의 위치와 사용법을 유지합니다.

## 백엔드 배치

```text
backend/
  app/                    기존 시제품과 PostgreSQL v1
    documents/            문서 원문, PDF 조건 추출
    ocr/                  OCR 설정, 실행, 결과 변환, 워커
    logos/                로고 모델 목록, 판정, 실행 경계
    reviews/              문구·행사 정보 비교와 검수 규칙
    video/                영상 프레임, 추적, 자막, 판정
      logos/              영상 로고 후보·대조·API·워커
    modeltest/            모델 테스트 화면의 서버 기능
      contracts.py        입력 규격과 검증
      catalog.py          모델 계열과 메타데이터
      registry.py         등록 모델 관리
      routes.py           HTTP 요청 처리
      ssh.py              SSH 전송과 독립 실행 소스 구성
      runner.py           원격 실행
      adapters.py         모델별 어댑터
      logo_comparison.py  로고 차이 분석
      prompts.py          프롬프트 처리
      prompts.json        프런트엔드와 서버의 공통 기본값
    contracts/            기존 업무 API의 입력·출력 규격
    services/             기존 업무 서비스
    persistence/          기존 DB 접근과 스키마 계약
    execution/            기존 검수 작업 실행
  creative_checker/       재구축 업무
    api.py                앱 생성, 미들웨어, 예외 처리
    http.py               HTTP 의존성, 업로드 처리
    routes/               인증·프로젝트·문서·납품물·실행·승인·그래프 라우터
    services/             접근 제어·파일·문서·납품물·실행·승인 업무 함수
  agent_framework/        공통 에이전트와 영속 작업 실행
  migrations/             기존 DB 마이그레이션
```

HTTP 처리, 업무 규칙, 모델 실행의 책임을 구분합니다. 다른 기능에서도 사용하는 코드는 실제 공통 책임이 확인됐을 때 옮깁니다. 파일 하나를 줄이려고 의미 없는 `utils.py`나 `manager.py`를 추가하지 않습니다.

`backend.creative_checker.services`는 기존 공개 함수의 import 경로를 유지합니다. 구현을 찾을 때는 `services/documents.py`, `services/runs.py`처럼 담당 기능으로 들어갑니다. 기존 인증·DB 모듈을 재사용하는 프레임워크의 의존 관계는 유지합니다.

## 프런트엔드와 테스트

- 화면과 그 화면 전용 로직은 같은 기능 폴더에 둡니다. 기존 검수의 `workflow`, 문서 조건·진행률 로직은 `frontend/src/pages/review/`에 있습니다.
- 재구축의 API·인증·타입·공통 UI는 `frontend/src/rebuild/`, 화면은 그 아래 `pages/`에 있습니다. 여러 프로필이 함께 쓰는 `components/`와 `auth/`는 기존 위치를 유지합니다.
- Python 테스트는 루트 `tests/` 아래에 구현 패키지별로 둡니다. 테스트 파일과 보조 모듈 이름에는 개발 단계 번호 대신 검증할 책임을 씁니다. 예: `test_worker.py`, `test_review_contracts.py`, `review_fixtures.py`.
- JavaScript 단위 테스트는 해당 모듈 옆의 `*.test.js`, 브라우저·v1 테스트는 기존 `frontend/tests/`에 둡니다.
- `scripts/`는 실행 명령의 진입점입니다. 재사용하는 업무 구현은 해당 백엔드 패키지에 둡니다.

## 명명 규칙

| 대상 | 규칙 | 예 |
| --- | --- | --- |
| Python 파일·모듈·함수·변수 | `snake_case` | `logo_comparison.py`, `create_run` |
| Python 클래스 | `PascalCase` | `ReviewCreate`, `PostgresStore` |
| 상수 | `UPPER_SNAKE_CASE` | `MODEL_STAGES` |
| React 컴포넌트·화면 | `PascalCase` | `Projects.tsx`, `ReviewPage.jsx` |
| JavaScript·TypeScript 함수·유틸리티 파일 | `camelCase` | `documentRequirements.js` |
| React 훅 | `use` + `PascalCase` | `useApi.ts` |
| Python 테스트 | `test_<responsibility>.py` | `test_structure_compatibility.py` |

폴더가 이미 기능을 나타내면 파일명에 같은 접두사를 반복하지 않습니다. 예: `modeltest_catalog.py` → `modeltest/catalog.py`, `video_logo_compare.py` → `video/logos/compare.py`. `models`는 모델 정의·처리, `catalog`는 지원 모델 목록, `runner`는 실행, `routes`는 HTTP 경계를 나타내도록 씁니다. 기존 API 스키마 클래스와 외부 필드 이름은 계약을 우선합니다.

`.editorconfig`는 새 편집의 인코딩·줄 끝·들여쓰기 기본값을 정합니다. 기존 파일 전체를 재포맷하지 않습니다. 고정 해시가 있는 파일의 Git 줄 끝 설정은 `.gitattributes`를 따릅니다.

## 호환성 범위와 확인 명령

API URL·요청/응답 필드·OpenAPI 스키마, DB 마이그레이션과 데이터 형식, 기존 실행 프로필을 유지합니다. 이전 OCR·로고 워커의 `python -m backend.app.<기존 모듈>` 명령은 작은 연결 모듈을 통해 새 구현을 실행합니다. 새 코드의 import는 기능별 패키지를 사용합니다. 실행 진입점이 아닌 내부 모듈의 예전 import 경로는 유지하지 않습니다.

모델 테스트 SSH 실행기는 저장소 없이 실행할 수 있도록 필요한 모듈과 기본 프롬프트를 함께 전송합니다. 이 경계와 기존 워커 진입점은 `tests/test_structure_compatibility.py`에서 검사합니다. 에이전트 프레임워크의 소스 경로·내용은 유지해 대기 작업과 비교하는 실행 해시가 바뀌지 않도록 합니다.

```sh
python -m pytest -q
cd frontend
npm test
npm run test:v1
npm run typecheck
npm run build
npm run build:v1
npm run build:rebuild
```

기본 pytest는 DB·외부 OCR·통합·실모델 검사를 제외합니다. Windows에서는 `PYTHONUTF8=1`과 쓰기 가능한 저장소 외부 `TEMP`/`TMP` 경로가 필요할 수 있습니다. 실제 DB·GPU 검증은 각 통합 가이드의 별도 환경에서 실행합니다.

`share/`, 날짜가 붙은 검증 보고서와 결과물은 배포·검증 당시의 사본입니다. 과거 경로가 남아 있을 수 있으며, 현재 개발 구조는 이 문서와 실제 소스를 기준으로 확인합니다.
