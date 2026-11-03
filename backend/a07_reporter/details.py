"""계약 3.0.0(A06 PR #53) ValidatedItem 필드를 사람이 읽는 말로 바꾼다. 지금 계약은 2.0.0이라 아직 연결 전이다.

- 계약 클래스 대신 값만 받는다. PR #53이 main에 들어오면 items()가 ValidatedItem의 값을 넘기게 연결한다.
- 읽은 값은 검사 모델의 주장이라 "검사 모델이 읽은 값"으로 적는다. 판정은 A06 결과를 그대로 쓴다.
- 값이 없거나 모르는 칸은 빼고 돌려준다. 같은 사실을 Qwen 입력으로도 쓰므로 빈 칸을 지어내지 않게 한다.
- 위치는 좌표 대신 3×3 칸의 말로 적는다. 위치가 없으면 적지 않는다.
"""
from __future__ import annotations

CHECK = {("text", "equals"): "문구가 같아야 함", ("text", "contains"): "문구를 포함해야 함",
         ("number", "equals"): "숫자가 같아야 함", ("number", "gte"): "기준값 이상이어야 함",
         ("number", "lte"): "기준값 이하여야 함", ("visual", "present"): "표시되어 있어야 함"}
OBSERVATION = {"observed": "읽음", "absent": "포스터에서 찾지 못함", "unreadable": "읽을 수 없음",
               "not_checked": "검사하지 않음", "unknown": "관측 상태 모름"}


def location_hint(bbox) -> str:
    """정규화 상자(x1, y1, x2, y2)를 말로 바꾼다. 거의 전체를 덮으면 "전체", 아니면 중심의 3×3 칸."""
    if bbox[2] - bbox[0] >= 0.9 and bbox[3] - bbox[1] >= 0.9:
        return "전체"
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    row = ("위", "가운데", "아래")[min(int(y * 3), 2)]
    col = ("왼쪽", "가운데", "오른쪽")[min(int(x * 3), 2)]
    if row == col == "가운데":
        return "가운데"
    if row == "가운데":
        return col
    return f"{row}쪽" if col == "가운데" else f"{col} {row}"


def value_text(kind: str, value: str | None, unit: str | None) -> str | None:
    """숫자는 단위를 붙이고(이미 붙어 있으면 그대로), 문구·시각 조건은 따옴표로 감싼다."""
    if value is None or not value.strip():
        return None
    if kind != "number":
        return f'"{value}"'
    return value if not unit or value.rstrip().endswith(unit) else f"{value}{unit}"


def facts(*, kind: str, operator: str, required: bool, expected: str | None, expected_unit: str | None = None,
          observed: str | None = None, observed_unit: str | None = None, observation_status: str = "unknown",
          bbox=None) -> dict[str, str]:
    """표시용 사실. 키: required·check·expected·observation·observed·location(없으면 뺀다)."""
    out = {"required": "필수" if required else "권장",
           "check": CHECK.get((kind, operator)) or ("사람이 직접 확인" if operator == "manual" else "자동 검사 범위 밖")}
    if (shown := value_text(kind, expected, expected_unit)):
        out["expected"] = shown
    out["observation"] = OBSERVATION.get(observation_status, OBSERVATION["unknown"])
    if observation_status == "observed" and (shown := value_text(kind, observed, observed_unit)):
        out["observed"] = shown
    if bbox is not None:
        out["location"] = location_hint(bbox)
    return out


def detail_lines(values: dict[str, str]) -> tuple[str, ...]:
    """보고서에 넣을 두 줄: 기준(필수 여부·비교 방식·기준값)과 검사 모델이 읽은 것."""
    basis = " · ".join([values["required"], values["check"],
                        *([f"기준값 {values['expected']}"] if "expected" in values else [])])
    where = f" (포스터 {values['location']})" if "location" in values else ""
    seen = (f"검사 모델이 읽은 값: {values['observed']}{where}" if "observed" in values
            else f"검사 모델: {values['observation']}")
    return f"기준: {basis}", seen
