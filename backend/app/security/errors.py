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
            'FRAMEWORK_TASK_NOT_QUEUED':'이미 시작되었거나 종료된 작업입니다. 대기 중인 작업만 취소할 수 있습니다.',
            'FRAMEWORK_TASK_NOT_FINISHED':'완료·실패·취소된 이력만 숨길 수 있습니다.',
            'FRAMEWORK_TASK_CURSOR_INVALID':'목록이 변경되었습니다. 처음부터 다시 불러와 주세요.',
            'POSTER_EDIT_NOT_CONFIGURED':'이 서버에 포스터 문구 수정 기능이 아직 연결되지 않았어요.',
            'POSTER_EDIT_APPROVAL_REQUIRED':'수정 요청을 승인한 포스터에서 문구를 수정할 수 있어요.',
            'POSTER_EDIT_SOURCE_UNAVAILABLE':'검수에 사용한 포스터를 불러오지 못했어요.',
            'POSTER_EDIT_AREA_TOO_SMALL':'문구가 들어갈 영역을 조금 더 넓게 선택해 주세요.',
            'POSTER_EDIT_BUSY':'다른 포스터를 수정하고 있어요. 잠시 후 다시 시도해 주세요.',
            'POSTER_EDIT_PROVIDER_ACCESS':'이미지 편집 서비스의 API 키와 모델 사용 권한을 확인해 주세요.',
            'POSTER_EDIT_PROVIDER_LIMIT':'이미지 편집 서비스의 사용량 한도에 도달했어요. 잠시 후 다시 시도해 주세요.',
            'POSTER_EDIT_REJECTED':'이 이미지와 문구로 수정본을 만들지 못했어요. 선택 영역이나 문구를 확인해 주세요.',
            'POSTER_EDIT_TIMEOUT':'이미지 수정 응답이 늦어지고 있어요. 잠시 후 다시 시도해 주세요.',
            'POSTER_EDIT_FAILED':'수정본을 만들지 못했어요. 잠시 후 다시 시도해 주세요.',
        }
        super().__init__(messages.get(code,'요청을 처리할 수 없습니다.'))
        self.code,self.status,self.retry_after=code,status,retry_after
