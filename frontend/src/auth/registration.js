// 서버의 기존 계정 정책과 같은 길이·확인 규칙으로 제출 전에 안내한다.
export function registrationIssue({ displayName, email, password, confirmation }) {
  const name = displayName.trim();
  if (!name || Array.from(name).length > 100 || /[\u0000-\u001f]/.test(name)) {
    return { field: 'name', message: '이름을 1~100자로 입력해 주세요.' };
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
    return { field: 'email', message: '올바른 이메일을 입력해 주세요.' };
  }
  const length = Array.from(password).length;
  if (length < 15 || length > 128 || password.includes('\0')) {
    return { field: 'password', message: '비밀번호를 15~128자로 입력해 주세요.' };
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
