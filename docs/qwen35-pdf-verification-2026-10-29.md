# Qwen3.5-4B PDF 조건 추출 구현·검증 — 2026-10-29

브랜치: `feat/qwen35-pdf-multimodal`. 기존 `feat/highres-poster-finetune`의 커밋 `3cbc129`에서 새 브랜치를 만들고 미커밋 작업을 보존했습니다.

## 구현

PDF를 페이지별로 렌더링하고 기존 PaddleOCR로 읽은 뒤, **같은 페이지의 PNG 이미지와 OCR 텍스트를 Qwen3.5-4B에 함께 전달**합니다. thinking을 끄고 JSON 스키마와 Pydantic으로 형식을 검사합니다. 로컬 loopback 전송 정책과 PDF 격리 프로세스를 재사용합니다.

ID와 필수·권장·선택이 함께 있는 기준표가 2행 이상 발견되면 그 표를 추출 범위로 사용합니다. 이번 7쪽 계약서는 7쪽 모두 OCR로 읽고, 4·5·6쪽의 이미지와 표를 페이지별 3회에 나눠 모델에 전달했습니다. **표 밖의 추가 조건을 자동으로 합치는 범위는 아닙니다.** 선택 범위는 `source_selection`과 화면 안내에 표시하며, 선택한 행이 누락되면 오류로 반환합니다. 기준표가 없으면 전체 OCR 구간을 페이지별로 사용합니다.

조건 JSON은 `label`, `requirement`, `level`, `inspection`, `type`, `requirement_id`, `source_id`, `page`, `source`, `source_alignment`, `approval_status: pending`을 보존합니다. 출처의 `source`는 교정하지 않은 OCR이며 모델이 재작성한 인용문이 아닙니다. 잘못된 머리말 인용에 대비해 문자 2-gram 겹침으로 후보 근거를 대조합니다. 이 값은 의미 정확도나 신뢰도 확률이 아닙니다.

`REQUIREMENTS_LLM_MODEL` 기본값과 pull 대상은 `qwen3.5:4b`입니다. 설치 모델의 vision 지원을 먼저 확인합니다. 서버 처리 제한 600초, 격리 프로세스 제한 610초, 화면 응답 대기 제한 630초를 적용했습니다.

## 실제 실행

환경: Apple M4 Pro·통합 메모리 24GB, Python 3.12.14, Ollama 0.40.3, 프로젝트 전용 모델 저장소. 확인된 실행 형식은 `mlx` / `nvfp4`입니다.

모델 digest: `7f3251fa6878a78a606bcb3c074283306e5ad2ea411a7774a9dd98a4bc7f2d13`  
추출기 SHA-256: `694a6fb9631549b68aac9c960651d98422ad172fdfec1cff763583af167dcf24`

| 입력 | OCR 페이지 | 모델 이미지 | 조건 | 페이지 준비·OCR | 모델·결과 정리 | 전체 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 요구사항 1쪽 텍스트 PDF | 1 | 1 | 7 | 17.6초 | 12.7초 | 30.3초 |
| 같은 1쪽의 스캔 PDF | 1 | 1 | 7 | 18.3초 | 13.0초 | 31.4초 |
| 기존 계약서 전체 7쪽 | 7 | 3 | 20 | 125.3초 | 54.3초 | 179.6초 |

각 입력은 최종 코드에서 1회씩 순차 실행했습니다. 모델이 이미 로드된 상태이며 이전 요청의 캐시가 남을 수 있습니다. 다운로드 시간은 제외했습니다. 3개 입력은 **동일 가상 계약서의 1쪽, 그 스캔 변형, 전체 7쪽**이므로 독립 문서 3건의 성능 평가가 아닙니다. 시간은 일반 처리 속도나 SLA로 해석할 수 없습니다.

전체 문서의 선택 기준표는 R01~R07, D01~D07, T01~T02, E01~E04의 20행입니다. 추출 수준 집계는 `{'필수': 17, '권장': 2, '선택': 1}`입니다. 원문 행 ID와 예상 페이지 연결, 수준, 대표 값·예외 문자열을 `content-checks.json`에 기록했습니다. 사람 승인 정답으로 계산한 정확도·F1 평가는 수행하지 않았습니다.

## 내용 대조와 한계

- 03-김천김밥축제_포스터_디자인용역계약서_초안_v0.1.json / D02: 판단 기준 문자열 미검출: 실제건축물
- 03-김천김밥축제_포스터_디자인용역계약서_초안_v0.1.json / D03: 대표 내용 문자열 미검출: 여러층, 전통
- 03-김천김밥축제_포스터_디자인용역계약서_초안_v0.1.json / E02: 대표 내용 문자열 미검출: pdf

이번 결과에 실제 오탈자·내용 누락이 남았습니다. D02의 건축물은 건출물로, E02의 PDF는 PDE로 추출됐고, D03의 여러 층·전통 표현은 빠졌습니다. 원시 모델 출력을 수동으로 정답처럼 고치지 않았습니다. 대표 문자열 검사는 원문의 모든 문자를 검사하지 않으며, 필드 간 정보 이동도 허용합니다. 모델 출력은 검토 전 초안이고 모든 조건의 `approval_status`는 `pending`입니다. 복잡한 병합 표, ID가 OCR에서 사라진 행, 표 밖 추가 조건, 다른 페이지 사이의 예외·상충은 원본 대조가 필요합니다.

처음에는 3페이지를 함께 처리했으나 일부 세부 요구 문구가 누락되어 최종 기본값을 1페이지로 조정했습니다. 계약 본문까지 추출해 6,000자를 초과하던 실행은 성공으로 취급하지 않았고, 명시적 기준표 선택과 누락 검사를 추가했습니다.

## 코드 검증

- 관련 백엔드·런처 회귀 테스트 **178개 통과**. 실제 PDF 렌더링, 이미지 전달, 스캔 입력, 페이지·원문 연결, 기준표 행 누락, 오류·한도·동시 요청을 검사했습니다. 이 테스트의 모델 추론은 모의 응답입니다.
- 프런트엔드 테스트 **23개 통과**, Vite 프로덕션 빌드 통과. PDF 이미지+OCR 출처와 입력 보존을 확인했습니다.
- 별도 실제 업로드 API 검사: HTTP 200, 조건 7개 반환. 증거는 `api-smoke.json`을 참고합니다.

## 재현

```sh
.venv/bin/python scripts/local_ollama.py serve
# 다른 터미널에서, 모델이 없는 환경에만 필요
.venv/bin/python scripts/local_ollama.py pull
.venv/bin/python scripts/verify_pdf_requirements.py \
  .runtime/pdf-input-smoke/requirements-page.pdf \
  .runtime/pdf-input-smoke/requirements-scan.pdf \
  '/Users/choejiheum/SK_AI/최종프로젝트/기준문서/김천김밥축제_용역계약_초안/김천김밥축제_포스터_디자인용역계약서_초안_v0.1.pdf' \
  --output .runtime/qwen35-pdf-reproduce
```

기존 로컬 앱은 `run.sh` / `run.cmd`로 실행하면 설치된 모델 서버를 자동 시작합니다. `.env.example`은 예시이며 자동 로드를 보장하지 않습니다.

## 검증 파일

- [조건 JSON](/Users/choejiheum/SK_AI/SKN33-Final-1Team/.runtime/qwen35-pdf-verification-pagewise/extracted-conditions-full.json)
- [실행 요약](/Users/choejiheum/SK_AI/SKN33-Final-1Team/.runtime/qwen35-pdf-verification-pagewise/summary.json)
- [모델·실행 환경](/Users/choejiheum/SK_AI/SKN33-Final-1Team/.runtime/qwen35-pdf-verification-pagewise/runtime.json)
- [대표 내용 검사](/Users/choejiheum/SK_AI/SKN33-Final-1Team/.runtime/qwen35-pdf-verification-pagewise/content-checks.json)
- [실제 API 응답](/Users/choejiheum/SK_AI/SKN33-Final-1Team/.runtime/qwen35-pdf-verification-pagewise/api-smoke.json)
- [실행 안내](/Users/choejiheum/SK_AI/SKN33-Final-1Team/docs/pdf-requirements.md)

공식 인터페이스 근거: [Ollama 이미지 입력](https://docs.ollama.com/capabilities/vision), [구조화 출력](https://docs.ollama.com/capabilities/structured-outputs), [Qwen3.5-4B 모델](https://ollama.com/library/qwen3.5:4b).
