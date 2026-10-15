# OCI와 Cloudflare Pages 시연 배포

Issue #16의 시연 구성이다. 화면은 `https://review.rayd.cc`, API 중계 대상은
`https://review-api.rayd.cc`를 사용한다. OCI에는 ARM64 CPU용 API와 전용 Tunnel을
배치하며, 기존 Nginx 설정과 다른 앱의 Tunnel은 수정하지 않는다.

## OCI 서버

저장소 루트에서 실행한다. Docker와 Compose가 필요하며, 최초 빌드에서 공식
Paddle CPU 패키지와 고정 revision의 OCR 모델을 다운로드한다.

```sh
cp deploy/oci/.env.example deploy/oci/.env
# .env에 시연 계정 비밀번호, 중계 비밀키, 전용 Tunnel 토큰을 입력한다.
chmod 600 deploy/oci/.env
docker compose --env-file deploy/oci/.env -f deploy/oci/compose.yaml up -d --build
```

Cloudflare Tunnel의 공개 호스트 `review-api.rayd.cc`를 `http://api:8000`으로
연결한다. API 포트는 호스트에 공개하지 않는다. Pages 중계 비밀키와 로그인
세션을 모두 확인하며, 업로드에는 CSRF 토큰도 필요하다.

API는 CPU 2개와 RAM 10GiB로 제한하고 OCR은 순차 실행한다. Ampere에서 확인한
Paddle 3.3.1의 다중 스레드 추론 충돌을 피하도록 `PADDLE_PDX_CPU_NUM_THREADS`,
`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`를 모두 1로 지정한다. 모델은 이미지에
포함하며, 검수 파일은 `baro-review_reviews` 볼륨에 저장한다. 재배포할 때 이
볼륨을 삭제하지 않는다. 메모리 세션은 컨테이너 재시작 시 만료된다. 실행 중
중단된 검수는 실패로 정리되며 자동 재개하지 않는다.

로그인용 DB는 사용하지 않는다. 시연 계정은 환경변수로 설정하고, 로그인한
계정과 만료 시각은 서버 메모리 세션에 저장한다. 필요하면 `.env`에서
`DEMO_ALLOW_LOCAL_ADMIN=1`로 지정해 기존 개발 계정도 허용한다. 이 설정의
기본값은 0이며, 알려진 개발 비밀번호로 공유 검수 자료에 접근할 수 있으므로
시연 환경에서만 사용한다. 두 계정 모두 같은 검수 자료에 접근한다.

## Cloudflare Pages

프로젝트는 `skn33-review-rayd`, 루트는 `frontend`를 사용한다. API 중계 Functions는
`frontend/functions/api/[[path]].js`에 있다. 다음 두 값을 Pages의 서버 환경에 둔다.

| 이름 | 값 |
|---|---|
| `API_ORIGIN` | `https://review-api.rayd.cc` |
| `API_PROXY_SECRET` | OCI의 값과 동일한 비밀키 |

```sh
cd frontend
npm ci
npm run build
npx wrangler@4.151.0 pages deploy dist --project-name skn33-review-rayd --branch feat/16-oci-pages-demo
```

Pages 프로젝트의 production branch도 `feat/16-oci-pages-demo`로 지정한다.
배포 후 `review.rayd.cc`를 프로젝트의 Custom domain으로 연결한다. 브라우저에는
중계 비밀키를 전달하지 않으며, API 응답은 캐시하지 않는다. 다른 주소에서
시연하려면 OCI의 `PUBLIC_ORIGIN`을 해당 HTTPS 출처로 변경한다.

## 배포 확인

로그인·로그아웃, 비로그인 검수 요청 거부, 잘못된 CSRF 거부, 샘플 OCR 완료와
결과·근거 표시를 실제 도메인에서 확인한다. 배포·확인 결과는 실행한 내용만
기록한다. 시연 계정은 공유 계정이며 사용자별 검수 소유권을 구분하지 않는다.
