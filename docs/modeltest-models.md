# HF 모델 등록과 실행 규격

`/modeltest`의 **Hugging Face 모델 추가**에서 저장소 URL, `owner/model`, 선택적 revision을 입력한다. 이름만 입력하면 소유자를 추측하지 않는다. 등록 기능은 로컬 개발 백엔드와 SSH 실행기에 연결된다. 별도 HTTP Pod 서버에는 이 등록 API·실행 코드를 자동 배포하지 않는다.

## 사용 순서와 상태

1. **메타데이터 확인**: HF API의 commit SHA·파일 목록과 작은 JSON 설정만 조회한다. README 명령·Python 코드·가중치를 실행하거나 다운로드하지 않는다.
2. **모델 등록·저장**: 판별된 계열을 확인한다. 불확실하거나 추가 옵션이 필요한 경우 실행 계열·설정 JSON을 수정한다. 설정·형식이 계열과 맞지 않으면 이유를 표시하고 실행을 차단한다.
3. **가중치·환경 준비**: 연결된 SSH Pod에서 라이브러리와 고정 revision 가중치를 준비한다. 첨부는 필요 없다. 기존 PyTorch·torchvision 버전을 제약으로 유지한다. 시스템 CUDA·Detectron2·컴파일러를 임의 설치하지 않는다. 기존 모니터·강제중단·새로고침 후 조회를 사용한다.
4. 워크플로우 노드의 **등록 모델**에서 설정을 선택한다. 지원 작업의 선택 상자에만 표시된다. 링크 직접 입력은 기존 프리셋에 사용할 수 있고 새 모델은 먼저 등록해야 한다.
5. **현재 워크플로우 샘플 실행**: 그 모델을 포함한 현재 워크플로우를 실행한다. 영상은 첫 추출 프레임, 문서 조건 추출은 첫 문서의 첫 페이지·텍스트 청크로 제한한다. 기존 첨부 필수 조건은 유지된다. 준비와 추론은 자동으로 연속 실행되지 않는다.

| 상태 | 의미 |
| --- | --- |
| 미검증 | 메타데이터와 실행 경로가 맞음. Pod 추론은 미확인 |
| 준비 필요 | checkpoint·라벨·파일 선택 등 등록 항목 부족 |
| 추가 구현 필요 | 지원하지 않는 구조·사용자 코드·헤드·실행 형식 |
| 실행 확인 | 동일 fingerprint의 실제 호출이 포함된 작업 성공. 품질·정확도나 다른 Pod의 성공을 뜻하지 않음 |

준비 성공은 별도 이력이며 `실행 확인`으로 바뀌지 않는다. 실제 버전은 결과의 `model_execution.versions`와 등록 이력에 남는다. 등록 모델의 실행은 라이브러리·파일 누락을 자동 다운로드로 숨기지 않고 준비를 요구한다. 기존 다섯 프리셋은 이전의 최초 실행 시 다운로드 경로를 유지한다.

등록은 로컬 SSH 저장소 아래 `models.json`에 원자적으로 저장하며 최대 100개다. 워크플로우·첨부는 기존 IndexedDB 저장을 유지한다. 등록 삭제는 Pod 캐시나 과거 결과를 지우지 않는다. 삭제된 모델을 참조하는 노드는 다시 선택해야 한다.

모델·revision·옵션·학습 artifact·라벨·임계값 변경은 fingerprint를 바꾸며 이전 실행 확인을 해제한다. 편집 시 임계값 재확인 체크가 없으면 분류 임계값을 비운다. 노드의 모델을 바꾸면 기존 후보 필터의 코사인 임계값을 비운다. 등록 설정 변경 후에는 노드에서도 모델을 다시 선택해야 하며 오래된 fingerprint 요청은 백엔드에서 차단한다.

## 계열과 지원 범위

공통 실행기는 기존 SSH·프레임 추출·DAG·시간 계측·취소·결과 저장을 재사용한다. `modeltest_catalog.py`는 계열·메타데이터, `modeltest_registry.py`는 등록, `modeltest_adapters.py`는 계열별 로딩·전처리·추론·출력 변환을 담당한다. 크기·해상도·파인튜닝 변형은 규격과 맞으면 모델별 함수를 추가하지 않는다.

| 실행 계열 | 후보 | 경로와 제한 |
| --- | --- | --- |
| qwen_text / qwen_vision | Qwen3-8B, Qwen3-VL-4B-Instruct, Qwen3.5 2B·4B·9B·27B | native Transformers 및 저장소 chat template·processor. Qwen3 텍스트는 이미지 입력 불가 |
| clip | CLIP, SigLIP2 Base·SO400M | 저장소 image processor·projection·정규화 설정 |
| dino | DINOv2 S/B, DINOv3 B/L·ConvNeXt | CLS, patch mean, pooler. mean에서 CLS·register 제외. ConvNeXt는 pooler |
| open_clip | EVA-CLIP, OpenCLIP 변환 모델 | `open_clip_config.json`, 일치하는 architecture, safetensors 파일 필요. 임의 pickle `.pt` 로드 불가 |
| sentence_text / sentence_multimodal | BGE-M3, multilingual-e5-small, Qwen3-Embedding-0.6B, EmbeddingGemma 2, Qwen3-VL-Embedding-2B | 공식 Sentence Transformers 모듈. BGE-M3는 **dense만** 지원. EG2·Qwen3-VL은 이미지·텍스트 입력 가능 |
| cross_encoder | BGE-reranker-v2-m3, Qwen3-Reranker-0.6B, Qwen3-VL-Reranker-2B | 공식 CrossEncoder classification / LogitScore. raw 또는 sigmoid 명시. Qwen3-VL만 이미지 후보 가능 |
| owlv2 / grounding_dino | OWLv2, Grounding DINO | 계열별 processor·후처리로 `bbox`·`score`·`label` 반환 |
| yolo | YOLO-World, YOLO11 | Ultralytics **ONNX** 파일 필요. World는 내보낼 때 고정한 어휘 사용. 동적 어휘 `.pt` 실행은 미연결 |
| paddle_vl / paddle_gguf | PaddleOCR-VL-1.6, GGUF | native Transformers / 기존 고정 llama.cpp. `OCR:` 텍스트 인식. 좌표 Spotting·전체 문서 파싱은 미연결 |
| paddle_ocr | PP-OCRv5 | detector·recognizer 저장소·commit SHA 각각 지정. PaddleOCR CPU 경로로 단어·polygon·bbox·점수 반환 |
| easyocr | EasyOCR 한국어·영어 | `craft_mlt_25k.pth`, `korean_g2.pth` 필요. `languages=["ko","en"]`. 자동 인식 파일 다운로드 비활성 |
| xfeat | vismatch/xfeat | 기존 고정 공식 구현·tensor 가중치·전처리·RANSAC 재사용 |
| lightglue | ETH-CVG/lightglue_superpoint | Transformers SuperPoint+LightGlue. 대응점 confidence·RANSAC 결과 분리 |
| vision_backbone / vision_classifier / timm | ResNet18, ConvNeXt V2, EfficientNet-B0, MobileNetV3 등 | native Transformers 또는 timm architecture·전처리·safetensors. 분류에는 학습된 head·라벨 필요 |
| linear_head | 학습한 SigLIP2·EG2 분류기 | encoder + safetensors `weight`·`bias` 선형 헤드. MLP·융합 헤드는 별도 연결 필요 |
| text_classifier | KoELECTRA, klue/roberta-base 기반 학습 모델 | 학습된 SequenceClassification checkpoint·라벨 필요. 원본 backbone을 분류기로 실행하지 않음 |
| layoutxlm | LayoutXLM 기반 학습 모델 | 학습된 TokenClassification checkpoint·라벨·Detectron2·OCR 단어·좌표·이미지 필요. 전체 문서 parser와 구분 |

ID 이름만으로 지원을 확정하지 않는다. `model_type`, architecture, 모듈·파일과 설치 환경을 검사한다. 알 수 없는 모델이나 불완전한 분류 가중치를 생성 로더로 대체하지 않는다. 공개 metadata를 읽을 수 있어야 등록 가능하며 제한된 가중치에는 Pod의 HF 접근 권한도 필요하다.

연결 규격은 Transformers 5.21+, Sentence Transformers 6.2+, OpenCLIP 3.3+, timm 1.0.30+ 등이며 계열 `requirements`에 버전 범위를 저장한다. 실제 버전을 비교하고 준비 시 필요한 패키지를 설치한다. `trust_remote_code=False`, local cache, safetensors를 사용한다. 시스템 의존성은 별도 준비가 필요할 수 있다. Sentence Transformers의 `max_length=0`은 저장소 길이를 유지하고 `prompt_name`·`prefix`를 비우면 저장소 기본 프롬프트를 유지한다.

## 직접 학습한 모델

원본 모델을 등록한 뒤 학습 설정을 넣는다. 파일은 별도 HF 저장소의 **고정 commit**으로 참조한다. 임의 로컬 경로·pickle·학습 스크립트는 실행하지 않는다.

```json
{
  "base_model_id": "google/siglip2-base-patch16-224",
  "base_revision": "원본의 40자리 commit SHA",
  "trained": true,
  "head": {
    "model_id": "owner/trained-head",
    "revision": "학습 헤드 저장소의 40자리 commit SHA",
    "filename": "head.safetensors",
    "sha256": "파일의 64자리 SHA256"
  },
  "labels": ["일치", "불일치"],
  "preprocessing": {"source": "repository"},
  "thresholds": {"일치": 0.8, "불일치": 0.8}
}
```

임계값은 예시다. 실제 학습 순서·정규화·pooling·전처리·임계값을 사용한다. SigLIP2 선형 헤드는 `linear_head`, `encoder_family=clip`, `pooling=projection`으로 설정한다. EG2는 `encoder_family=sentence_multimodal`, `pooling=repository`, `preprocessing={"source":"repository"}`로 저장소 처리를 유지한다. 다른 전처리로 학습했다면 그 설정을 담은 원본 저장소가 필요하다.

전체 모델은 `checkpoint: {model_id, revision}`, Qwen LoRA는 `lora: {model_id, revision}`을 사용한다. 원본 ID·revision은 등록한 원본과 같아야 한다. 라벨 수·순서, head 차원·SHA256, 로딩 시 누락 가중치를 검사한다. 필요한 항목이 없으면 준비 필요 또는 추가 구현 필요이며 임의 head를 만들지 않는다. 원본 링크만으로 과거 실험 재현을 주장하지 않는다.

## 새 노드와 결과

에이전트 내부 단계 추가에서 임베딩·재정렬·분류와 일반 처리 노드를 선택한다. `입력 설정 JSON` 예:

```json
{"documents":["후보 문서 1","후보 문서 2"],"query":"검색할 내용","image_source":"media","modality":"text"}
```

- `image_source`: `media`, `references`, `crops`. 크롭은 `images` 입력에 로고 추출을 연결한다.
- `text`: OCR·조건·응답 생성·텍스트 후보 노드 결과. 연결된 결과가 없으면 `documents` 사용.
- `vector_compare`: `left`·`right`에 벡터 노드를 연결한다. **모델·revision·옵션·학습 artifact fingerprint가 같은 벡터만** 비교한다. 차원만 같아서는 허용하지 않는다. 코사인 내림차순 결과에 후보 ID·텍스트·미리보기 출처를 유지한다.
- `rerank`: `candidates`에 BM25·벡터 비교·재정렬을 연결하거나 `text`/`documents` 사용. 이미지 후보는 `modality=image`. 입력 형태와 점수 변환을 별도 표시한다.
- `document_classify`: `ocr`에 PP-OCRv5 또는 EasyOCR 출력을 연결한다. 단어 좌표를 0~1000으로 변환해 원본 이미지와 함께 전달한다.
- 임베딩은 차원·벡터 공간·원본, 분류는 라벨 점수·임계값, 재정렬은 순위·점수를 표시한다. 기존 프레임·크롭·원본 비교, 소요 시간, JSON 다운로드를 유지한다.

pHash·ORB·SIFT·TF-IDF/LR·BM25·RRF와 DISK+LightGlue는 HF 모델과 구분한다. pHash는 DC를 뺀 63비트 해밍 거리, ORB/SIFT는 ratio match·별도 RANSAC이다. DISK+LightGlue는 **일반 처리 준비** 버튼으로 Kornia 0.9.0과 공식 가중치를 먼저 준비한다. 매칭 수·RANSAC 통과 수를 일치 확률로 표시하지 않는다.

TF-IDF/LR의 `classifier`는 학습한 `vocabulary`, `idf`, 클래스별 `coef`, `intercept`, `labels` 숫자 JSON이다. lowercase 단어 unigram·raw TF·L2 정규화·다중 클래스 softmax 규격이며 이진 분류도 클래스별 행이 필요하다. 다른 tokenizer·ngram·head는 별도 연결이 필요하다. BM25는 단어 토큰·k1=1.2·b=0.75, RRF는 k=60이다. RRF 입력은 중복 없는 동일 후보 ID가 필요하다.

## API와 확인 범위

로컬 헤더·출처·CSRF 정책은 기존 SSH와 같다.

| 경로 | 용도 |
| --- | --- |
| `GET /api/modeltest/ssh/models` | 등록·계열·로컬 버전 목록 |
| `POST /api/modeltest/ssh/models/inspect` | value·revision의 metadata 조회 |
| `POST /api/modeltest/ssh/models` | 계열·options·training 저장 |
| `PUT /api/modeltest/ssh/models/{id}` | 설정 수정, revision 변경 시 재조회 |
| `DELETE /api/modeltest/ssh/models/{id}` | 등록 삭제 |

노드는 `registered_model`·`model_fingerprint`를 전달하고 서버가 profile을 주입한다. 브라우저의 `model_profile`은 신뢰하지 않는다. `purpose=prepare`는 첨부 없는 준비 단계 하나, `purpose=sample`은 제한된 표본 실행이다. 같은 `/connections/{id}/runs` 접수·조회·중단 경로를 쓴다.

검증은 metadata fixture·가짜 adapter·로컬 자식 프로세스·격리 브라우저 범위다. 실제 Pod 접속, 실제 가중치 다운로드·추론, GPU 메모리·속도·정확도, 패키지 설치 충돌은 미확인이다. 실행 경로 구현과 실제 실행 확인 상태를 구분한다.

공식 규격: [Sentence Transformers 멀티모달·CrossEncoder](https://huggingface.co/blog/multimodal-sentence-transformers), [Qwen3 Reranker](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B), [EmbeddingGemma](https://ai.google.dev/gemma/docs/embeddinggemma/inference-embeddinggemma-with-sentence-transformers), [OpenCLIP factory](https://github.com/mlfoundations/open_clip/blob/v3.3.0/src/open_clip/factory.py), [Transformers LightGlue](https://huggingface.co/docs/transformers/model_doc/lightglue), [LayoutXLM](https://huggingface.co/docs/transformers/model_doc/layoutxlm), [PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6), [YOLO-World](https://docs.ultralytics.com/models/yolo-world/).
