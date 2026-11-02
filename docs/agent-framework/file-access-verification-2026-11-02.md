# 기존 저장소 파일 접근 연결 검증 — 2026-11-02

## 구현 범위

`backend/agent_framework/files.py`의 `VerifiedFileAccess`가 인증된 호출자에 바인딩한 카탈로그와 기존 저장소 리더를 통해 PDF·포스터를 읽는다. 생성자는 파일·DB I/O를 수행하지 않는다.

검증 순서는 요청 계약과 실행 버전 → 현재 접근 권한 → 프로젝트·버전·파일 연결 → 저장 메타데이터 → 실제 바이트의 크기·SHA-256 → PDF/이미지 형식·페이지 수·치수다. 오류가 있으면 모델 콜백을 호출하지 않고 `AdapterFailure`로 반환한다. 원본 바이트는 변경하지 않는다.

| 기존 경로 | 재사용 대상 | 연결 범위 |
|---|---|---|
| `backend/creative_checker` | 기존 `object_access`·프로젝트 권한, `cc_documents`·`cc_asset_versions`·`cc_files`, `LocalStorage`/`S3Storage.read` | 저장된 PDF와 포스터 |
| `backend/app` M008 | 기존 `Authorization.resource`, `asset_versions`·`stored_files`, `LocalStorage.verified` | 포스터 원본 또는 표시 이미지 |

- PDF 버전 식별자는 `cc_documents.id`다. 정수 버전 번호나 파일 ID로 대체하지 않는다.
- M008에는 영속 PDF 파일·문서 버전 연결이 없으므로 PDF 요청은 `FILE_SOURCE_UNSUPPORTED`로 반환한다. 별도 경로의 기록을 합치지 않는다.
- 포스터 원본/표시 이미지의 파일 ID·해시·치수를 구분한다. EXIF 회전, 원본 치수, 좌표 기준을 `VerifiedPoster`에 전달한다.
- `FileBackedExtractor`와 `FileBackedInspector`가 검증된 바이트를 담당자의 PDF 추출·포스터 검사 콜백에 전달한다. 실제 모델 구현은 포함하지 않는다.
- 로그인은 기존 제품 경계의 책임이다. 인증되지 않은 `Context.actor_id`만으로 권한을 부여하지 않는다.

## 실행 결과

실행 위치는 프로젝트 루트이며 macOS의 기존 `.venv`를 사용했다.

```bash
.venv/bin/python -m pytest tests/agent_framework backend/tests/test_c09_contracts_storage.py -q
```

결과: **406 passed, 9 subtests passed**. 기존 pytest-asyncio 설정 관련 경고 2개와 PyMuPDF SWIG 폐기 예정 경고 5개가 발생했으며 실패는 없었다.

신규 파일 검사는 총 85개다.

- `test_files.py`: 62개. 실제 PDF·PNG·JPEG·WebP, 암호화 PDF, 잘못된 형식·해시·버전·길이·치수, EXIF, 크기 제한, 메타데이터 변조, 콜백 차단을 확인했다.
- `test_project_files.py`: 23개. 기존 모델과 격리 SQLite·LocalStorage 연계, 권한·계정·프로젝트·버전·파일 매핑, M008 저장소 읽기와 오류 변환을 확인했다. M008 권한 객체 호출 검사는 대역을 사용했다.

```bash
.venv/bin/python -m examples.agent_framework.file_access_demo
```

생성한 1페이지 PDF와 32×24 PNG를 기존 `services.store_file`로 임시 저장하고 새 연결부로 읽었다. 읽은 바이트가 입력과 같고 해시·페이지 수·치수가 일치했으며 변경한 해시 참조는 `FILE_HASH_MISMATCH`로 거부됐다. 모델 호출은 0회였다. 예제는 격리 SQLite와 자동 정리되는 임시 저장소만 사용한다.

추가 확인:

- 패키지 허용 목록으로 만든 임시 독립 복사본에서 파일 예제 성공 및 프레임워크 **338 passed, 9 subtests passed**. 동일한 프로젝트 가상환경을 사용했으며 새 가상환경 설치 검증은 아니다.
- `uv.lock` 오프라인 일관성 검사 통과. 기존 M008 저장소에 필요한 `portalocker==3.2.0`, `rfc8785==0.1.4`를 프로젝트 의존성에 추가했고 기존 잠금 버전은 변경하지 않았다.
- `git diff --check` 통과.
- 루트 프레임워크 안내·흐름도·소스 뷰어·코드 그래프를 재생성했다. 브라우저 렌더링은 확인하지 않았다.
- 공유 폴더의 기존 `index.html` SHA-256은 `ac071cbb1c59611b026af89cbe5f532730421702ea48a8097da8a5fceba687ef`로 유지됐다. 기존 공유 ZIP은 이번 작업에서 갱신하지 않았다.

## 검증 경계

운영 PostgreSQL·실제 S3·사용자 업로드 파일·제품 로그인 세션으로 실행한 결과는 아니다. Windows 실제 실행과 원격 CI도 확인하지 않았다. M008 저장소의 POSIX 전용 제약은 기존 구현 그대로이며 해당 저장소 테스트는 Windows에서 건너뛴다.

제품 API·영속 worker·front-v2에 자동 연결하지 않았으며 Qwen 추론이나 Neo4j 검색을 실행하지 않았다. 기존 제품 소스·운영 데이터·업로드·`.env`를 변경하지 않았고 커밋·푸시는 하지 않았다.

연결 코드는 [개발 안내의 파일 접근 절](README.md#기존-저장소의-pdf포스터-읽기)을 참고한다.
