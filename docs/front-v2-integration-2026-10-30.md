# front-v2 로컬 통합

`feat/29-front-v2`에 main(a938f6c), 재구축(03400bb), 모델 테스트(7ea8e17)를 통합했다. 재구축과 모델 테스트의 부모에 main이 포함되어 있으므로 main을 다시 병합하면 already up to date가 정상이다. 원격 push는 수행하지 않는다.

## 실행 프로필

기본 프론트는 front-v2 이미지·PDF 검수를 유지한다. 영상은 `/video`, 모델 테스트는 `/modeltest`에서 연다. `npm run dev` / `npm run build`를 사용한다. 기존 PostgreSQL v1 화면은 `dev:v1` / `build:v1`, 재구축 업무는 `dev:rebuild` / `build:rebuild`로 실행한다. 세 프로필은 세션·CSRF·API 계약이 다르므로 임의의 서버에 대체 연결하지 않는다. `API_PROXY_TARGET`으로 같은 프로필의 백엔드 주소를 지정할 수 있다.

재구축 프론트도 공용 Fairweather 랜딩, 가로 업무 메뉴, 검수 목록·이미지·상세의 3열 배치, 확대·이동·선택 근거 확대를 사용한다. 승인·수정 요청·재검수 API와 상태·권한 조건은 재구축 구현을 유지한다. `frontend/dist`는 기본 화면, `dist-v1`과 `dist-rebuild`는 각 프로필 빌드다. 후자는 Git에서 제외하고 필요할 때 생성한다.

재구축 API는 `UV_PROJECT_ENVIRONMENT=.venv-rebuild uv sync --locked` 후 `.env.rebuild.example`을 참고해 로컬 설정을 준비하고 `UV_PROJECT_ENVIRONMENT=.venv-rebuild uv run --no-sync --env-file .env.rebuild python start.py api`로 실행한다. worker/projection/mcp도 같은 명시적 컴포넌트 인수를 쓴다. 기존 OCR 환경 `.venv`를 보존하기 위해 재구축 의존성은 `.venv-rebuild`에 설치한다. 기존 OCR 준비 옵션과 기존 v1 실행·배포 진단은 기존 진입점을 보존했다. 데이터베이스 마이그레이션·계정 발급은 해당 재구축 문서의 명시적 절차로만 수행한다.

## 검증 범위

프론트 단위 테스트, v1 계약 테스트, TypeScript 검사, 세 프로필 빌드를 함께 실행한다. Python 기본 pytest는 기존 API/문서/영상/모델 테스트 및 재구축 단위·HTTP·worker 계약을 포함한다. 실제 GPU·모델 추론과 별도 PostgreSQL 배포 테스트는 기본 실행에서 제외된다.

재구축 PostgreSQL·Neo4j 통합 검증은 기존 5432 DB가 아닌 전용 25429/57629 서비스에서 격리 schema와 테스트 프로젝트만 사용한다. 검사에 쓰는 모델 응답은 명시적 합성 응답이며 실제 모델 정확도를 뜻하지 않는다. 브라우저 검증도 별도 임시 DB/파일과 합성 결과를 사용한다. 기존 사용자 프로젝트·검수 기록·녹화·모델 캐시는 변경하지 않는다.

오프라인 모델 테스트의 포트 할당은 실제 소켓을 여는 대신 주소 선택을 검증하는 mock을 사용한다. 8,001자 입력의 pytest parameter에는 짧은 ID를 붙여 자식 프로세스 환경 크기 제한을 피한다.

## 실행 결과

- 프론트 단위 테스트 96개, v1 계약 테스트 35개 통과. TypeScript 검사와 세 프로필 프로덕션 빌드 통과.
- Python 기본 회귀 테스트와 전용 PostgreSQL·Neo4j 통합 테스트 6개 통과. 통합 테스트 모델 응답은 합성 응답이다.
- Chrome에서 재구축 로그인 → 문서 → 실행 목록 → 결과·근거 확대 → 수정·예외 승인 화면을 확인했고 결과 화면 새로고침 후 세션 유지도 확인했다.
- PostgreSQL 웹 프로필에서 영상 조회·실행에 DB 세션 경계를 적용하고, 중단된 영상 실행 복구를 연결했다. 모델 테스트 SSH 저장 요청은 기본·v1 모두 CSRF 토큰을 준비한 뒤 전송한다.
- 테스트 컨테이너에는 로컬 캐시의 PostgreSQL 17.11과 Neo4j 2026.10 이미지를 사용했다. 배포 compose 기본 Neo4j 5.26 이미지를 다시 내려받아 검증한 것은 아니다.
- 실제 RunPod/GPU 모델 추론, 모델 품질 평가, 운영 배포·전체 복구 리허설은 이번 병합 검증 범위에 포함하지 않는다.
