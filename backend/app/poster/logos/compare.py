"""2부: 사용자가 확정한 포스터 속 로고 위치를 원본 로고와 정밀 대조한다. 판정은 내지 않고 근거를 모은다.

대조 규칙은 영상 로고 2부(backend/app/video/logos/compare.py의 reference·compare)를 그대로 쓴다:
배경 분리(M3c) → 원본을 요소(주 문구·보조 문구·심볼 또는 로고 전체)로 나눔 → 요소별 있음·빠짐·위치 다름·모양 다름·비교 불가,
추가 문구·요소, 심볼:문구 크기 비율, 외곽 비율, 색 근거. outcome은 match(차이 근거 없음)/diff(차이 근거 있음)/unknown(비교 불가 있음).
포스터는 한 장이라 영상의 묶음·초별 집계(나온 초 수, 결과가 바뀌는 초)가 없고, 확정한 위치마다 결과 하나를 낸다.
"""
from __future__ import annotations

from PIL import Image

from backend.app.video.logos import compare as V

# 영상 합성 변형 실험(2026-10-31)에서 짧은 변 60px 미만 crop은 정상 로고(대조군)도 60%가 차이 근거에 걸렸다.
# 포스터에서도 같은 크기면 근거를 그대로 믿기 어려우므로 결과에 표시하고 사람 확인을 안내한다(판정은 바꾸지 않는다).
SMALL_CROP_PX = 60
MIN_CROP_PX = 4


def crop_box(poster: Image.Image, box, source: str) -> Image.Image:
    """포스터 원본 좌표 상자를 자른다. 후보 상자는 탐지기 상자라 빠듯할 수 있어 영상과 같이 8% 넓힌다(직접 지정은 그대로)."""
    return V.crop_at(poster, box, list(poster.size), source)


def poster_wording(text: str) -> str:
    """영상 2부 근거 문구의 비교 대상 이름(영상)을 포스터로 바꾼다. 수치·판단 기준은 그대로다."""
    return text.replace(", 영상 ", ", 포스터 ")


def location_evidence(result: dict, crop: Image.Image) -> dict:
    """대조 결과 1건에 crop 크기·선명도와 작은 영역 표시를 붙인다."""
    return {"outcome": result["outcome"], "findings": [poster_wording(t) for t in result["findings"]], "kinds": result["kinds"],
            "elements": result["elements"], "ocr": result["ocr"], "color": result["color"],
            "size": list(crop.size), "sharp": round(V.sharpness(crop), 1), "small": min(crop.size) < SMALL_CROP_PX}


def compare_local(ref_image: Image.Image, ref_text: str, crops: list[Image.Image], progress=lambda done: None,
                  comparer=None) -> list[dict]:
    """이 프로세스에서 원본 로고 1장 대 crop 여러 장을 대조한다(RunPod 워커가 없을 때, 또는 테스트 주입)."""
    reference = V.reference(ref_image, ref_text)
    out = []
    for i, crop in enumerate(crops):
        out.append((comparer or V.compare)(crop, reference)); progress(i + 1)
    return out
