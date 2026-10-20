"""Safe domain errors; input, credentials and database messages stay private."""
class AuthError(RuntimeError):
    def __init__(self, code, status=400, *, retry_after=None):
        messages={
            'INVALID_CREDENTIALS':'이메일 또는 비밀번호를 확인해 주세요.',
            'INVALID_AUTH_REQUEST':'인증 입력을 확인해 주세요.',
            'AUTHENTICATION_REQUIRED':'로그인이 필요합니다.',
            'AUTH_ROUTE_NOT_FOUND':'인증 경로를 찾을 수 없습니다.',
            'AUTH_SETUP_REQUIRED':'개발 계정과 프로젝트 초기화가 필요합니다.',
            'AUTH_UNAVAILABLE':'인증 서비스를 사용할 수 없습니다.',
            'AUTH_RATE_LIMITED':'잠시 후 다시 시도해 주세요.',
            'CSRF_FAILED':'보안 확인에 실패했습니다.',
            'RESOURCE_NOT_FOUND':'리소스를 찾을 수 없습니다.',
            'PROJECT_PERMISSION_DENIED':'프로젝트 권한이 부족합니다.',
            'PROJECT_ARCHIVED':'보관된 프로젝트입니다.',
            'LAST_OWNER_REQUIRED':'활성 owner가 한 명 이상 필요합니다.',
            'EMAIL_ALREADY_EXISTS':'이미 사용 중인 이메일입니다.',
            'AUTH_STATE_CHANGED':'계정 상태가 변경되었습니다. 다시 시도해 주세요.',
            'DEVELOPMENT_CONFLICT':'기존 개발 계정/프로젝트 구성이 다릅니다.',
        }
        super().__init__(messages.get(code,'요청을 처리할 수 없습니다.'))
        self.code,self.status,self.retry_after=code,status,retry_after
