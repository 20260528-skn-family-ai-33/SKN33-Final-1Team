개발·시연용 가상 광고 입력 자료

festival-v1: 가상 노을 사운드 페스티벌 포스터 6장.
croissant-v2: 가상 BUTTER STUDIO 광고 6장. 최신 크기 혼합 버전.
기존 data/samples의 딸기라떼 6장과 합쳐 현재 실행 샘플은 18장.
모든 이미지의 실제 메타데이터: 1254 × 1254 PNG.

기준은 각 폴더 criteria.json에서 읽는다. croissant-v2의 C04 기대 수량은
sample_conditions에 사전 등록된 발주 조건이다. 관찰한 실제 수량이나
정답이 아니다. 이미지 접수 파일에는 선택한 주제·발주 ID를 저장한다.
객체 개수·크기·외관 자동 검사는 아직 구현하지 않았다.

reviewer 정답키, 생성 프롬프트, 독립 검토 결과는 복사하지 않았다.
이 자료는 학습/검증 분리나 일반 성능 평가를 보장하는 데이터셋이 아니다.
이전 크루아상 v1은 원본 보관 위치에 유지하고 앱 샘플에서 제외했다.

원본 위치: 모닝컵_가상납품검수_예제/output/
festival-qa-v1/inputs/ 및 croissant-qa-v2/inputs/
입력 이미지는 변경 없이 복사했으며, 기준에는 OCR 영역·판독 형식을 추가했다.

[추가] 가상 발주 3건(data/virtual-orders): fnb-ongi-v1 · festival-dalbit-v1 · virtual-seoha-v1
각 6장(sample-01~06 = 납품 이미지 D1~D6의 바이트 동일 복사본), 규격 1080 × 1350 PNG.
기준은 data/virtual-orders/scripts/make_topics.py가 생성한다. 문구(OCR) 규칙만 자동 검사하며 나머지는 manual이다.
