"""포스터 일반 객체 상자(OWLv2 텍스트 탐지). A04/A05 시각 기준의 판정 근거 후보를 만든다.

로고 후보 탐지와 같은 모델(google/owlv2-base-patch16, backend/app/video/logos/models.owl)을 같은 RunPod 워커에서 쓴다.
팀 시험(2026-11-17~18)과 같은 방식: 포스터 한 장을 통째로 넣고(타일 없음) 일반 어휘로 찾은 뒤
점수 문턱(기본 0.1)을 넘는 상자를 분류와 상관없이 NMS 0.5로 합친다.
  - detect(image)          : 모델이 있는 프로세스(워커 kind="poster_objects" 또는 GPU Pod 안 백엔드)에서 돈다
  - request_objects(image) : 백엔드에서 부른다. VIDEO_LOGO_WORKER_URL이 있으면 워커로, local=True면 이 프로세스에서
좌표는 넣은 이미지의 픽셀 [x1, y1, x2, y2]다.
"""
from __future__ import annotations

from PIL import Image

from backend.agent_framework.visual_objects import OWL_MAX_BOXES, OWL_NMS_IOU, OWL_THRESHOLD, OWL_VOCABULARY

INPUT_SIDE = 960  # OWLv2 base patch16 입력 한 변. 처리기가 어차피 줄이므로 미리 줄여 큰 포스터의 CPU 리사이즈를 피한다


def detect(image: Image.Image, *, vocabulary=OWL_VOCABULARY, threshold: float = OWL_THRESHOLD) -> dict:
    import torch
    from torchvision.ops import nms

    from backend.app.video.logos import models as M

    image = image.convert("RGB")
    k = INPUT_SIDE / max(image.size)
    small = image.resize((max(1, round(image.width * k)), max(1, round(image.height * k))), Image.LANCZOS) if k < 1 else image
    scale = 1 / k if k < 1 else 1.0
    proc, model = M.owl()
    inputs = proc(text=[list(vocabulary)], images=[small], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model(**inputs)
    res, = proc.image_processor.post_process_object_detection(out, threshold=threshold,
                                                              target_sizes=[(small.height, small.width)])
    boxes, scores, labels = res["boxes"].float().cpu() * scale, res["scores"].float().cpu(), res["labels"].cpu()
    objects = []
    if len(scores):
        keep = nms(boxes, scores, OWL_NMS_IOU)
        keep = keep[scores[keep].argsort(descending=True)[:OWL_MAX_BOXES]]
        objects = [{"label": vocabulary[int(labels[i])], "score": round(float(scores[i]), 4),
                    "bbox": [round(v, 1) for v in boxes[i].tolist()]} for i in keep]
    return {"size": list(image.size), "threshold": threshold, "objects": objects}


def request_objects(image: Image.Image, *, threshold: float = OWL_THRESHOLD, local: bool = False) -> dict | None:
    """OWLv2 상자를 돌려준다. 워커 주소가 없고 local도 아니면 None(건너뜀)이다. 워커 실패는 WorkerError로 올린다."""
    from backend.app.video.logos.client import WorkerError, _encode, _run, worker_url

    if local:
        return detect(image, threshold=threshold)
    if not worker_url():
        return None
    payload = {"kind": "poster_objects", "threshold": threshold,
               "poster": {"jpg_base64": _encode(image.convert("RGB"), "JPEG")}}
    data = _run(payload, lambda p, d: None)
    if (not isinstance(data, dict) or not isinstance(data.get("objects"), list)
            or data.get("size") != list(image.size)):
        raise WorkerError("검수 서버의 포스터 객체 결과 형식이 올바르지 않습니다.")
    return data
