// 서버와 같은 비밀번호 규칙으로 제출 전에 안내한다.
const SPECIAL_CHARACTERS = `!"#$%&'()*+,-./:;<=>?@[\\]^_\`{|}~`;
export function registrationIssue({ displayName, email, password, confirmation }) {
  const name = displayName.trim();
  if (!name || Array.from(name).length > 100 || /[\u0000-\u001f]/.test(name)) {
    return { field: 'name', message: '이름을 1~100자로 입력해 주세요.' };
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
    return { field: 'email', message: '올바른 이메일을 입력해 주세요.' };
  }
  const characters = Array.from(password);
  if (characters.length < 8) {
    return { field: 'password', message: '비밀번호는 최소 8자 이상 입력해 주세요.' };
  }
  if (characters.length > 20) {
    return { field: 'password', message: '비밀번호는 최대 20자까지 입력할 수 있어요.' };
  }
  if (password.includes('\0') || !/[0-9]/.test(password) || !characters.some(char => SPECIAL_CHARACTERS.includes(char))) {
    return { field: 'password', message: '비밀번호에 숫자와 특수문자를 각각 하나 이상 포함해 주세요.' };
  }
  if (password !== confirmation) return { field: 'confirmation', message: '비밀번호 확인이 일치하지 않습니다.' };
  return null;
}

export function registrationErrorMessage(error) {
  if (error?.code === 'EMAIL_ALREADY_EXISTS') return '이미 가입된 이메일입니다. 로그인해 주세요.';
  if (error?.code === 'CSRF_FAILED') return '보안 확인에 실패했습니다. 다시 시도해 주세요.';
  if (error?.status === 422) return '이메일·이름과 비밀번호 조건을 확인해 주세요.';
  if (error?.status === 429) return '요청이 많습니다. 잠시 기다린 뒤 다시 시도해 주세요.';
  if (error?.status === 0) return '회원가입 서버에 연결하지 못했습니다. 연결 상태를 확인해 주세요.';
  if ([404, 405, 501].includes(error?.status)) return '회원가입을 지원하는 API로 업데이트한 뒤 다시 시도해 주세요.';
  return '회원가입 결과를 확인하지 못했습니다. 먼저 로그인해 가입 여부를 확인해 주세요.';
}
