// 검수를 시작하는 첫 화면에서 자료 종류(포스터·동영상)를 고르게 하는 이동 정보.
// 종류가 정해지지 않은 진입(로그인 직후 · 소개 화면의 시작하기 · 내 작업의 새 검수)은 /review/new를 이 state와 함께 연다.
// 머리글의 '포스터 검수'·'동영상 검수'나 저장된 작업 주소처럼 종류가 정해진 진입은 state 없이 열어 바로 보안 여부부터 묻는다.
// 고르는 화면에 새 주소를 두지 않는 까닭: 새로고침·직접 접근 때 서버가 화면(HTML)을 돌려주는 주소가 정해져 있다
// (backend/app/spa.py, deploy/agent-framework/aws-test-spa-rewrite.js).
export const mediaChoicePath = '/review/new';
export const mediaChoiceState = Object.freeze({ chooseMedia: true });

// location: react-router의 location(또는 로그인 화면이 받은 '돌아갈 곳').
export function wantsMediaChoice(location) {
  return location?.state?.chooseMedia === true;
}
