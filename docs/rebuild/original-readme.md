# 크리에이티브체커

문서의 요구사항을 포스터·영상과 대조하고, 근거 확인 → 수정 요청 → 새 버전 재검수 → 사람의 최종 승인까지 기록하는 업무 앱입니다.

이 브랜치는 [Issue #29](https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team/issues/29)의 재구축 구현입니다. **실제 PostgreSQL·Neo4j 통합과 Linux 이미지 실행을 확인한 개발 단계입니다.** 실제 Qwen 검증과 운영 배포의 범위는 [검증 기록](docs/rebuild/verification-20261030.md)을 따릅니다. 기존 DB를 자동으로 이관하지 않습니다.

## 실행 경로

| 역할 | 현재 경로 |
| --- | --- |
| 화면 | React·TypeScript·Vite, `frontend/src/main.tsx` |
| 업무 API | FastAPI, `backend.creative_checker.api:create_app` |
| 업무 실행 | `backend.creative_checker.worker`, PostgreSQL 작업·lease와 LangGraph checkpoint |
| 처리 도구 | `backend.creative_checker.tools:create_app`, 인증된 MCP 문서·미디어·추론·근거 도구 |
| 원본 기록 | PostgreSQL `cc_*` 테이블과 LangGraph checkpoint |
| 관계 조회 | PostgreSQL outbox → Neo4j, 관계 탐색·조건 변경 영향 조회 |
| 파일 | 로컬 개발 저장소 또는 S3, 원본과 파생 근거를 별도 보관 |

`run.cmd`·`run.sh`·`start.py`와 프런트의 기본 실행 명령은 위 경로를 사용합니다. 구형 `backend/app`, JSX 화면, 연구 스크립트와 문서는 아직 남아 있습니다. 새 진입점에서 사용하지 않으며 **구형 소스 제거는 미완료**입니다. 기존 연구 결과의 정확도·처리 시간을 새 구현의 성능으로 해석하지 않습니다.

## 업무 흐름

1. 운영자가 계정을 발급하고 프로젝트의 승인자·작업자·조회자 권한을 지정합니다.
2. PDF·DOCX·HWPX 문서의 본문·표를 추출하고 Qwen이 조건을 해석합니다. 사람이 조건과 AND·OR·예외 관계를 편집·승인합니다.
3. 포스터·영상과 프로젝트 기준 로고를 등록합니다. 실행은 조건·입력 파일·로고 버전과 모델 설정을 고정합니다.
4. Qwen3.5-4B가 문구·시각 관측과 근거·1차 판정을 함께 반환합니다. 코드가 출처·좌표·수치·누락을 검증하고 보고·수정 요청 초안을 연결합니다.
5. 승인자가 수정 요청을 확정하면 새 버전을 제출해 적용 기준 전체를 재검사합니다. 최종 승인과 예외 사유는 원판정과 별도로 보존합니다.

A03·RAG·VectorDB는 사용하지 않습니다. A04·A05는 하나의 통합 검사 역할입니다. A02·통합 검사·A07은 모델, A01·A06·A08은 코드가 담당합니다. 다른 유료 모델로 자동 전환하지 않습니다.

## 지원 범위와 한계

| 입력 | 한도 |
| --- | --- |
| 전자문서 | PDF·DOCX·HWPX 20MiB, PDF 50페이지, 추출 본문 10만 자 |
| 포스터 | PNG·JPEG·WEBP 20MiB, 1,000만 픽셀 |
| 영상 | MP4·WebM 60초·100MiB, 긴 변 1920·짧은 변 1080 이하 |

영상은 최대 12개 프레임을 4개씩 검사합니다. 미관측 구간·음성·정확한 연속 노출시간을 검사했다고 표시하지 않습니다. 로고와 명시된 배치·색상 검사는 모델 관측의 한계가 있으며, 현재 별도의 픽셀 정합 알고리즘이나 보정된 정확도 근거는 없습니다. 근거가 부족한 결과는 ‘확인 필요’로 남깁니다.

실행 상태, 기계 판정, 사람의 승인은 서로 다른 기록입니다. 빈 결과·미지원·실패를 통과로 바꾸지 않으며, 예외 승인에도 원판정과 사유를 남깁니다. 보고서는 브라우저 인쇄/PDF 저장을 사용합니다.

## 개발·협업

- 설치·실행·복구: [실행가이드](실행가이드.md)
- 기능·API 책임: [재구축 계약](docs/rebuild/architecture.md)
- 구현·검증 현황과 남은 작업: [작업 현황](docs/rebuild/status.md)
- 제품 범위: [PRODUCT.md](PRODUCT.md)
- Issue·브랜치·리뷰 규칙: [CONTRIBUTING.md](CONTRIBUTING.md)

새 기능은 화면 → API → 작업 → 저장 → 조회까지 연결된 경로와 책임이 있어야 합니다. 사용처가 없는 인터페이스·미리 만든 대체 실행 경로를 추가하지 않습니다. 과거 `docs/DB_Settings`, `docs/project-specification.md` 및 OCR·modeltest 문서는 당시 구현·연구 자료이며 이 브랜치의 실행 안내가 아닙니다.
