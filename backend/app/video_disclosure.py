"""숏폼 검수 6-A — 광고·협찬 표시 판정(기준 문장이 아니라 업로드 때 받은 광고 입력 + 프레임 OCR).

근거: 추천·보증 등에 관한 표시·광고 심사지침(공정위 예규 제499호). 법률 자문이 아니다.
'3초'·'15자'·'R3c 한 번 이상'은 내부 기준이며 화면에 그렇게 표시한다.
문구 사전(R1)은 지침 예시로 한정한다 — 예시 밖 표현은 '모호'로 두고 확인 대상으로 남긴다.
"""
from __future__ import annotations

import re
import unicodedata

from .video_frames import EDGE_WINDOW_MS

KINDS = {"self", "endorsement", "partial", "unknown"}       # 광고주 자체 광고 · 전체 추천·보증 · 일부 구간 · 모름
TITLE_WINDOW = 15
SHORT_VIDEO_MS = 2 * EDGE_WINDOW_MS

ALLOWED = [re.compile(p) for p in (r"#\s*광고", r"#\s*협찬", r"유료\s*광고")]
EXCLUDED = [re.compile(p) for p in (r"^\s*협찬\s*[:：]", r"^\s*협찬\s*사\b")]     # 엔딩 크레딧 '협찬: ○○'
DISALLOWED = [re.compile(p, re.I) for p in (r"^\s*체험단\s*$", r"^\s*#?\s*AD\s*$", r"^\s*#?\s*sponsor(ed)?\s*$")]
AMBIGUOUS = re.compile(r"광고|협찬|체험단|\bAD\b|sponsor", re.I)
INTERNAL_NOTE = "'3초'·'15자'·'중간 한 번 이상'은 내부 기준이며 법령의 숫자 기준이 아닙니다."


def classify_phrase(text: str) -> str:
    """OCR 줄 하나를 허용·제외·불허·모호·무관으로 나눈다(제외 → 허용 → 불허 → 모호 순)."""
    text = unicodedata.normalize("NFC", text or "")
    if any(p.search(text) for p in EXCLUDED):
        return "excluded"
    if any(p.search(text) for p in ALLOWED):
        return "allowed"
    if any(p.search(text) for p in DISALLOWED):
        return "disallowed"
    if AMBIGUOUS.search(text):
        return "ambiguous"
    return "none"


def validate_input(value: dict | None) -> dict:
    value = dict(value or {})
    result = {
        "kind": value.get("kind", "unknown"), "celebrity_exposure": bool(value.get("celebrity_exposure", False)),
        "title": str(value.get("title") or "")[:500], "caption": str(value.get("caption") or "")[:5000],
        "platform": str(value.get("platform") or "")[:50],
    }
    if result["kind"] not in KINDS:
        raise ValueError("Invalid disclosure input")
    return result


def judge_disclosure(info: dict, frames: list[dict], lines: list[dict], duration_ms: int) -> dict:
    """프레임별 OCR 줄로 R0~R6을 보고 '표시 문구·시간'과 전체 광고 적합을 나눠 낸다."""
    if info["kind"] == "self":
        return {"applicable": False, "status": "not_applicable", "severity": "info", "reviewer": "auto",
                "reason": "광고주 자체 광고로 입력되어 추천·보증 표시 대상이 아닙니다.", "rules": {}, "notes": [INTERNAL_NOTE]}
    capped = info["kind"] == "unknown"
    status_of = {f["frame_id"]: f.get("ocr_status") for f in frames}
    hits = []
    for line in lines:
        kind = classify_phrase(line["text"])
        if kind != "none":
            hits.append({**{k: line[k] for k in ("frame_id", "time_ms", "text", "bbox_xyxy")}, "class": kind})
    allowed = [h for h in hits if h["class"] == "allowed"]

    def window(start, end, base_only=False):
        ids = [f["frame_id"] for f in frames if start <= f["time_ms"] < end and (not base_only or f["sampling"] == "base")]
        evidence = [h for h in allowed if h["frame_id"] in ids]
        ok = all(status_of[i] != "failed" for i in ids) and bool(ids)
        return {"status": "pass" if evidence else "review", "evidence": evidence[:5], "frames": len(ids), "ocr_ok": ok}

    rules = {"R0": {"status": "pass" if not capped else "review",
                    "note": "적용 대상 확정" if not capped else "영상 성격이 '모름'이라 모든 결과를 최대 '확인 필요'로 둡니다."}}
    disallowed = [h for h in hits if h["class"] == "disallowed"]
    rules["R1"] = {"status": "fail" if disallowed and not allowed else ("pass" if allowed else "review"),
                   "counts": {c: sum(1 for h in hits if h["class"] == c) for c in ("allowed", "disallowed", "ambiguous", "excluded")},
                   "evidence": (disallowed or [h for h in hits if h["class"] == "ambiguous"])[:5],
                   "note": "불허 문구만 있으면 위반 후보, 엔딩의 '협찬: ○○' 크레딧은 표시 문구로 보지 않습니다."}
    title = unicodedata.normalize("NFC", info["title"])
    head_title = title[:TITLE_WINDOW]
    rules["R2"] = {"status": "pass" if any(p.search(head_title) for p in ALLOWED)
                   else "review", "note": "제목 앞 15자 안의 허용 문구" + (
                       " — 뒤쪽에만 있어 모바일에서 잘릴 수 있습니다." if not any(p.search(head_title) for p in ALLOWED)
                       and any(p.search(title) for p in ALLOWED) else "")}
    short = duration_ms <= SHORT_VIDEO_MS
    if short:  # 6초 이하: 시작·끝 창이 겹치므로 하나로 합치고 중간 반복은 생략(문서 '짧은 영상' 규칙)
        merged = window(0, duration_ms)
        rules["R3a"] = rules["R3b"] = {**merged, "note": "6초 이하 영상 — 시작·끝 창을 합쳐 봅니다."}
        rules["R3c"] = {"status": "skipped", "note": "6초 이하 영상은 중간 반복 창이 없습니다."}
    else:
        rules["R3a"] = {**window(0, EDGE_WINDOW_MS), "note": "[0, 3초) 0.2초 프레임"}
        rules["R3b"] = {**window(duration_ms - EDGE_WINDOW_MS, duration_ms), "note": "[끝−3초, 끝) 0.2초 프레임"}
        middle = window(EDGE_WINDOW_MS, duration_ms - EDGE_WINDOW_MS, base_only=True)
        rules["R3c"] = {**middle, "note": "중간 1초 프레임에서 한 번 이상" + ("" if middle["evidence"] else " — 중간 반복 미확인")}
    caption_allowed = any(p.search(info["caption"]) for p in ALLOWED)
    any_screen = bool(allowed)
    rules["R4"] = {"status": "review" if caption_allowed and not any_screen and rules["R2"]["status"] != "pass" else "pass",
                   "note": "캡션에만 허용 문구가 있으면 확인 필요"}
    rules["R5"] = {"status": "review", "note": "글자 크기·대비·노출 시간(명확한 인식)은 후속 — 담당자 확인"}
    rules["R6"] = {"status": "review", "note": "AI 가상인물 등장 여부는 후속 — 담당자 확인"}

    if info["celebrity_exposure"] and not short:
        screen_ok = rules["R3a"]["status"] == "pass" and rules["R3b"]["status"] == "pass"
    else:
        screen_ok = all(rules[r]["status"] in ("pass", "skipped") for r in ("R3a", "R3b", "R3c"))
    text_time = "pass" if rules["R2"]["status"] == "pass" or screen_ok else "review"

    head_tail_ok = rules["R3a"].get("ocr_ok") and rules["R3b"].get("ocr_ok")
    no_trace = not allowed and not any(p.search(title) for p in ALLOWED) and not caption_allowed
    # 허용 밖의 '모호한' 표시(예: '광고'·'협찬받았습니다')가 화면에 있으면 자동 위반으로 확정하지 않는다.
    # 지침 예시 밖 표현이라 위반인지 단정할 수 없으므로 사람 확인 대상('확인 필요')으로 남긴다.
    ambiguous_present = any(h["class"] == "ambiguous" for h in hits)
    violation = (not capped and (info["title"] or info["caption"]) and head_tail_ok and no_trace
                 and not ambiguous_present)
    if violation:
        status, reason = "fail", "적용 대상 영상인데 제목·캡션·화면 어디에도 허용 표시 문구가 없습니다."
    elif ambiguous_present and no_trace and not capped and (info["title"] or info["caption"]) and head_tail_ok:
        status = "review"
        reason = "허용 표시 문구는 없지만 모호한 표시(예: '광고'·'협찬')가 있어 자동 위반으로 확정하지 않고 확인 필요로 둡니다."
    else:
        status = "review"
        reason = ("표본에서 표시 문구·시간은 확인했지만, 시인성(R5)·가상인물(R6)이 남아 전체 광고 적합은 확인 필요입니다."
                  if text_time == "pass" else "필수 표시를 모두 확인하지 못했습니다. 중간 반복 미확인 등은 위반으로 올리지 않습니다.")
    return {"applicable": True, "text_time_status": text_time, "status": status,
            "severity": "block" if status == "fail" else "warn", "reviewer": "auto" if status == "fail" else "manager",
            "reason": reason, "rules": rules, "notes": [INTERNAL_NOTE, "표본 문구 확인과 표시 규정 충족은 다른 상태입니다."]}
