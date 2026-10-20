-- 2026-10-16: 이메일 로그인용 비밀번호 해시·사용자세션 설계 반영.
-- 관계도 가져오기용 논리 설계이며 운영 DB 적용 이력을 뜻하지 않습니다.

CREATE TABLE `사용자` (
	`사용자식별자`	UUID	NOT NULL	COMMENT '사용자식별자',
	`이메일`	VARCHAR(254)	NOT NULL	COMMENT '이메일',
	`비밀번호해시`	TEXT	NOT NULL	COMMENT '비밀번호해시',
	`표시이름`	VARCHAR(100)	NOT NULL	COMMENT '표시이름',
	`활성여부`	BOOLEAN	NOT NULL DEFAULT TRUE	COMMENT '활성여부',
	`등록시각`	TIMESTAMPTZ	NOT NULL DEFAULT CURRENT_TIMESTAMP	COMMENT '등록시각',
	`수정시각`	TIMESTAMPTZ	NOT NULL DEFAULT CURRENT_TIMESTAMP	COMMENT '수정시각'
);

CREATE TABLE `사용자세션` (
	`세션식별자`	UUID	NOT NULL	COMMENT '세션식별자',
	`사용자식별자`	UUID	NOT NULL	COMMENT '사용자식별자',
	`세션토큰해시`	VARCHAR(64)	NOT NULL	COMMENT '세션토큰해시',
	`발급시각`	TIMESTAMPTZ	NOT NULL DEFAULT CURRENT_TIMESTAMP	COMMENT '발급시각',
	`만료시각`	TIMESTAMPTZ	NOT NULL	COMMENT '만료시각',
	`폐기시각`	TIMESTAMPTZ	NULL	COMMENT '폐기시각'
);

CREATE TABLE `프로젝트` (
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`생성자식별자`	UUID	NOT NULL	COMMENT '생성자식별자',
	`프로젝트명`	VARCHAR(200)	NOT NULL	COMMENT '프로젝트명',
	`발주목적`	TEXT	NULL	COMMENT '발주목적',
	`예정사용처`	TEXT	NULL	COMMENT '예정사용처',
	`납기시각`	TIMESTAMP	NULL	COMMENT '납기시각',
	`업무상태`	VARCHAR(40)	NOT NULL	COMMENT '업무상태',
	`등록시각`	TIMESTAMP	NOT NULL	COMMENT '등록시각'
);

CREATE TABLE `프로젝트참여자` (
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`사용자식별자`	UUID	NOT NULL	COMMENT '사용자식별자',
	`참여역할`	VARCHAR(40)	NOT NULL	COMMENT '참여역할',
	`활성여부`	BOOLEAN	NOT NULL	COMMENT '활성여부',
	`참여시각`	TIMESTAMP	NOT NULL	COMMENT '참여시각'
);

CREATE TABLE `자료` (
	`자료식별자`	UUID	NOT NULL	COMMENT '자료식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`상위자료식별자`	UUID	NULL	COMMENT '상위자료식별자',
	`자료종류`	VARCHAR(50)	NOT NULL	COMMENT '자료종류',
	`자료명`	VARCHAR(200)	NOT NULL	COMMENT '자료명'
);

CREATE TABLE `자료버전` (
	`자료버전식별자`	UUID	NOT NULL	COMMENT '자료버전식별자',
	`자료식별자`	UUID	NOT NULL	COMMENT '자료식별자',
	`버전번호`	INTEGER	NOT NULL	COMMENT '버전번호',
	`원본파일식별자`	UUID	NULL	COMMENT '원본파일식별자',
	`표시파일식별자`	UUID	NULL	COMMENT '표시파일식별자',
	`원본파일해시`	VARCHAR(64)	NOT NULL	COMMENT '원본파일해시',
	`원본가로크기`	INTEGER	NULL	COMMENT '원본가로크기',
	`원본세로크기`	INTEGER	NULL	COMMENT '원본세로크기',
	`표시가로크기`	INTEGER	NULL	COMMENT '표시가로크기',
	`표시세로크기`	INTEGER	NULL	COMMENT '표시세로크기',
	`원본확인상태`	VARCHAR(40)	NOT NULL	COMMENT '원본확인상태',
	`파일측정내용`	TEXT	NULL	COMMENT '파일측정내용',
	`등록시각`	TIMESTAMP	NOT NULL	COMMENT '등록시각'
);

CREATE TABLE `저장파일` (
	`파일식별자`	UUID	NOT NULL	COMMENT '파일식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`원천파일식별자`	UUID	NULL	COMMENT '원천파일식별자',
	`인계묶음식별자`	UUID	NULL	COMMENT '인계묶음식별자',
	`파일역할`	VARCHAR(50)	NOT NULL	COMMENT '파일역할',
	`원본파일명`	VARCHAR(255)	NOT NULL	COMMENT '원본파일명',
	`확인된파일형식`	VARCHAR(100)	NULL	COMMENT '확인된파일형식',
	`파일크기`	BIGINT	NULL	COMMENT '파일크기',
	`파일해시`	VARCHAR(64)	NULL	COMMENT '파일해시',
	`저장상태`	VARCHAR(40)	NOT NULL	COMMENT '저장상태',
	`출처내용`	TEXT	NULL	COMMENT '출처내용',
	`등록시각`	TIMESTAMP	NOT NULL	COMMENT '등록시각'
);

CREATE TABLE `이미지좌표계` (
	`좌표계식별자`	UUID	NOT NULL	COMMENT '좌표계식별자',
	`자료버전식별자`	UUID	NOT NULL	COMMENT '자료버전식별자',
	`기준파일식별자`	UUID	NULL	COMMENT '기준파일식별자',
	`상위좌표계식별자`	UUID	NULL	COMMENT '상위좌표계식별자',
	`좌표기준종류`	VARCHAR(50)	NOT NULL	COMMENT '좌표기준종류',
	`기준가로크기`	INTEGER	NOT NULL	COMMENT '기준가로크기',
	`기준세로크기`	INTEGER	NOT NULL	COMMENT '기준세로크기',
	`확인상태`	VARCHAR(40)	NOT NULL	COMMENT '확인상태',
	`좌표변환내용`	TEXT	NULL	COMMENT '좌표변환내용'
);

CREATE TABLE `외부식별별칭` (
	`별칭식별자`	UUID	NOT NULL	COMMENT '별칭식별자',
	`자료버전식별자`	UUID	NOT NULL	COMMENT '자료버전식별자',
	`인계묶음식별자`	UUID	NULL	COMMENT '인계묶음식별자',
	`식별출처`	VARCHAR(100)	NOT NULL	COMMENT '식별출처',
	`외부식별값`	VARCHAR(200)	NOT NULL	COMMENT '외부식별값'
);

CREATE TABLE `데이터규격` (
	`규격식별자`	UUID	NOT NULL	COMMENT '규격식별자',
	`규격명`	VARCHAR(150)	NOT NULL	COMMENT '규격명',
	`규격버전`	VARCHAR(40)	NOT NULL	COMMENT '규격버전',
	`설명파일식별자`	UUID	NULL	COMMENT '설명파일식별자',
	`검증규격파일식별자`	UUID	NULL	COMMENT '검증규격파일식별자',
	`확보상태`	VARCHAR(40)	NOT NULL	COMMENT '확보상태',
	`항목목록`	TEXT	NULL	COMMENT '항목목록'
);

CREATE TABLE `인계묶음` (
	`인계묶음식별자`	UUID	NOT NULL	COMMENT '인계묶음식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`목록파일식별자`	UUID	NULL	COMMENT '목록파일식별자',
	`이전인계식별자`	UUID	NULL	COMMENT '이전인계식별자',
	`인계출처`	VARCHAR(100)	NOT NULL	COMMENT '인계출처',
	`외부기록식별값`	VARCHAR(200)	NULL	COMMENT '외부기록식별값',
	`묶음내용해시`	VARCHAR(64)	NOT NULL	COMMENT '묶음내용해시',
	`해시확인상태`	VARCHAR(40)	NOT NULL	COMMENT '해시확인상태',
	`수입상태`	VARCHAR(40)	NOT NULL	COMMENT '수입상태',
	`보고된검증내용`	TEXT	NULL	COMMENT '보고된검증내용',
	`접수시각`	TIMESTAMP	NOT NULL	COMMENT '접수시각'
);

CREATE TABLE `감사이력` (
	`감사이력식별자`	UUID	NOT NULL	COMMENT '감사이력식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`행위자식별자`	UUID	NULL	COMMENT '행위자식별자',
	`행위종류`	VARCHAR(100)	NOT NULL	COMMENT '행위종류',
	`대상종류`	VARCHAR(100)	NOT NULL	COMMENT '대상종류',
	`대상식별값`	UUID	NULL	COMMENT '대상식별값',
	`행위내용`	TEXT	NULL	COMMENT '행위내용',
	`발생시각`	TIMESTAMP	NOT NULL	COMMENT '발생시각'
);

CREATE TABLE `문자인식실행` (
	`문자인식실행식별자`	UUID	NOT NULL	COMMENT '문자인식실행식별자',
	`자료버전식별자`	UUID	NOT NULL	COMMENT '자료버전식별자',
	`처리시도식별자`	UUID	NULL	COMMENT '처리시도식별자',
	`인계묶음식별자`	UUID	NULL	COMMENT '인계묶음식별자',
	`규격식별자`	UUID	NOT NULL	COMMENT '규격식별자',
	`결과파일식별자`	UUID	NOT NULL	COMMENT '결과파일식별자',
	`좌표계식별자`	UUID	NOT NULL	COMMENT '좌표계식별자',
	`실행상태`	VARCHAR(40)	NOT NULL	COMMENT '실행상태',
	`인식엔진명`	VARCHAR(100)	NULL	COMMENT '인식엔진명',
	`엔진버전`	VARCHAR(100)	NULL	COMMENT '엔진버전',
	`인식모델명`	VARCHAR(200)	NULL	COMMENT '인식모델명',
	`실행장치`	VARCHAR(100)	NULL	COMMENT '실행장치',
	`보고된좌표기준`	VARCHAR(100)	NOT NULL	COMMENT '보고된좌표기준',
	`실행설정내용`	TEXT	NULL	COMMENT '실행설정내용',
	`시작시각`	TIMESTAMP	NULL	COMMENT '시작시각',
	`완료시각`	TIMESTAMP	NULL	COMMENT '완료시각'
);

CREATE TABLE `인식문구` (
	`인식문구식별자`	UUID	NOT NULL	COMMENT '인식문구식별자',
	`문자인식실행식별자`	UUID	NOT NULL	COMMENT '문자인식실행식별자',
	`외부문구식별값`	VARCHAR(150)	NOT NULL	COMMENT '외부문구식별값',
	`문구순서`	INTEGER	NOT NULL	COMMENT '문구순서',
	`인식원문`	TEXT	NOT NULL	COMMENT '인식원문',
	`인식신뢰도`	NUMERIC(8,6)	NULL	COMMENT '인식신뢰도',
	`사각영역좌표`	TEXT	NULL	COMMENT '사각영역좌표',
	`다각형영역좌표`	TEXT	NULL	COMMENT '다각형영역좌표'
);

CREATE TABLE `포스터추출` (
	`추출식별자`	UUID	NOT NULL	COMMENT '추출식별자',
	`자료버전식별자`	UUID	NOT NULL	COMMENT '자료버전식별자',
	`규격식별자`	UUID	NOT NULL	COMMENT '규격식별자',
	`문자인식실행식별자`	UUID	NULL	COMMENT '문자인식실행식별자',
	`이전추출식별자`	UUID	NULL	COMMENT '이전추출식별자',
	`원문파일식별자`	UUID	NULL	COMMENT '원문파일식별자',
	`추출버전번호`	INTEGER	NOT NULL	COMMENT '추출버전번호',
	`추출방식`	VARCHAR(50)	NOT NULL	COMMENT '추출방식',
	`추출기버전`	VARCHAR(100)	NULL	COMMENT '추출기버전',
	`전체추출내용`	TEXT	NOT NULL	COMMENT '전체추출내용',
	`행사대표제목`	VARCHAR(255)	NULL	COMMENT '행사대표제목',
	`개최연도`	INTEGER	NULL	COMMENT '개최연도',
	`시작연도`	INTEGER	NULL	COMMENT '시작연도',
	`시작월`	INTEGER	NULL	COMMENT '시작월',
	`시작일자`	INTEGER	NULL	COMMENT '시작일자',
	`종료연도`	INTEGER	NULL	COMMENT '종료연도',
	`종료월`	INTEGER	NULL	COMMENT '종료월',
	`종료일자`	INTEGER	NULL	COMMENT '종료일자',
	`완전한시작날짜`	DATE	NULL	COMMENT '완전한시작날짜',
	`완전한종료날짜`	DATE	NULL	COMMENT '완전한종료날짜',
	`호환상태`	VARCHAR(40)	NOT NULL	COMMENT '호환상태',
	`구조검증상태`	VARCHAR(40)	NOT NULL	COMMENT '구조검증상태',
	`확인필요내용`	TEXT	NULL	COMMENT '확인필요내용'
);

CREATE TABLE `포스터항목관찰` (
	`관찰식별자`	UUID	NOT NULL	COMMENT '관찰식별자',
	`추출식별자`	UUID	NOT NULL	COMMENT '추출식별자',
	`항목코드`	VARCHAR(100)	NOT NULL	COMMENT '항목코드',
	`항목경로`	VARCHAR(255)	NOT NULL	COMMENT '항목경로',
	`표기판독상태`	VARCHAR(40)	NOT NULL	COMMENT '표기판독상태',
	`해결상태`	VARCHAR(40)	NOT NULL	COMMENT '해결상태',
	`인쇄원문`	TEXT	NULL	COMMENT '인쇄원문',
	`정규화내용`	TEXT	NULL	COMMENT '정규화내용',
	`미확인사유`	TEXT	NULL	COMMENT '미확인사유'
);

CREATE TABLE `근거` (
	`근거식별자`	UUID	NOT NULL	COMMENT '근거식별자',
	`관찰식별자`	UUID	NULL	COMMENT '관찰식별자',
	`검수항목결과식별자`	UUID	NULL	COMMENT '검수항목결과식별자',
	`외부항목비교결과식별자`	UUID	NULL	COMMENT '외부항목비교결과식별자',
	`사람정정식별자`	UUID	NULL	COMMENT '사람정정식별자',
	`자료버전식별자`	UUID	NULL	COMMENT '자료버전식별자',
	`출처파일식별자`	UUID	NULL	COMMENT '출처파일식별자',
	`좌표계식별자`	UUID	NULL	COMMENT '좌표계식별자',
	`문자인식실행식별자`	UUID	NULL	COMMENT '문자인식실행식별자',
	`외부행사기준식별자`	UUID	NULL	COMMENT '외부행사기준식별자',
	`근거종류`	VARCHAR(40)	NOT NULL	COMMENT '근거종류',
	`상세항목경로`	VARCHAR(255)	NULL	COMMENT '상세항목경로',
	`문서쪽번호`	INTEGER	NULL	COMMENT '문서쪽번호',
	`근거원문`	TEXT	NULL	COMMENT '근거원문',
	`영역좌표`	TEXT	NULL	COMMENT '영역좌표',
	`확인방법`	VARCHAR(100)	NOT NULL	COMMENT '확인방법',
	`검사한계내용`	TEXT	NULL	COMMENT '검사한계내용'
);

CREATE TABLE `근거문구연결` (
	`근거식별자`	UUID	NOT NULL	COMMENT '근거식별자',
	`인식문구식별자`	UUID	NOT NULL	COMMENT '인식문구식별자',
	`문구연결순서`	INTEGER	NOT NULL	COMMENT '문구연결순서'
);

CREATE TABLE `검수기준묶음` (
	`기준묶음식별자`	UUID	NOT NULL	COMMENT '기준묶음식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`기준묶음명`	VARCHAR(200)	NOT NULL	COMMENT '기준묶음명'
);

CREATE TABLE `검수기준초안` (
	`기준초안식별자`	UUID	NOT NULL	COMMENT '기준초안식별자',
	`기준묶음식별자`	UUID	NOT NULL	COMMENT '기준묶음식별자',
	`대상자료버전식별자`	UUID	NOT NULL	COMMENT '대상자료버전식별자',
	`초안수정번호`	INTEGER	NOT NULL	COMMENT '초안수정번호',
	`요구사항원문`	TEXT	NOT NULL	COMMENT '요구사항원문',
	`편집항목내용`	TEXT	NOT NULL	COMMENT '편집항목내용',
	`초안상태`	VARCHAR(40)	NOT NULL	COMMENT '초안상태',
	`수정자식별자`	UUID	NOT NULL	COMMENT '수정자식별자',
	`수정시각`	TIMESTAMP	NOT NULL	COMMENT '수정시각'
);

CREATE TABLE `확정검수기준` (
	`확정기준식별자`	UUID	NOT NULL	COMMENT '확정기준식별자',
	`기준묶음식별자`	UUID	NOT NULL	COMMENT '기준묶음식별자',
	`대상자료버전식별자`	UUID	NOT NULL	COMMENT '대상자료버전식별자',
	`원천초안식별자`	UUID	NULL	COMMENT '원천초안식별자',
	`복사원본기준식별자`	UUID	NULL	COMMENT '복사원본기준식별자',
	`인계묶음식별자`	UUID	NULL	COMMENT '인계묶음식별자',
	`기준버전번호`	INTEGER	NOT NULL	COMMENT '기준버전번호',
	`원천초안수정번호`	INTEGER	NULL	COMMENT '원천초안수정번호',
	`확정당시요구원문`	TEXT	NULL	COMMENT '확정당시요구원문',
	`확정자식별자`	UUID	NULL	COMMENT '확정자식별자',
	`확정시각`	TIMESTAMP	NULL	COMMENT '확정시각',
	`생성경로`	VARCHAR(40)	NOT NULL	COMMENT '생성경로',
	`원천확정상태`	VARCHAR(40)	NOT NULL	COMMENT '원천확정상태'
);

CREATE TABLE `검수기준항목` (
	`기준항목식별자`	UUID	NOT NULL	COMMENT '기준항목식별자',
	`확정기준식별자`	UUID	NOT NULL	COMMENT '확정기준식별자',
	`항목순서`	INTEGER	NOT NULL	COMMENT '항목순서',
	`항목분류`	VARCHAR(50)	NOT NULL	COMMENT '항목분류',
	`검사규칙코드`	VARCHAR(100)	NULL	COMMENT '검사규칙코드',
	`기준문장`	TEXT	NOT NULL	COMMENT '기준문장',
	`기대값내용`	TEXT	NULL	COMMENT '기대값내용',
	`필수여부`	BOOLEAN	NOT NULL	COMMENT '필수여부',
	`원천문서버전식별자`	UUID	NULL	COMMENT '원천문서버전식별자',
	`원천외부기준식별자`	UUID	NULL	COMMENT '원천외부기준식별자',
	`원문위치내용`	TEXT	NULL	COMMENT '원문위치내용',
	`적용조건내용`	TEXT	NULL	COMMENT '적용조건내용',
	`검사범위내용`	TEXT	NULL	COMMENT '검사범위내용'
);

CREATE TABLE `검수실행` (
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`대상자료버전식별자`	UUID	NOT NULL	COMMENT '대상자료버전식별자',
	`확정기준식별자`	UUID	NOT NULL	COMMENT '확정기준식별자',
	`납품묶음식별자`	UUID	NULL	COMMENT '납품묶음식별자',
	`원재검수실행식별자`	UUID	NULL	COMMENT '원재검수실행식별자',
	`실행상태`	VARCHAR(40)	NOT NULL	COMMENT '실행상태',
	`종합판정`	VARCHAR(40)	NULL	COMMENT '종합판정',
	`실행구성방식`	VARCHAR(40)	NOT NULL	COMMENT '실행구성방식',
	`실행흐름버전`	VARCHAR(100)	NULL	COMMENT '실행흐름버전',
	`통합정책버전`	VARCHAR(100)	NOT NULL	COMMENT '통합정책버전',
	`충돌정책버전`	VARCHAR(100)	NULL	COMMENT '충돌정책버전',
	`접수중복방지키`	VARCHAR(200)	NOT NULL	COMMENT '접수중복방지키',
	`실행요청내용`	TEXT	NULL	COMMENT '실행요청내용',
	`시작시각`	TIMESTAMP	NULL	COMMENT '시작시각',
	`완료시각`	TIMESTAMP	NULL	COMMENT '완료시각',
	`최종확정시각`	TIMESTAMP	NULL	COMMENT '최종확정시각'
);

CREATE TABLE `작업처리시도` (
	`처리시도식별자`	UUID	NOT NULL	COMMENT '처리시도식별자',
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`검수작업식별자`	UUID	NULL	COMMENT '검수작업식별자',
	`시도번호`	INTEGER	NOT NULL	COMMENT '시도번호',
	`처리단계`	VARCHAR(50)	NOT NULL	COMMENT '처리단계',
	`실행상태`	VARCHAR(40)	NOT NULL	COMMENT '실행상태',
	`실제실행내용`	TEXT	NULL	COMMENT '실제실행내용',
	`실패내용`	TEXT	NULL	COMMENT '실패내용',
	`시작시각`	TIMESTAMP	NULL	COMMENT '시작시각',
	`종료시각`	TIMESTAMP	NULL	COMMENT '종료시각'
);

CREATE TABLE `검수항목결과` (
	`검수항목결과식별자`	UUID	NOT NULL	COMMENT '검수항목결과식별자',
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`처리시도식별자`	UUID	NOT NULL	COMMENT '처리시도식별자',
	`검수작업식별자`	UUID	NULL	COMMENT '검수작업식별자',
	`에이전트실행식별자`	UUID	NULL	COMMENT '에이전트실행식별자',
	`해결대상충돌식별자`	UUID	NULL	COMMENT '해결대상충돌식별자',
	`기준항목식별자`	UUID	NOT NULL	COMMENT '기준항목식별자',
	`문자인식실행식별자`	UUID	NULL	COMMENT '문자인식실행식별자',
	`추출식별자`	UUID	NULL	COMMENT '추출식별자',
	`외부기준비교식별자`	UUID	NULL	COMMENT '외부기준비교식별자',
	`실행상태`	VARCHAR(40)	NOT NULL	COMMENT '실행상태',
	`판정`	VARCHAR(40)	NULL	COMMENT '판정',
	`관찰값내용`	TEXT	NULL	COMMENT '관찰값내용',
	`판정사유`	TEXT	NOT NULL	COMMENT '판정사유',
	`검사방법`	VARCHAR(100)	NULL	COMMENT '검사방법',
	`검사한계내용`	TEXT	NULL	COMMENT '검사한계내용',
	`후속조치내용`	TEXT	NULL	COMMENT '후속조치내용'
);

CREATE TABLE `현재항목채택` (
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`기준항목식별자`	UUID	NOT NULL	COMMENT '기준항목식별자',
	`채택결과식별자`	UUID	NOT NULL	COMMENT '채택결과식별자',
	`채택이력식별자`	UUID	NULL	COMMENT '채택이력식별자',
	`채택수정번호`	INTEGER	NOT NULL	COMMENT '채택수정번호',
	`채택시각`	TIMESTAMP	NOT NULL	COMMENT '채택시각'
);

CREATE TABLE `에이전트정의` (
	`에이전트정의식별자`	UUID	NOT NULL	COMMENT '에이전트정의식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`에이전트명`	VARCHAR(150)	NOT NULL	COMMENT '에이전트명',
	`업무역할`	VARCHAR(100)	NOT NULL	COMMENT '업무역할',
	`정의버전번호`	INTEGER	NOT NULL	COMMENT '정의버전번호',
	`지시문파일식별자`	UUID	NULL	COMMENT '지시문파일식별자',
	`입력규격식별자`	UUID	NULL	COMMENT '입력규격식별자',
	`출력규격식별자`	UUID	NULL	COMMENT '출력규격식별자',
	`입출력규약버전`	VARCHAR(100)	NOT NULL	COMMENT '입출력규약버전',
	`요청모델명`	VARCHAR(200)	NULL	COMMENT '요청모델명',
	`모델설정내용`	TEXT	NULL	COMMENT '모델설정내용',
	`허용도구내용`	TEXT	NULL	COMMENT '허용도구내용',
	`자료접근범위`	TEXT	NULL	COMMENT '자료접근범위',
	`사용상태`	VARCHAR(40)	NOT NULL	COMMENT '사용상태'
);

CREATE TABLE `검수작업` (
	`검수작업식별자`	UUID	NOT NULL	COMMENT '검수작업식별자',
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`작업구분키`	VARCHAR(200)	NOT NULL	COMMENT '작업구분키',
	`처리단계`	VARCHAR(50)	NOT NULL	COMMENT '처리단계',
	`처리주체종류`	VARCHAR(40)	NOT NULL	COMMENT '처리주체종류',
	`에이전트정의식별자`	UUID	NULL	COMMENT '에이전트정의식별자',
	`산출물종류`	VARCHAR(40)	NOT NULL	COMMENT '산출물종류',
	`필수작업여부`	BOOLEAN	NOT NULL	COMMENT '필수작업여부',
	`검사범위내용`	TEXT	NULL	COMMENT '검사범위내용',
	`재시도정책내용`	TEXT	NULL	COMMENT '재시도정책내용',
	`완료정책내용`	TEXT	NULL	COMMENT '완료정책내용',
	`작업상태`	VARCHAR(40)	NOT NULL	COMMENT '작업상태',
	`대기사유`	TEXT	NULL	COMMENT '대기사유',
	`입력목록파일식별자`	UUID	NULL	COMMENT '입력목록파일식별자',
	`현재처리시도식별자`	UUID	NULL	COMMENT '현재처리시도식별자',
	`채택처리시도식별자`	UUID	NULL	COMMENT '채택처리시도식별자'
);

CREATE TABLE `작업기준배정` (
	`검수작업식별자`	UUID	NOT NULL	COMMENT '검수작업식별자',
	`기준항목식별자`	UUID	NOT NULL	COMMENT '기준항목식별자',
	`배정역할`	VARCHAR(40)	NOT NULL	COMMENT '배정역할',
	`출력필수여부`	BOOLEAN	NOT NULL	COMMENT '출력필수여부',
	`검토대상충돌식별자`	UUID	NULL	COMMENT '검토대상충돌식별자',
	`검토후보집합버전`	INTEGER	NULL	COMMENT '검토후보집합버전',
	`검토후보집합해시`	VARCHAR(64)	NULL	COMMENT '검토후보집합해시'
);

CREATE TABLE `작업의존관계` (
	`입력역할`	VARCHAR(100)	NOT NULL	COMMENT '입력역할',
	`후행작업식별자`	UUID	NOT NULL	COMMENT '후행작업식별자',
	`선행작업식별자`	UUID	NOT NULL	COMMENT '선행작업식별자',
	`선행완료조건`	VARCHAR(40)	NOT NULL	COMMENT '선행완료조건',
	`채택선행시도식별자`	UUID	NULL	COMMENT '채택선행시도식별자',
	`입력문자인식식별자`	UUID	NULL	COMMENT '입력문자인식식별자',
	`입력추출식별자`	UUID	NULL	COMMENT '입력추출식별자',
	`입력외부비교식별자`	UUID	NULL	COMMENT '입력외부비교식별자',
	`입력파일식별자`	UUID	NULL	COMMENT '입력파일식별자'
);

CREATE TABLE `에이전트실행` (
	`에이전트실행식별자`	UUID	NOT NULL	COMMENT '에이전트실행식별자',
	`에이전트정의식별자`	UUID	NOT NULL	COMMENT '에이전트정의식별자',
	`처리시도식별자`	UUID	NULL	COMMENT '처리시도식별자',
	`기준초안식별자`	UUID	NULL	COMMENT '기준초안식별자',
	`초안수정번호`	INTEGER	NULL	COMMENT '초안수정번호',
	`초안분석요청키`	VARCHAR(200)	NULL	COMMENT '초안분석요청키',
	`실행범위`	VARCHAR(40)	NOT NULL	COMMENT '실행범위',
	`호출순번`	INTEGER	NOT NULL	COMMENT '호출순번',
	`상위실행식별자`	UUID	NULL	COMMENT '상위실행식별자',
	`재호출원본실행식별자`	UUID	NULL	COMMENT '재호출원본실행식별자',
	`입력목록파일식별자`	UUID	NOT NULL	COMMENT '입력목록파일식별자',
	`출력파일식별자`	UUID	NULL	COMMENT '출력파일식별자',
	`실행상태`	VARCHAR(40)	NOT NULL	COMMENT '실행상태',
	`출력검증상태`	VARCHAR(40)	NOT NULL	COMMENT '출력검증상태',
	`업무반영상태`	VARCHAR(40)	NOT NULL	COMMENT '업무반영상태',
	`실제모델명`	VARCHAR(200)	NULL	COMMENT '실제모델명',
	`실제모델버전`	VARCHAR(150)	NULL	COMMENT '실제모델버전',
	`출력요약내용`	TEXT	NULL	COMMENT '출력요약내용',
	`실패내용`	TEXT	NULL	COMMENT '실패내용',
	`시작시각`	TIMESTAMP	NULL	COMMENT '시작시각',
	`종료시각`	TIMESTAMP	NULL	COMMENT '종료시각'
);

CREATE TABLE `도구호출` (
	`도구호출식별자`	UUID	NOT NULL	COMMENT '도구호출식별자',
	`에이전트실행식별자`	UUID	NOT NULL	COMMENT '에이전트실행식별자',
	`논리호출구분키`	VARCHAR(200)	NOT NULL	COMMENT '논리호출구분키',
	`전송시도번호`	INTEGER	NOT NULL	COMMENT '전송시도번호',
	`원호출식별자`	UUID	NULL	COMMENT '원호출식별자',
	`도구명`	VARCHAR(150)	NOT NULL	COMMENT '도구명',
	`도구버전`	VARCHAR(100)	NOT NULL	COMMENT '도구버전',
	`동작종류`	VARCHAR(50)	NOT NULL	COMMENT '동작종류',
	`요청파일식별자`	UUID	NOT NULL	COMMENT '요청파일식별자',
	`응답파일식별자`	UUID	NULL	COMMENT '응답파일식별자',
	`호출상태`	VARCHAR(40)	NOT NULL	COMMENT '호출상태',
	`외부요청식별값`	VARCHAR(200)	NULL	COMMENT '외부요청식별값',
	`실패내용`	TEXT	NULL	COMMENT '실패내용',
	`시작시각`	TIMESTAMP	NULL	COMMENT '시작시각',
	`종료시각`	TIMESTAMP	NULL	COMMENT '종료시각'
);

CREATE TABLE `결과충돌` (
	`충돌식별자`	UUID	NOT NULL	COMMENT '충돌식별자',
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`기준항목식별자`	UUID	NOT NULL	COMMENT '기준항목식별자',
	`충돌종류`	VARCHAR(100)	NOT NULL	COMMENT '충돌종류',
	`처리상태`	VARCHAR(40)	NOT NULL	COMMENT '처리상태',
	`후보집합버전`	INTEGER	NOT NULL	COMMENT '후보집합버전',
	`후보집합해시`	VARCHAR(64)	NOT NULL	COMMENT '후보집합해시',
	`충돌정책버전`	VARCHAR(100)	NOT NULL	COMMENT '충돌정책버전',
	`충돌설명내용`	TEXT	NULL	COMMENT '충돌설명내용',
	`해결채택이력식별자`	UUID	NULL	COMMENT '해결채택이력식별자',
	`발견시각`	TIMESTAMP	NOT NULL	COMMENT '발견시각',
	`해결시각`	TIMESTAMP	NULL	COMMENT '해결시각'
);

CREATE TABLE `충돌후보` (
	`충돌식별자`	UUID	NOT NULL	COMMENT '충돌식별자',
	`검수항목결과식별자`	UUID	NOT NULL	COMMENT '검수항목결과식별자',
	`추가당시집합버전`	INTEGER	NOT NULL	COMMENT '추가당시집합버전',
	`후보역할`	VARCHAR(40)	NOT NULL	COMMENT '후보역할'
);

CREATE TABLE `결과채택이력` (
	`채택이력식별자`	UUID	NOT NULL	COMMENT '채택이력식별자',
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`기준항목식별자`	UUID	NOT NULL	COMMENT '기준항목식별자',
	`채택수정번호`	INTEGER	NOT NULL	COMMENT '채택수정번호',
	`이전채택이력식별자`	UUID	NULL	COMMENT '이전채택이력식별자',
	`결정중복방지키`	VARCHAR(200)	NOT NULL	COMMENT '결정중복방지키',
	`채택결과식별자`	UUID	NOT NULL	COMMENT '채택결과식별자',
	`충돌식별자`	UUID	NULL	COMMENT '충돌식별자',
	`판단후보집합버전`	INTEGER	NULL	COMMENT '판단후보집합버전',
	`판단후보집합해시`	VARCHAR(64)	NULL	COMMENT '판단후보집합해시',
	`채택정책버전`	VARCHAR(100)	NOT NULL	COMMENT '채택정책버전',
	`결정종류`	VARCHAR(50)	NOT NULL	COMMENT '결정종류',
	`결정사유`	TEXT	NOT NULL	COMMENT '결정사유',
	`결정주체종류`	VARCHAR(40)	NOT NULL	COMMENT '결정주체종류',
	`결정자식별자`	UUID	NULL	COMMENT '결정자식별자',
	`결정에이전트실행식별자`	UUID	NULL	COMMENT '결정에이전트실행식별자',
	`결정자료파일식별자`	UUID	NULL	COMMENT '결정자료파일식별자',
	`결정시각`	TIMESTAMP	NOT NULL	COMMENT '결정시각'
);

CREATE TABLE `외부기준수집본` (
	`수집본식별자`	UUID	NOT NULL	COMMENT '수집본식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`수집출처명`	VARCHAR(200)	NOT NULL	COMMENT '수집출처명',
	`수집자료버전`	VARCHAR(100)	NOT NULL	COMMENT '수집자료버전',
	`출처주소`	TEXT	NULL	COMMENT '출처주소',
	`수집시각`	TIMESTAMP	NULL	COMMENT '수집시각',
	`원문파일식별자`	UUID	NOT NULL	COMMENT '원문파일식별자',
	`정규화파일식별자`	UUID	NULL	COMMENT '정규화파일식별자',
	`규격식별자`	UUID	NULL	COMMENT '규격식별자',
	`정규화방법버전`	VARCHAR(100)	NOT NULL	COMMENT '정규화방법버전',
	`수집내용해시`	VARCHAR(64)	NOT NULL	COMMENT '수집내용해시'
);

CREATE TABLE `외부행사기준` (
	`외부행사기준식별자`	UUID	NOT NULL	COMMENT '외부행사기준식별자',
	`수집본식별자`	UUID	NOT NULL	COMMENT '수집본식별자',
	`원천기록식별값`	VARCHAR(200)	NOT NULL	COMMENT '원천기록식별값',
	`등록기관명`	VARCHAR(200)	NULL	COMMENT '등록기관명',
	`자료기준일`	DATE	NULL	COMMENT '자료기준일',
	`행사명`	VARCHAR(255)	NULL	COMMENT '행사명',
	`시작날짜`	DATE	NULL	COMMENT '시작날짜',
	`종료날짜`	DATE	NULL	COMMENT '종료날짜',
	`행사장소`	TEXT	NULL	COMMENT '행사장소',
	`원천전체내용`	TEXT	NOT NULL	COMMENT '원천전체내용'
);

CREATE TABLE `외부기준비교` (
	`외부기준비교식별자`	UUID	NOT NULL	COMMENT '외부기준비교식별자',
	`추출식별자`	UUID	NOT NULL	COMMENT '추출식별자',
	`비교버전번호`	INTEGER	NOT NULL	COMMENT '비교버전번호',
	`이전비교식별자`	UUID	NULL	COMMENT '이전비교식별자',
	`채택외부기준식별자`	UUID	NULL	COMMENT '채택외부기준식별자',
	`기준연결상태`	VARCHAR(40)	NOT NULL	COMMENT '기준연결상태',
	`비교실행상태`	VARCHAR(40)	NOT NULL	COMMENT '비교실행상태',
	`비교정책버전`	VARCHAR(100)	NOT NULL	COMMENT '비교정책버전',
	`선택자식별자`	UUID	NULL	COMMENT '선택자식별자',
	`선택사유`	TEXT	NULL	COMMENT '선택사유',
	`비교원문파일식별자`	UUID	NULL	COMMENT '비교원문파일식별자',
	`보고된비교요약`	TEXT	NULL	COMMENT '보고된비교요약',
	`자료확인상태`	VARCHAR(40)	NOT NULL	COMMENT '자료확인상태'
);

CREATE TABLE `외부기준후보` (
	`외부기준비교식별자`	UUID	NOT NULL	COMMENT '외부기준비교식별자',
	`외부행사기준식별자`	UUID	NOT NULL	COMMENT '외부행사기준식별자',
	`후보순위`	INTEGER	NULL	COMMENT '후보순위',
	`후보점수`	NUMERIC(12,6)	NULL	COMMENT '후보점수',
	`연결근거내용`	TEXT	NULL	COMMENT '연결근거내용'
);

CREATE TABLE `외부항목비교결과` (
	`외부항목비교결과식별자`	UUID	NOT NULL	COMMENT '외부항목비교결과식별자',
	`외부기준비교식별자`	UUID	NOT NULL	COMMENT '외부기준비교식별자',
	`항목경로`	VARCHAR(255)	NOT NULL	COMMENT '항목경로',
	`비교상태`	VARCHAR(40)	NOT NULL	COMMENT '비교상태',
	`포스터관찰값`	TEXT	NULL	COMMENT '포스터관찰값',
	`외부기준값`	TEXT	NULL	COMMENT '외부기준값',
	`비교사유`	TEXT	NULL	COMMENT '비교사유'
);

CREATE TABLE `납품묶음버전` (
	`납품묶음식별자`	UUID	NOT NULL	COMMENT '납품묶음식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`납품버전번호`	INTEGER	NOT NULL	COMMENT '납품버전번호',
	`이전납품식별자`	UUID	NULL	COMMENT '이전납품식별자',
	`대표자료버전식별자`	UUID	NOT NULL	COMMENT '대표자료버전식별자',
	`제작신고내용`	TEXT	NULL	COMMENT '제작신고내용',
	`제출자식별자`	UUID	NOT NULL	COMMENT '제출자식별자',
	`제출시각`	TIMESTAMP	NOT NULL	COMMENT '제출시각',
	`묶음확정시각`	TIMESTAMP	NOT NULL	COMMENT '묶음확정시각'
);

CREATE TABLE `납품자료연결` (
	`제출자료역할`	VARCHAR(50)	NOT NULL	COMMENT '제출자료역할',
	`납품묶음식별자`	UUID	NOT NULL	COMMENT '납품묶음식별자',
	`자료버전식별자`	UUID	NOT NULL	COMMENT '자료버전식별자',
	`자료순서`	INTEGER	NOT NULL	COMMENT '자료순서',
	`제출설명내용`	TEXT	NULL	COMMENT '제출설명내용'
);

CREATE TABLE `사람판정정정` (
	`사람정정식별자`	UUID	NOT NULL	COMMENT '사람정정식별자',
	`검수항목결과식별자`	UUID	NOT NULL	COMMENT '검수항목결과식별자',
	`이전정정식별자`	UUID	NULL	COMMENT '이전정정식별자',
	`정정자식별자`	UUID	NOT NULL	COMMENT '정정자식별자',
	`정정판정`	VARCHAR(40)	NOT NULL	COMMENT '정정판정',
	`정정사유`	TEXT	NOT NULL	COMMENT '정정사유',
	`정정시각`	TIMESTAMP	NOT NULL	COMMENT '정정시각'
);

CREATE TABLE `검수보고서` (
	`보고서식별자`	UUID	NOT NULL	COMMENT '보고서식별자',
	`검수실행식별자`	UUID	NOT NULL	COMMENT '검수실행식별자',
	`보고서버전번호`	INTEGER	NOT NULL	COMMENT '보고서버전번호',
	`보고서파일식별자`	UUID	NULL	COMMENT '보고서파일식별자',
	`고정된보고내용`	TEXT	NOT NULL	COMMENT '고정된보고내용',
	`보고내용해시`	VARCHAR(64)	NOT NULL	COMMENT '보고내용해시',
	`생성시각`	TIMESTAMP	NOT NULL	COMMENT '생성시각'
);

CREATE TABLE `사람승인이력` (
	`승인이력식별자`	UUID	NOT NULL	COMMENT '승인이력식별자',
	`보고서식별자`	UUID	NOT NULL	COMMENT '보고서식별자',
	`승인자식별자`	UUID	NOT NULL	COMMENT '승인자식별자',
	`승인목적`	VARCHAR(100)	NOT NULL	COMMENT '승인목적',
	`승인행위`	VARCHAR(50)	NOT NULL	COMMENT '승인행위',
	`승인사유`	TEXT	NULL	COMMENT '승인사유',
	`처리한예외내용`	TEXT	NULL	COMMENT '처리한예외내용',
	`대상보고내용해시`	VARCHAR(64)	NOT NULL	COMMENT '대상보고내용해시',
	`발생시각`	TIMESTAMP	NOT NULL	COMMENT '발생시각'
);

CREATE TABLE `보완작업` (
	`보완작업식별자`	UUID	NOT NULL	COMMENT '보완작업식별자',
	`프로젝트식별자`	UUID	NOT NULL	COMMENT '프로젝트식별자',
	`원검수실행식별자`	UUID	NOT NULL	COMMENT '원검수실행식별자',
	`원항목결과식별자`	UUID	NOT NULL	COMMENT '원항목결과식별자',
	`기준항목식별자`	UUID	NOT NULL	COMMENT '기준항목식별자',
	`보완종류`	VARCHAR(50)	NOT NULL	COMMENT '보완종류',
	`요청내용`	TEXT	NOT NULL	COMMENT '요청내용',
	`담당자식별자`	UUID	NULL	COMMENT '담당자식별자',
	`처리기한`	TIMESTAMP	NULL	COMMENT '처리기한',
	`완료조건내용`	TEXT	NOT NULL	COMMENT '완료조건내용',
	`처리상태`	VARCHAR(40)	NOT NULL	COMMENT '처리상태',
	`작업중복방지키`	VARCHAR(200)	NOT NULL	COMMENT '작업중복방지키'
);

CREATE TABLE `보완제출이력` (
	`보완제출식별자`	UUID	NOT NULL	COMMENT '보완제출식별자',
	`보완작업식별자`	UUID	NOT NULL	COMMENT '보완작업식별자',
	`납품묶음식별자`	UUID	NOT NULL	COMMENT '납품묶음식별자',
	`재검수실행식별자`	UUID	NULL	COMMENT '재검수실행식별자',
	`확인결과식별자`	UUID	NULL	COMMENT '확인결과식별자',
	`해결확인상태`	VARCHAR(40)	NOT NULL	COMMENT '해결확인상태',
	`확인자식별자`	UUID	NULL	COMMENT '확인자식별자',
	`제출시각`	TIMESTAMP	NOT NULL	COMMENT '제출시각',
	`확인시각`	TIMESTAMP	NULL	COMMENT '확인시각'
);

ALTER TABLE `사용자` ADD CONSTRAINT `PK_사용자` PRIMARY KEY (
	`사용자식별자`
);

ALTER TABLE `사용자` ADD CONSTRAINT `UQ_사용자_이메일` UNIQUE (
	`이메일`
);

ALTER TABLE `사용자` ADD CONSTRAINT `CK_사용자_이메일정규화`
CHECK (`이메일` = LOWER(TRIM(`이메일`)) AND `이메일` <> '');

ALTER TABLE `사용자` ADD CONSTRAINT `CK_사용자_비밀번호해시필수`
CHECK (`비밀번호해시` <> '');

ALTER TABLE `사용자세션` ADD CONSTRAINT `PK_사용자세션` PRIMARY KEY (
	`세션식별자`
);

ALTER TABLE `사용자세션` ADD CONSTRAINT `UQ_사용자세션_토큰해시` UNIQUE (
	`세션토큰해시`
);

ALTER TABLE `사용자세션` ADD CONSTRAINT `CK_사용자세션_토큰해시형식`
CHECK (`세션토큰해시` REGEXP '^[0-9a-f]{64}$');

ALTER TABLE `사용자세션` ADD CONSTRAINT `CK_사용자세션_만료시각`
CHECK (`만료시각` > `발급시각`);

ALTER TABLE `사용자세션` ADD CONSTRAINT `CK_사용자세션_폐기시각`
CHECK (`폐기시각` IS NULL OR `폐기시각` >= `발급시각`);

ALTER TABLE `프로젝트` ADD CONSTRAINT `PK_프로젝트` PRIMARY KEY (
	`프로젝트식별자`
);

ALTER TABLE `프로젝트참여자` ADD CONSTRAINT `PK_프로젝트참여자` PRIMARY KEY (
	`프로젝트식별자`,
	`사용자식별자`
);

ALTER TABLE `자료` ADD CONSTRAINT `PK_자료` PRIMARY KEY (
	`자료식별자`
);

ALTER TABLE `자료버전` ADD CONSTRAINT `PK_자료버전` PRIMARY KEY (
	`자료버전식별자`
);

ALTER TABLE `저장파일` ADD CONSTRAINT `PK_저장파일` PRIMARY KEY (
	`파일식별자`
);

ALTER TABLE `이미지좌표계` ADD CONSTRAINT `PK_이미지좌표계` PRIMARY KEY (
	`좌표계식별자`
);

ALTER TABLE `외부식별별칭` ADD CONSTRAINT `PK_외부식별별칭` PRIMARY KEY (
	`별칭식별자`
);

ALTER TABLE `데이터규격` ADD CONSTRAINT `PK_데이터규격` PRIMARY KEY (
	`규격식별자`
);

ALTER TABLE `인계묶음` ADD CONSTRAINT `PK_인계묶음` PRIMARY KEY (
	`인계묶음식별자`
);

ALTER TABLE `감사이력` ADD CONSTRAINT `PK_감사이력` PRIMARY KEY (
	`감사이력식별자`
);

ALTER TABLE `문자인식실행` ADD CONSTRAINT `PK_문자인식실행` PRIMARY KEY (
	`문자인식실행식별자`
);

ALTER TABLE `인식문구` ADD CONSTRAINT `PK_인식문구` PRIMARY KEY (
	`인식문구식별자`
);

ALTER TABLE `포스터추출` ADD CONSTRAINT `PK_포스터추출` PRIMARY KEY (
	`추출식별자`
);

ALTER TABLE `포스터항목관찰` ADD CONSTRAINT `PK_포스터항목관찰` PRIMARY KEY (
	`관찰식별자`
);

ALTER TABLE `근거` ADD CONSTRAINT `PK_근거` PRIMARY KEY (
	`근거식별자`
);

ALTER TABLE `근거문구연결` ADD CONSTRAINT `PK_근거문구연결` PRIMARY KEY (
	`근거식별자`,
	`인식문구식별자`
);

ALTER TABLE `검수기준묶음` ADD CONSTRAINT `PK_검수기준묶음` PRIMARY KEY (
	`기준묶음식별자`
);

ALTER TABLE `검수기준초안` ADD CONSTRAINT `PK_검수기준초안` PRIMARY KEY (
	`기준초안식별자`
);

ALTER TABLE `확정검수기준` ADD CONSTRAINT `PK_확정검수기준` PRIMARY KEY (
	`확정기준식별자`
);

ALTER TABLE `검수기준항목` ADD CONSTRAINT `PK_검수기준항목` PRIMARY KEY (
	`기준항목식별자`
);

ALTER TABLE `검수실행` ADD CONSTRAINT `PK_검수실행` PRIMARY KEY (
	`검수실행식별자`
);

ALTER TABLE `작업처리시도` ADD CONSTRAINT `PK_작업처리시도` PRIMARY KEY (
	`처리시도식별자`
);

ALTER TABLE `검수항목결과` ADD CONSTRAINT `PK_검수항목결과` PRIMARY KEY (
	`검수항목결과식별자`
);

ALTER TABLE `현재항목채택` ADD CONSTRAINT `PK_현재항목채택` PRIMARY KEY (
	`검수실행식별자`,
	`기준항목식별자`
);

ALTER TABLE `에이전트정의` ADD CONSTRAINT `PK_에이전트정의` PRIMARY KEY (
	`에이전트정의식별자`
);

ALTER TABLE `검수작업` ADD CONSTRAINT `PK_검수작업` PRIMARY KEY (
	`검수작업식별자`
);

ALTER TABLE `작업기준배정` ADD CONSTRAINT `PK_작업기준배정` PRIMARY KEY (
	`검수작업식별자`,
	`기준항목식별자`
);

ALTER TABLE `작업의존관계` ADD CONSTRAINT `PK_작업의존관계` PRIMARY KEY (
	`입력역할`,
	`후행작업식별자`,
	`선행작업식별자`
);

ALTER TABLE `에이전트실행` ADD CONSTRAINT `PK_에이전트실행` PRIMARY KEY (
	`에이전트실행식별자`
);

ALTER TABLE `도구호출` ADD CONSTRAINT `PK_도구호출` PRIMARY KEY (
	`도구호출식별자`
);

ALTER TABLE `결과충돌` ADD CONSTRAINT `PK_결과충돌` PRIMARY KEY (
	`충돌식별자`
);

ALTER TABLE `충돌후보` ADD CONSTRAINT `PK_충돌후보` PRIMARY KEY (
	`충돌식별자`,
	`검수항목결과식별자`
);

ALTER TABLE `결과채택이력` ADD CONSTRAINT `PK_결과채택이력` PRIMARY KEY (
	`채택이력식별자`
);

ALTER TABLE `외부기준수집본` ADD CONSTRAINT `PK_외부기준수집본` PRIMARY KEY (
	`수집본식별자`
);

ALTER TABLE `외부행사기준` ADD CONSTRAINT `PK_외부행사기준` PRIMARY KEY (
	`외부행사기준식별자`
);

ALTER TABLE `외부기준비교` ADD CONSTRAINT `PK_외부기준비교` PRIMARY KEY (
	`외부기준비교식별자`
);

ALTER TABLE `외부기준후보` ADD CONSTRAINT `PK_외부기준후보` PRIMARY KEY (
	`외부기준비교식별자`,
	`외부행사기준식별자`
);

ALTER TABLE `외부항목비교결과` ADD CONSTRAINT `PK_외부항목비교결과` PRIMARY KEY (
	`외부항목비교결과식별자`
);

ALTER TABLE `납품묶음버전` ADD CONSTRAINT `PK_납품묶음버전` PRIMARY KEY (
	`납품묶음식별자`
);

ALTER TABLE `납품자료연결` ADD CONSTRAINT `PK_납품자료연결` PRIMARY KEY (
	`제출자료역할`,
	`납품묶음식별자`,
	`자료버전식별자`
);

ALTER TABLE `사람판정정정` ADD CONSTRAINT `PK_사람판정정정` PRIMARY KEY (
	`사람정정식별자`
);

ALTER TABLE `검수보고서` ADD CONSTRAINT `PK_검수보고서` PRIMARY KEY (
	`보고서식별자`
);

ALTER TABLE `사람승인이력` ADD CONSTRAINT `PK_사람승인이력` PRIMARY KEY (
	`승인이력식별자`
);

ALTER TABLE `보완작업` ADD CONSTRAINT `PK_보완작업` PRIMARY KEY (
	`보완작업식별자`
);

ALTER TABLE `보완제출이력` ADD CONSTRAINT `PK_보완제출이력` PRIMARY KEY (
	`보완제출식별자`
);

ALTER TABLE `사용자세션` ADD CONSTRAINT `FK_사용자_TO_사용자세션_1` FOREIGN KEY (
	`사용자식별자`
)
REFERENCES `사용자` (
	`사용자식별자`
) ON DELETE RESTRICT;

ALTER TABLE `프로젝트참여자` ADD CONSTRAINT `FK_프로젝트_TO_프로젝트참여자_1` FOREIGN KEY (
	`프로젝트식별자`
)
REFERENCES `프로젝트` (
	`프로젝트식별자`
);

ALTER TABLE `프로젝트참여자` ADD CONSTRAINT `FK_사용자_TO_프로젝트참여자_1` FOREIGN KEY (
	`사용자식별자`
)
REFERENCES `사용자` (
	`사용자식별자`
);

ALTER TABLE `근거문구연결` ADD CONSTRAINT `FK_근거_TO_근거문구연결_1` FOREIGN KEY (
	`근거식별자`
)
REFERENCES `근거` (
	`근거식별자`
);

ALTER TABLE `근거문구연결` ADD CONSTRAINT `FK_인식문구_TO_근거문구연결_1` FOREIGN KEY (
	`인식문구식별자`
)
REFERENCES `인식문구` (
	`인식문구식별자`
);

ALTER TABLE `현재항목채택` ADD CONSTRAINT `FK_검수실행_TO_현재항목채택_1` FOREIGN KEY (
	`검수실행식별자`
)
REFERENCES `검수실행` (
	`검수실행식별자`
);

ALTER TABLE `현재항목채택` ADD CONSTRAINT `FK_검수기준항목_TO_현재항목채택_1` FOREIGN KEY (
	`기준항목식별자`
)
REFERENCES `검수기준항목` (
	`기준항목식별자`
);

ALTER TABLE `작업기준배정` ADD CONSTRAINT `FK_검수작업_TO_작업기준배정_1` FOREIGN KEY (
	`검수작업식별자`
)
REFERENCES `검수작업` (
	`검수작업식별자`
);

ALTER TABLE `작업기준배정` ADD CONSTRAINT `FK_검수기준항목_TO_작업기준배정_1` FOREIGN KEY (
	`기준항목식별자`
)
REFERENCES `검수기준항목` (
	`기준항목식별자`
);

ALTER TABLE `작업의존관계` ADD CONSTRAINT `FK_검수작업_TO_작업의존관계_1` FOREIGN KEY (
	`후행작업식별자`
)
REFERENCES `검수작업` (
	`검수작업식별자`
);

ALTER TABLE `작업의존관계` ADD CONSTRAINT `FK_검수작업_TO_작업의존관계_2` FOREIGN KEY (
	`선행작업식별자`
)
REFERENCES `검수작업` (
	`검수작업식별자`
);

ALTER TABLE `충돌후보` ADD CONSTRAINT `FK_결과충돌_TO_충돌후보_1` FOREIGN KEY (
	`충돌식별자`
)
REFERENCES `결과충돌` (
	`충돌식별자`
);

ALTER TABLE `충돌후보` ADD CONSTRAINT `FK_검수항목결과_TO_충돌후보_1` FOREIGN KEY (
	`검수항목결과식별자`
)
REFERENCES `검수항목결과` (
	`검수항목결과식별자`
);

ALTER TABLE `외부기준후보` ADD CONSTRAINT `FK_외부기준비교_TO_외부기준후보_1` FOREIGN KEY (
	`외부기준비교식별자`
)
REFERENCES `외부기준비교` (
	`외부기준비교식별자`
);

ALTER TABLE `외부기준후보` ADD CONSTRAINT `FK_외부행사기준_TO_외부기준후보_1` FOREIGN KEY (
	`외부행사기준식별자`
)
REFERENCES `외부행사기준` (
	`외부행사기준식별자`
);

ALTER TABLE `납품자료연결` ADD CONSTRAINT `FK_납품묶음버전_TO_납품자료연결_1` FOREIGN KEY (
	`납품묶음식별자`
)
REFERENCES `납품묶음버전` (
	`납품묶음식별자`
);

ALTER TABLE `납품자료연결` ADD CONSTRAINT `FK_자료버전_TO_납품자료연결_1` FOREIGN KEY (
	`자료버전식별자`
)
REFERENCES `자료버전` (
	`자료버전식별자`
);
