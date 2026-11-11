"""숏폼 검수 6-A 결과(result.json)를 근거 관계도용 노드·관계로 바꾼다.

저장된 완료 결과만 읽는 순수 함수다. 모델·OCR을 다시 부르지 않고 판정도 새로 만들지 않는다.
Neo4j를 거치지 않으므로 응답에 answered_by='result_json'으로 표시한다. 영상 결과는 아직
PostgreSQL 원장·그래프 투영(agent_framework.graph_projection)에 올라가지 않는다.

관측(observation)은 화면에서 읽힌 표시 문구를 (분류, 문구)로 묶은 것이다. 판정 때와 같은
classify_phrase로 저장된 프레임 줄을 다시 분류하므로, 문구 사전이 그 뒤 바뀌었으면 저장된 R1
집계와 달라질 수 있다. 그때는 warnings에 PHRASE_LEXICON_CHANGED를 남긴다(판정은 그대로 둔다).
"""
from __future__ import annotations

from hashlib import sha1

from backend.app.video.disclosure import classify_phrase

ANSWERED_BY = "result_json"
OBSERVATION_LIMIT = 40   # 관측 노드 수 상한. 넘으면 truncated=True
OCCURRENCE_LIMIT = 20    # 관측 하나에 싣는 위치(프레임·bbox) 수 상한
GUIDELINE = "guideline:ftc-endorsement"   # disclosure.py가 근거로 밝힌 지침
RULES = {  # internal: 숫자 기준('3초'·'15자'·'중간 한 번 이상')이 지침이 아닌 내부 기준인 규칙
    "R0": ("적용 대상", False), "R1": ("표시 문구", False), "R2": ("제목 앞 15자", True),
    "R3a": ("시작 3초 표시", True), "R3b": ("끝 3초 표시", True), "R3c": ("중간 반복 표시", True),
    "R4": ("캡션만 표시", False), "R5": ("시인성", False), "R6": ("AI 가상인물", False),
}
INPUTS = {"kind": "영상 성격", "title": "제목", "caption": "캡션"}
INPUT_RULES = {"R0": "kind", "R2": "title", "R4": "caption"}
SCREEN_RULES = ("R1", "R3a", "R3b", "R3c")   # 화면 문구를 보는 규칙
CLASS_LABELS = {"allowed": "허용", "disallowed": "불허", "ambiguous": "모호", "excluded": "제외"}
PRIMARY = ("disallowed", "ambiguous", "allowed", "excluded")   # R1 근거 사슬에서 먼저 보여 줄 분류 순서


class GraphNotReady(Exception):
    """완료되지 않았거나 광고·협찬 판정이 없는 검수 기록."""


class UnknownRule(Exception):
    """이 검수 결과에 없는 판정 규칙."""


def _clock(ms: int) -> str:
    return f"{ms // 60000:02d}:{ms % 60000 / 1000:04.1f}"


def _observation_id(kind: str, text: str) -> str:
    return f"observation:{kind}:{sha1(text.encode()).hexdigest()[:10]}"


class _Builder:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}

    def node(self, node_id: str, kind: str, label: str, **extra) -> str:
        self.nodes.setdefault(node_id, {"id": node_id, "kind": kind, "label": label, **extra})
        return node_id

    def edge(self, source: str, target: str, kind: str, rule: str | None = None, dashed: bool = False) -> None:
        edge = self.edges.setdefault((source, target, kind), {"source": source, "target": target, "kind": kind,
                                                               "dashed": dashed, "rules": []})
        if rule and rule not in edge["rules"]:
            edge["rules"].append(rule)


def build_evidence_graph(job: dict, rule: str | None = None) -> dict:
    """완료된 검수 결과 → {nodes, edges, verdicts, ...}. rule을 주면 그 규칙의 관계만 남긴다."""
    disclosure = job.get("disclosure")
    if job.get("status") != "completed" or not isinstance(disclosure, dict):
        raise GraphNotReady
    rules = disclosure.get("rules") or {}
    if rule is not None and rule not in rules:
        raise UnknownRule(rule)
    info, media, frames = job.get("disclosure_input") or {}, job.get("media") or {}, job.get("frames") or []
    b, warnings = _Builder(), []

    run = b.node(f"run:{job['id']}", "run", "검수 실행", detail=(job.get("runtime") or {}).get("pipeline"),
                 created_at=job.get("created_at"))
    video = b.node(f"video:{str(media.get('sha256', ''))[:12]}", "video", "영상",
                   detail=_clock(media.get("duration_ms", 0)), sha256=media.get("sha256"),
                   duration_ms=media.get("duration_ms"))
    b.edge(run, video, "inspects")

    # 화면 문구 관측: 판정과 같은 분류기로 저장된 프레임 줄을 다시 나눈다.
    frame_of = {f["frame_id"]: f for f in frames}
    groups: dict[str, dict] = {}
    counts = {c: 0 for c in CLASS_LABELS}
    for frame in frames:
        for line in frame.get("lines", []):
            kind = classify_phrase(line["text"])
            if kind == "none":
                continue
            counts[kind] += 1
            group = groups.setdefault(_observation_id(kind, line["text"]), {
                "class": kind, "text": line["text"], "count": 0, "occurrences": []})
            group["count"] += 1
            if len(group["occurrences"]) < OCCURRENCE_LIMIT:
                group["occurrences"].append({"frame_id": frame["frame_id"], "time_ms": frame["time_ms"],
                                             "bbox_xyxy": line["bbox_xyxy"]})
    stored = (rules.get("R1") or {}).get("counts")
    if stored is not None and any(stored.get(c, 0) != counts[c] for c in CLASS_LABELS):
        warnings.append("PHRASE_LEXICON_CHANGED")
    ordered = sorted(groups, key=lambda key: (PRIMARY.index(groups[key]["class"]),
                                              groups[key]["occurrences"][0]["time_ms"], key))
    truncated = len(ordered) > OBSERVATION_LIMIT

    def frame_node(frame_id: str) -> str:
        frame = frame_of[frame_id]
        return b.node(f"frame:{frame_id}", "frame", f"프레임 {_clock(frame['time_ms'])}", frame_id=frame_id,
                      time_ms=frame["time_ms"], sampling=frame.get("sampling"), width=frame.get("width"),
                      height=frame.get("height"))

    def observe(key: str, at: dict, name: str, relation: str) -> str:
        group = groups[key]
        observation = b.node(key, "observation", group["text"], detail=CLASS_LABELS[group["class"]],
                             phrase_class=group["class"], count=group["count"], occurrences=group["occurrences"],
                             occurrences_truncated=group["count"] > len(group["occurrences"]))
        frame = frame_node(at["frame_id"])
        b.edge(video, frame, "has_frame", name)
        b.edge(observation, frame, "located_at", name)
        b.edge(observation, f"verdict:{name}", relation, name, dashed=relation == "excluded")
        return observation

    human = "human:disclosure"
    verdicts = []
    for name, result in rules.items():
        title, internal = RULES.get(name, (name, False))
        verdict = b.node(f"verdict:{name}", "verdict", f"판정 {name}", rule=name, status=result.get("status"),
                         note=result.get("note"))
        rule_node = b.node(f"rule:{name}", "rule", f"{name} {title}", rule=name, internal_standard=internal)
        b.node(GUIDELINE, "guideline", "추천·보증 등에 관한 표시·광고 심사지침", detail="공정거래위원회 예규 제499호")
        b.edge(run, verdict, "judged", name)
        b.edge(verdict, rule_node, "applies_rule", name)
        b.edge(rule_node, GUIDELINE, "based_on", name)
        status = result.get("status")
        lead, missing = [run], False   # lead: 근거 사슬의 판정 앞부분
        if name == "R1":
            # R1은 허용 문구가 하나라도 있어야 pass다(disclosure.py). 저장된 상태를 그대로 따른다.
            missing = status in ("review", "fail")
            for key in ordered[:OBSERVATION_LIMIT]:
                group = groups[key]
                relation = "excluded" if group["class"] == "excluded" else "evidence"
                observation = observe(key, group["occurrences"][0], name, relation)
                if lead == [run] and relation == "evidence":
                    lead = [video, f"frame:{group['occurrences'][0]['frame_id']}", observation]
        elif name in SCREEN_RULES:
            missing = status == "review" and not result.get("evidence")
            for hit in result.get("evidence", []):
                if hit["frame_id"] not in frame_of:
                    continue
                key = _observation_id(hit.get("class", "allowed"), hit["text"])
                # 저장된 근거가 지금 분류와 다르면(문구 사전 변경) 저장된 근거를 그대로 싣는다.
                groups.setdefault(key, {"class": hit.get("class", "allowed"), "text": hit["text"], "count": 1,
                                        "occurrences": [{k: hit[k] for k in ("frame_id", "time_ms", "bbox_xyxy")}]})
                observation = observe(key, hit, name, "evidence")
                if lead == [run]:
                    lead = [video, f"frame:{hit['frame_id']}", observation]
        elif name in INPUT_RULES:
            field = INPUT_RULES[name]
            source = b.node(f"input:{field}", "input", INPUTS[field], field=field, value=info.get(field))
            b.edge(source, verdict, "input", name)
            lead = [source]
        if missing:
            absent = b.node("phrase:allowed", "phrase_class", "허용 문구", detail="이번 관측 0건")
            b.edge(verdict, absent, "absent", name, dashed=True)
        if status == "review":
            b.node(human, "human_review", "담당자 확인", detail="결정 대기")
            b.edge(verdict, human, "review_requested", name, dashed=True)
        chain = lead + [verdict, rule_node, GUIDELINE]
        verdicts.append({"id": verdict, "rule": name, "title": title, "status": result.get("status"),
                         "note": result.get("note"), "internal_standard": internal, "chain": chain})

    edges = list(b.edges.values())
    if rule is not None:
        edges = [edge for edge in edges if rule in edge["rules"] or (edge["source"], edge["target"]) == (run, video)]
        verdicts = [item for item in verdicts if item["rule"] == rule]
    used = {run, video} | {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    nodes = [node for node in b.nodes.values() if node["id"] in used]
    return {
        "job_id": job["id"], "answered_by": ANSWERED_BY, "scope": "disclosure", "rule": rule,
        "applicable": bool(disclosure.get("applicable")),
        "disclosure": {key: disclosure.get(key) for key in ("status", "text_time_status", "severity", "reviewer", "reason")},
        "verdicts": verdicts, "nodes": nodes, "edges": edges,
        "counts": {"nodes": len(nodes), "edges": len(edges), "observations": counts},
        "truncated": truncated, "warnings": warnings, "notes": disclosure.get("notes", []),
    }
