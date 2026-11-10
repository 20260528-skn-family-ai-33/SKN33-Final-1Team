# AWS 테스트 프론트 자동 배포

`AWS_TEST` 브랜치의 프론트 코드를 검사하고, 검사에 성공하면 테스트용 S3와 CloudFront에 배포합니다. 운영용 프론트 버킷은 변경하지 않습니다.

## 배포 대상

- GitHub 저장소: `20260528-skn-family-ai-33/SKN33-Final-1Team`
- 브랜치: `AWS_TEST`
- S3 버킷: `creative-checker-frontend-test-20261108-jh`
- CloudFront 배포 ID: `ESI2WHFLU88QT`
- 테스트 주소: `https://d3navmtcjn23y2.cloudfront.net/`
- AWS 리전: `ap-northeast-2` (서울)

2026-11-09 후속 반영으로 포스터 API는 테스트 `8016`, main `8018`로 나뉘었으며, 일반 인증 API와 PostgreSQL·EFS는 공유합니다. 영상 API `8013`과 두 워커도 main·테스트가 공유합니다. 따라서 브랜치와 프론트 버킷이 달라도 데이터까지 격리된 시험 환경은 아닙니다. 프로젝트 생성·PDF 등록·승인·검수는 공유 저장소에 반영됩니다. 연결·검증 시점은 [AWS 운영 현황](agent-framework/AWS_인프라_운영현황_20261110.md)을 확인하세요.

이 워크플로는 API·워커 이미지나 CloudFront Function을 자동 배포하지 않습니다. backend 변경과 새 화면 경로 추가는 별도 서버·CloudFront 반영이 필요합니다.

## 동작 순서

1. `AWS_TEST`에 푸시하거나 GitHub CLI에서 해당 브랜치를 지정해 실행합니다.
2. Node.js 의존성을 설치하고 프론트 테스트와 빌드를 실행합니다.
3. AWS 역할이 설정되어 있으면 계정과 CloudFront의 S3 원본을 확인합니다.
4. 빌드 결과를 테스트 버킷에 올리고, CloudFront의 `/`와 `/index.html` 캐시를 갱신합니다.

설정된 AWS 역할이 없으면 검사와 빌드는 실행되지만 배포는 건너뜁니다. 빌드에는 현재 서비스와 같은 `VITE_API_PROFILE=legacy`를 사용합니다. 다른 버전의 사용자가 열어둔 파일이 사라지지 않도록 버킷의 기존 파일을 자동 삭제하지 않습니다.

## 최초 1회: GitHub에서 사용할 AWS 역할 만들기

고정 AWS 액세스 키를 GitHub에 저장하지 않고, GitHub Actions의 OIDC 인증을 사용합니다. 다음 설정은 AWS 관리자 권한이 있는 사람이 AWS 콘솔에서 진행합니다.

1. IAM의 **자격 증명 공급자**에서 `token.actions.githubusercontent.com` 공급자가 있는지 확인합니다. 없다면 공급자 유형을 OpenID Connect로 만들고 대상은 `sts.amazonaws.com`으로 설정합니다. 이미 있으면 기존 공급자를 사용합니다.
2. IAM에서 새 역할을 만들고 웹 자격 증명 공급자로 위 공급자를 선택합니다. 이 저장소는 2026년 7월 15일 이후에 생성되어 OIDC `sub`에 조직 ID와 저장소 ID가 포함됩니다. 역할의 신뢰 정책을 `AWS_TEST`의 정확한 `sub`로 제한합니다. [GitHub OIDC 규격](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims)

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::357297649391:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:20260528-skn-family-ai-33@288865373/SKN33-Final-1Team@1391545254:ref:refs/heads/AWS_TEST"
      }
    }
  }]
}
```

3. 역할에 다음 권한 정책을 연결합니다. 테스트 버킷과 테스트 배포에만 적용됩니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListTestBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::creative-checker-frontend-test-20261108-jh"
    },
    {
      "Sid": "UploadTestFrontend",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::creative-checker-frontend-test-20261108-jh/*"
    },
    {
      "Sid": "RefreshTestDistribution",
      "Effect": "Allow",
      "Action": ["cloudfront:GetDistributionConfig", "cloudfront:CreateInvalidation"],
      "Resource": "arn:aws:cloudfront::357297649391:distribution/ESI2WHFLU88QT"
    }
  ]
}
```

4. 생성한 역할의 ARN을 복사합니다. GitHub 저장소의 **Settings → Secrets and variables → Actions → Variables**에서 이름 `AWS_TEST_DEPLOY_ROLE_ARN`으로 등록합니다. 값은 위에서 만든 역할의 ARN입니다.
5. 워크플로가 `AWS_TEST`에만 있을 때는 아래 명령으로 실행합니다. `AWS_TEST`에 푸시해도 자동 실행됩니다.

   ```bash
   gh workflow run aws-test-frontend.yml --ref AWS_TEST \
     --repo 20260528-skn-family-ai-33/SKN33-Final-1Team
   ```

## 결과 확인

- Actions의 검사·빌드·배포 작업이 모두 성공했는지 확인합니다. 배포가 건너뛰어졌다면 역할 변수가 등록되었는지 확인합니다.
- 테스트 주소를 열어 화면과 로그인, PDF 업로드를 확인합니다. API 연결 상태와 실제 검수 결과는 별도로 확인해야 합니다.
- CloudFront 배포 상태가 변경 중이면 반영이 완료된 뒤 다시 확인합니다.
- AWS 요금은 CloudFront, S3 및 사용 중인 다른 리소스를 포함해 별도로 발생할 수 있습니다.

2026-11-08에 `CreativeCheckerAwsTestFrontendDeploy` 역할을 연결하고 `AWS_TEST`의 `2769e80`을 배포했습니다. 프론트 테스트 125개, 테스트 S3 업로드, CloudFront 캐시 갱신 완료를 확인했습니다. 테스트 사이트의 HTML과 진입 JS·CSS는 배포 산출물과 일치했고, `/api/framework/v1/health`는 HTTP 200을 반환했습니다. [첫 배포 실행](https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team/actions/runs/42457197546)
