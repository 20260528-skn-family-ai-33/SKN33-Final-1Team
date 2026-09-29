# GitHub 협업 컨벤션

작업 저장소는 [SKN33-Final-1Team](https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team)입니다. 모든 변경은 Issue에서 관리합니다.

## 작업 순서

1. 작업 전에 Issue를 만듭니다. Project 초안에서 시작했다면 실제 Issue로 전환합니다.
2. 책임자, 포함·제외 범위, 확인 가능한 완료 기준, 검증 계획을 합의하고 Project의 `Backlog`에서 `Ready`로 옮깁니다.
3. 파일 변경은 기본 브랜치인 최신 `main`에서 Issue 번호를 넣은 브랜치를 만듭니다. 작업을 시작하면 `In Progress`로 옮깁니다.
4. 작업에 포함할 파일만 지정해 작고 검토 가능한 단위로 커밋하고, `main` 대상 PR을 엽니다. 추가 요구가 생기면 별도 Issue를 만들고 작업 순서를 합의합니다.
5. 리뷰와 검증을 거쳐 병합한 뒤, Issue 전체의 완료 기준을 확인하고 마무리합니다.

GitHub 웹 설정만 바꾸는 일은 브랜치나 PR 없이 진행하고, 변경 내용과 확인 결과를 해당 Issue에 남깁니다.

## 브랜치·커밋·PR 제목

브랜치는 `<type>/<issue-number>-<short-kebab-description>` 형식으로 만듭니다. 설명은 짧은 kebab-case로 씁니다.

커밋과 PR 제목은 `<type>(<scope>): 요약` 형식으로 씁니다. scope는 `mcp`, `agent`, `workflow`, `infra` 등을 사용하고, 범위가 분명하지 않으면 `<type>: 요약`으로 생략합니다. 요약은 한국어로 써도 됩니다.

type은 `feat`, `fix`, `refactor`, `docs`, `test`, `build`, `ci`, `chore` 중에서 고릅니다. 커밋은 제목 다음에 빈 줄을 두고, 필요하면 본문을 적은 뒤 마지막에 `Refs #번호`를 붙입니다.

Issue `#42`의 MCP 시간제한 작업을 예로 들면 다음과 같습니다. 번호는 실제로 생성한 Issue 번호를 사용합니다.

| 위치 | 예시 |
|---|---|
| 브랜치 | `feat/42-mcp-timeout` |
| 커밋·PR 제목 | `feat(mcp): MCP 호출 제한시간 적용` |
| 커밋 본문 | `Refs #42` |
| PR 본문 연결 | Issue 전체 완료 시 `Closes #42`, 일부 작업이면 `Refs #42` |

## Issue와 PR 작성

일반 작업과 새 기능은 [Feature / Task 템플릿](.github/ISSUE_TEMPLATE/feature_or_task.md), 버그는 [Bug Report 템플릿](.github/ISSUE_TEMPLATE/bug_report.md)을 사용합니다. 안내와 예시를 실제 작업 내용으로 바꾸고, 범위·완료 기준·검증 계획을 적습니다. 버그에는 재현 방법과 기대 동작, 실제 결과를 포함합니다.

Issue의 GitHub 기본 `Type`은 일반 작업 `Task`, 버그 `Bug`, 새 기능 `Feature`로 지정합니다. 담당자와 Project를 직접 지정하고, 필요한 라벨과 Milestone을 정합니다. 유형은 `Type`으로 관리하며 `type:*` 라벨은 사용하지 않습니다.

PR은 [PR 템플릿](.github/PULL_REQUEST_TEMPLATE.md)에 관련 Issue, 변경 사항, 검증 결과를 작성합니다. 남은 작업이나 위험, 중점적으로 검토할 부분이 있으면 리뷰 참고 사항에 적습니다.

PR 병합으로 Issue 전체가 완료되면 본문에 `Closes #번호`를 씁니다. 일부만 처리하는 PR은 `Refs #번호`로 연결하고 Issue를 닫지 않습니다. `Closes`로 연결한 PR은 기본 브랜치인 `main`에 병합한 뒤 Issue가 닫혔는지 확인합니다.

## Project 운영

Board에서는 `Status`별 진행 상황을 보고, Table에서는 담당자·우선순위·목표를 정리합니다. `Status`, `Assignees`, `Type`, `Priority`, `Labels`, `Milestone`을 함께 확인합니다.

| 항목 | 역할 |
|---|---|
| `Type` | Issue의 작업 유형: `Task`, `Bug`, `Feature` |
| `Assignees` | Issue의 작업 책임자 |
| `Labels` | Issue의 작업 영역과 차단 여부 |
| `Status` | Project의 현재 작업 단계 |
| `Priority` | Project에서만 관리하는 처리 우선순위 |
| `Milestone` | 팀이 합의한 버전·배포 목표와 완료 기준 |

유형·담당자·라벨·Milestone은 Issue에서 관리합니다. `Labels`와 `Milestone`은 Issue 기본 필드를 사용하며, 같은 이름의 사용자 지정 필드를 따로 만들지 않습니다.

### 상태

| Status | 진입 조건 |
|---|---|
| `Backlog` | 아직 준비되지 않은 Issue |
| `Ready` | 책임자, 범위, 완료 기준, 검증 계획을 합의해 시작할 수 있는 Issue |
| `In Progress` | 파일 변경이나 웹 설정 작업 중. Draft PR도 이 상태에 둡니다. |
| `In Review` | PR 또는 웹 설정 결과를 검토 중 |
| `Changes Requested` | 검토에서 요청된 수정을 보완 중 |
| `Ready to Merge` | 승인과 필수 검사를 충족해 PR 병합을 기다리는 상태 |
| `Done` | 필요한 PR 병합 또는 웹 설정 검토·검증을 마치고 Issue 전체의 완료 기준을 충족한 상태 |

`In Review`, `Changes Requested`, `Ready to Merge`는 검토 상황에 맞춰 Issue 카드를 직접 옮깁니다. 수정을 마치고 재검토를 요청하면 `In Review`로 되돌립니다. 웹 설정만 하는 일은 검토와 검증을 마치면 `In Review`에서 `Done`으로 옮길 수 있습니다.

막힌 작업에는 `flag:blocked`를 붙이고 Issue 댓글에 사유와 해소 조건을 적습니다. 별도 상태는 만들지 않습니다.

취소한 작업은 Issue를 `not planned`로 닫고 Project 항목을 정리합니다. `Done`에 들어갔다면 Project에서 제거해 완료 작업과 구분합니다.

### 우선순위

우선순위는 Project의 `Priority`로만 관리하며 우선순위 라벨은 사용하지 않습니다.

| Priority | 기준 |
|---|---|
| `P0` | 즉시 해결해야 하는 일 |
| `P1` | 중요한 작업이라 먼저 해결할 일 |
| `P2` | 당장 미뤄도 진행에 지장은 없지만 주요 작업 뒤에 처리할 일 |
| `P3` | 진행에 지장 없는 개선으로 여유 있을 때 처리할 일 |

### 라벨

`area:*`는 작업 영역에 맞는 것만 붙이고, `flag:blocked`는 막힌 경우에만 붙입니다.

| 라벨 | 용도 |
|---|---|
| `area:mcp` | MCP 서버, 도구 정의·호출 |
| `area:agent` | AI 에이전트의 역할·동작·협업 로직 |
| `area:workflow` | 업무 실행 순서·분기·상태 관리 |
| `area:infra` | Docker, 개발 환경, CI, 배포, 저장소 설정 |
| `area:docs` | README, 사용법, 설계 문서, 팀 컨벤션 |
| `flag:blocked` | 선행 작업이나 외부 응답을 기다려 진행할 수 없는 상태 |

### Milestone

Milestone은 Issue와 PR을 버전·배포 목표로 묶어 진행 상황과 기한을 관리합니다. 팀에서 목표와 완료 기준을 합의해 갱신하고, 날짜는 합의한 경우에만 넣습니다. 같은 작업이 두 번 집계되지 않도록 보통 Issue에만 지정합니다.

## 리뷰·병합

PR은 승인 1명, 리뷰 대화 해결, 설정된 필수 검사 통과를 확인한 뒤 `Ready to Merge`로 옮깁니다. 병합 방식은 Squash merge와 Merge commit을 모두 허용합니다. 상태 표시와 별개로 실제 병합 조건을 확인합니다.

부분 PR을 병합했을 때는 Issue를 열어 두고 남은 작업에 맞춰 상태를 옮깁니다. 필요한 PR 병합 또는 웹 설정 검토·검증을 마쳐 Issue 전체의 완료 기준을 충족하면 Issue를 닫고 Project가 `Done`인지 확인합니다. 병합한 작업 브랜치는 우선 정리하지 않고 보존합니다. 추후 브랜치가 많아져 작업에 지장이 생길 시 정리합니다.
