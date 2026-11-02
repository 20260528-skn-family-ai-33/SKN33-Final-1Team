# 실제 연동 검증 기록 — 2026-10-30

Issue #29 / `feat/29-rebuild-workflow`. 이 기록은 전체 기능의 정확도 평가가 아니라 실행 경로와 복구 동작을 확인한 범위를 설명한다.

## 보존과 구형 파일 정리

원래 `SKN33-Final-1Team` 작업 폴더는 수정하지 않았다. 변경 전 추적 파일 diff의 SHA256은 `6618D041D54A492970F7426EF718683737CA2BAE085CF6FE4DBB7D22DAB3A530`이다. 기존 미커밋 코드와 연구 자료는 원래 경로에 남아 있다.

새 worktree에서 구형 앱·마이그레이션·테스트·프런트·실행/설치 스크립트·환경 예제 308개를 정리 대상으로 식별했다. 원본 기준 커밋 `a938f6c791a7238621ebdec68d15ae414065e295` 전체를 `artifacts/rebuild/legacy-main-a938f6c.zip`에 보존했다. 1,125개 ZIP 항목의 CRC를 확인했으며 SHA256은 `E24FFB02AD4F0E24CD4E9263C8B03EE3BB270F76543A21E62A36F1C74FD3E4CD`다. 목록은 `.runtime/continuation-20261030/legacy-files.txt`에 있다.

**삭제는 미실행이다.** 새 worktree 안의 일반 파일만 대상으로 경로·수정 상태·백업을 확인했지만 자동 승인 검토가 삭제 명령을 `blocked by policy`로 거부했다. 다른 도구로 같은 삭제를 우회하지 않았다. 구형 코드의 미사용 여부와 삭제 완료 여부는 구분해야 한다.

## Docker 복구와 Linux 실행

Docker Desktop 4.91.0의 기동 실패 지점은 Windows 런타임 소켓의 rename 오류였다. 모든 Docker 프로세스가 종료된 상태에서 0바이트 런타임 소켓만 있는 디렉터리를 확인하고 부모 폴더를 보존용 이름으로 변경했다. 기존 이미지·컨테이너·볼륨·WSL 배포판을 초기화하지 않았다.

- `%LOCALAPPDATA%/Docker/run` → `run.preserved-20261030-issue29` 및 재시도 시 `run.preserved-20261030-issue29-attempt2`.
- `%LOCALAPPDATA%/docker-secrets-engine` → `docker-secrets-engine.preserved-20261030-issue29`.
- 새 기동에서 Engine 29.8.0 응답과 전용 Compose 서비스를 확인했다.

이는 같은 증상의 [Docker 저장소 이슈 #460](https://github.com/docker/desktop-feedback/issues/460)에 보고된 부모 폴더 보존 방법을 참고한 로컬 복구다. Docker 자체 결함을 수정하거나 재발하지 않음을 증명한 것은 아니다.

Windows 예약 포트 범위에 55429가 포함되어 PostgreSQL 바인딩이 실패했다. 전역 예약을 바꾸지 않고 프로젝트 기본 포트를 25429로 옮겼다. Neo4j는 Bolt 57629를 사용한다. `.env.rebuild`에는 작업 전용 무작위 자격증명을 저장했고 Git에서 제외했다.

존재하지 않는 uv 파생 이미지 태그는 Python 3.12 공식 이미지에 고정 uv 바이너리를 복사하는 구성으로 고쳤다. [uv 공식 Docker 안내](https://docs.astral.sh/uv/guides/integration/docker/)의 설치 방식이다. `creative-checker:rebuild-29` 빌드가 성공했고, UID 10001·읽기 전용 루트·권한 상승 금지 환경에서 실제 PostgreSQL API ready 200과 Neo4j 연결을 확인했다. 로그는 `artifacts/rebuild/docker-build.log`다.

## PostgreSQL·Neo4j·복구 검증

전용 PostgreSQL DB `checker_rebuild` 안에서 매번 다른 `cc_it_*` schema와 프로젝트 UUID를 생성했다. 검사가 끝난 뒤 해당 schema와 프로젝트의 그래프만 정리했다. 기존 업무 데이터는 조회·삭제하지 않았다.

첫 검사에서 두 종류의 결함을 발견했다.

- 승인 대기 checkpoint 저장 후 worker가 중단되면 재획득 시 Document/Run을 `running`으로 덮어써 승인이 막혔다. 이미 저장된 대기 상태를 보존하도록 수정했다.
- 오래된 lease의 graph가 첫 도구 호출을 한 뒤에야 권한을 검사했다. 호출 전 확인을 추가하고 MCP의 변경 도구에도 현재 lease token을 전달·검사하도록 수정했다.

수정 후 **실제 서비스 통합 6개 모두 통과**했다. API의 문서·조건 승인·검사·수정 요청·새 버전 재검수·최종 승인, 원본 파일 보존, 중복 요청, 동시 claim 배타성, 이전 lease의 완료 차단, checkpoint 이후 재시작, outbox의 실제 Neo4j 반영·영향 조회·재구성을 검사했다. MCP JSON-RPC 경계에서도 인증과 누락/오래된 lease 거절, 거절 시 receipt 미생성, 유효 lease의 성공·재호출을 확인했다.

이 통합 검사에서 **Qwen 응답은 synthetic**이다. 실제 모델 연결의 증거로 사용하지 않는다. 로컬 계약 검사도 수정 후 86개 통과했다. 실제 모델을 호출하는 별도 스크립트는 `python -m scripts.rebuild.verify_live_workflow --output <새 경로>`다. 이 스크립트는 가상 자료, 실제 PostgreSQL·Neo4j·MCP 서버, 별도 worker CLI 프로세스와 실제 Qwen을 사용하며 모델 출력이나 결과를 주입하지 않는다.

## 실제 모델

기존 Pod의 공식 이름 캐시에서 Qwen3.5-4B revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`의 두 가중치 파일을 찾았다. [Qwen 모델 안내](https://huggingface.co/Qwen/Qwen3.5-4B)의 vLLM 서빙 방식을 사용했다. 기존 환경은 읽기 전용으로 참조하고 추가 패키지를 격리한 뒤, vLLM 0.17.1·PyTorch 2.10.0+cu128에서 해당 revision을 실제 GPU에 올렸다. 원격 HTTP는 loopback에만 바인딩했고 기존 SSH 연결로만 접근했다.

설치·초기 실행에서 확인한 사항은 다음과 같다.

- `/workspace`의 FUSE 오브젝트 스토리지가 패키지 chmod를 지원하지 않아 최초 설치가 실패했다. 별도 native 디렉터리의 추가 패키지와 기존 CUDA 패키지를 함께 사용하는 환경으로 설치했다. 기존 venv를 변경하지 않았다.
- PyTorch 배포 메타데이터는 `2.10.0`, 실제 실행 버전은 `2.10.0+cu128`이었다. 실행 버전과 native 모듈 import를 별도로 확인했다.
- 서버 cwd가 오브젝트 스토리지에 있을 때 작은 동일 컴파일이 10.253초, native cwd에서는 0.032초 걸렸다. 서버 cwd와 임시·커널 캐시 경로를 native 디스크로 지정했다. 일반 성능 벤치마크 수치는 아니다.

실제 업무 검증 첫 시도는 14:32:21~14:38:33 KST에 진행했다. PostgreSQL·별도 MCP·worker CLI를 통과한 문서 추론 요청이 초기 준비 지연으로 `qwen_network_failure`에 도달했다. 이를 성공으로 바꾸지 않았으며 `artifacts/rebuild/live-workflow-01/report.json`에 실패 기록과 격리 schema를 보존했다. 포스터·영상 단계에는 도달하지 못했다.

native cwd로 재기동한 뒤 일반 Chat Completions의 단문 요청은 HTTP 200, `OK` 응답을 반환했다. 해당 요청은 56.94초, 입력 18·출력 2토큰이며 `artifacts/rebuild/live-model/plain-probe-native.json`에 원응답을 보존했다. **이 20토큰은 단문 요청 하나의 사용량이다.** 실패·중단된 요청까지 포함한 전체 추론 토큰이나 Pod 청구 비용을 뜻하지 않는다. 구조화 조건 추출·멀티모달 검사·정확도 검증 완료를 증명하지도 않는다.

이후 사용자가 다른 작업에서 모든 Pod를 사용 중이라며 연결 검증을 나중에 별도로 진행하도록 요청했다. 이번에 생성한 서버 PID 98594의 stopped 상태와 로컬 SSH 터널 종료를 확인하고 `.env.rebuild`의 임시 모델 주소를 비웠다. 다른 Pod·기존 서비스는 중지하지 않았다. `user-deferred-final-status.json`과 `user-deferral.json`에 남겼으며 추가 Pod 검증은 진행하지 않는다.

## Pod 디스크 정리 보류

Container Disk 정리 요청에 따라 용량만 확인한 뒤 사용자의 중단 요청을 적용했다. **Pod 디스크 파일은 0개 삭제했다.** 모델 캐시·기존 venv·연구 자료는 보존했다. 확인 당시 `/` 기준 50GiB 중 약 46.59GiB 사용, 3.41GiB 여유였으며 대시보드 표시와 동일한 시점·산정 방식인지는 확인하지 않았다.

이번 작업의 `/tmp/creative-checker-rebuild-29`는 약 2.70GiB였다. 실행 환경이 참조하는 설치 캐시를 포함하므로 이를 임의 삭제하지 않았다. 정리 재개 시 새 검증 런타임의 참조 관계와 실행 여부부터 다시 확인해야 한다. 임시 서빙 제어 코드와 진단 자료는 로컬 `artifacts/rebuild/live-model/control`에 보관하며 제품 코드로 편입하지 않았다.
