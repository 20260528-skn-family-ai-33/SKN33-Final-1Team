# 합성 에이전트 계약 예제

이 폴더의 JSON은 실제 PDF, 포스터, 모델, PostgreSQL, Neo4j 실행 결과가 아닙니다.
파일 ID와 SHA-256도 **합성 참조 문자열**을 나타냅니다. 원본 미디어 파일은 제공하지 않습니다.
모든 역할 출력의 `synthetic`은 `true`이며 모델 정확도나 배포 완료 증거로 사용할 수 없습니다.

| 역할 | 요청 JSON | 응답 JSON | 계약 |
|---|---|---|---|
| A02 조건 추출 | `A02.json` | `A02.output.json` | ExtractRequest → CriteriaDraft |
| A03 근거 검색 | `A03.json` | `A03.output.json` | RetrieveRequest → EvidenceBundle |
| A04/A05 통합 검사 | `A04_A05.json` | `A04_A05.output.json` | InspectRequest → InspectionResult |
| A06 코드 검증 | `A06.json` | `A06.output.json` | ValidateRequest → ValidatedResult |
| A07 보고서 | `A07.json` | `A07.output.json` | ReportRequest → ReportDraft |

기본 예제는 `MORNING CUP` 정확문구와 딸기 `3개` 조건을 사용합니다.
`scenarios/`에는 `pass`, `fail`, `review`, `stale`, `unavailable`의 A06 검증 결과가 있습니다.
`stale`과 `unavailable`에서는 합성 검사기가 충족을 주장해도 검색 근거가 불완전하므로 코드 검증이 `review`로 남깁니다.

저장소 루트에서 같은 JSON을 다시 생성할 수 있습니다. 외부 서비스에 접속하지 않습니다.

```sh
python -m backend.agent_framework.adapters.generate_examples
```

`custom_adapter.py`는 팀별 어댑터 팩터리의 연결 위치만 보여 줍니다. 구현하지 않은 메서드는
`ADAPTER_NOT_CONFIGURED`를 반환하며 합성 결과로 대체하지 않습니다. 실제 서비스를 연결할 때는
각 메서드의 입력·출력 계약과 현재 버전·출처·인용 검증을 유지해야 합니다.
