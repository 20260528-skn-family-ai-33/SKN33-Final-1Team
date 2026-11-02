# 팀 공유 묶음 검증 · 2026-11-02

이 기록은 공유 문서·패키지의 검증이며 실제 모델·DB·제품 통합 검증이 아니다.

- 기존 환경의 Python 3.12로 ZIP을 한글·공백이 포함된 별도 임시 폴더에 압축 해제했다.
- 압축 해제한 소스 기준 `tests/agent_framework`: **108 passed, 9 subtests passed**. 기존 환경에 pytest-asyncio가 없어 관련 설정 경고 2개가 발생했다.
- 같은 폴더에서 `python -m backend.agent_framework demo`가 종료 코드 0으로 합성 결과 JSON을 반환했다. 실제 DB·모델을 실행하지 않았다.
- 업무 흐름 18개 노드·27개 연결, 48개 코드 근거 참조와 17개 내장 소스의 파일·행 번호·해시를 대조했다.
- HTML의 JavaScript 7개를 `node --check`로 검사했다. 링크는 상대 경로이며 자동으로 불러오는 외부 자원은 없다. 기술 참고용 외부 링크 2개는 별도다.
- Windows 금지 문자·예약 파일명·대소문자 충돌, 묶음 밖 링크, ZIP CRC, MANIFEST.json의 파일별 SHA-256을 확인했다.
- `.env`, `.git`, 가상환경, 캐시, 모델 가중치, 업로드·DB·로그는 공유 묶음에 포함하지 않았다.
- Git용 `.gitattributes`로 내장 소스 대상의 LF 줄바꿈을 고정했다. 기존 추적 파일에서도 Windows 금지 문자가 포함된 파일명은 발견되지 않았다.

**미검증:** 실제 Windows 기기의 설치·명령 실행, Chrome의 화면 렌더링·클릭 동작, 새 환경에서의 uv 의존성 설치. 현재 Mac의 기존 가상환경을 사용했으므로 새 설치 검증으로 확대하지 않는다.

원본 문서와 코드 변경 후 `python -m scripts.package_agent_framework`로 HTML·ZIP·해시를 함께 재생성한다. 빌드마다 정적 검사 결과와 파일 목록은 ZIP의 MANIFEST.json에 기록된다.
