"""로고 검수 파이프라인 — 조합 A(OWLv2 탐지 + DINO 동일성 대조).

두 역할:
  - LogoDetector(OWLv2): 올린 기준 로고를 쿼리 이미지로 넣어 영상 프레임 안에서 '같은 그림'의 위치를 찾는다
    (image-guided detection). 학습이 필요 없다.
  - LogoMatcher(DINO/CLIP/SigLIP): 크롭 한 장을 임베딩 벡터로 바꾼다. 142개 기준 로고를 미리 임베딩해 두고,
    코사인 유사도로 '어느 브랜드인지/정말 같은지'를 확인한다.

모든 추론은 RunPod GPU Pod에서 돈다. CUDA가 있으면 GPU, 없으면 CPU로 떨어진다(로컬 점검용).
모델은 먼저 scripts/prepare_logo_models.py 로 .runtime/logo-models/ 에 받아 둬야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from . import logo_models as reg

# 알파(투명) 로고를 흰 배경에 합성할 때 쓰는 배경색. 기준 로고와 크롭을 같은 방식으로 다룬다.
_NEUTRAL_BG = (255, 255, 255)


def _device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_rgb(path_or_image):
    """경로나 PIL 이미지를 받아 알파를 흰 배경에 합성한 RGB로 돌려준다(원본 좌표 보존)."""
    from PIL import Image
    image = Image.open(path_or_image) if not isinstance(path_or_image, Image.Image) else path_or_image
    if image.mode == "RGB":
        return image
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (*_NEUTRAL_BG, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def trim_to_content(image, tol: int = 12, pad: float = 0.04):
    """흰(또는 투명→흰) 여백을 잘라 로고 내용만 남긴다. 기준 로고와 탐지 크롭의 프레이밍을 맞춰
    매칭 정확도를 높이기 위한 전처리다. 내용이 없으면 원본을 돌려준다."""
    from PIL import Image, ImageChops
    rgb = load_rgb(image)
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, _NEUTRAL_BG)).convert("L")
    bbox = diff.point(lambda p: 255 if p > tol else 0).getbbox()
    if not bbox:
        return rgb
    x0, y0, x1, y1 = bbox
    w, h = rgb.size
    dx, dy = int((x1 - x0) * pad), int((y1 - y0) * pad)
    return rgb.crop((max(0, x0 - dx), max(0, y0 - dy), min(w, x1 + dx), min(h, y1 + dy)))


# ---------------------------------------------------------------------------
# 동일성 대조(matching)
# ---------------------------------------------------------------------------
class LogoMatcher:
    """기준 로고/크롭을 L2 정규화된 임베딩으로 바꾼다. match 모델 1개를 GPU에 올린다."""

    def __init__(self, key: str = "dinov2", device: str | None = None):
        spec = reg.spec(key)
        if spec.role != "match":
            raise ValueError(f"{key} 는 match 모델이 아닙니다(role={spec.role}).")
        if not reg.is_prepared(key):
            raise FileNotFoundError(
                f"{key} 모델이 아직 없습니다. 먼저: python scripts/prepare_logo_models.py --models {key}")
        self.key = key
        self.spec = spec
        self.device = device or _device()
        self._load()

    def _load(self):
        import torch
        from transformers import AutoImageProcessor, AutoModel

        source = str(reg.model_dir(self.key))
        self.processor = AutoImageProcessor.from_pretrained(source)
        self.model = AutoModel.from_pretrained(source).to(self.device).eval()
        self._torch = torch

    def embed(self, image) -> "list[float]":
        """이미지 1장 → 정규화 임베딩(list). 배치가 필요하면 embed_many 를 쓴다."""
        return self.embed_many([image])[0]

    def embed_many(self, images) -> list:
        import numpy as np
        # 기준 로고와 탐지 크롭 모두 여백을 잘라 프레이밍을 맞춘다(매칭 변별력↑).
        rgb = [trim_to_content(img) for img in images]
        inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            if self.spec.embed == "image_features":
                # CLIP/SigLIP 계열: 투영된 이미지 특징
                features = self.model.get_image_features(**inputs)
            else:
                # DINO 계열: pooler_output 우선, 없으면 CLS 토큰
                output = self.model(**inputs)
                features = getattr(output, "pooler_output", None)
                if features is None:
                    features = output.last_hidden_state[:, 0]
        vectors = features.float().cpu().numpy()
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vectors / norms).tolist()


# ---------------------------------------------------------------------------
# 위치 탐지(detection)
# ---------------------------------------------------------------------------
@dataclass
class Box:
    xyxy: list  # [x0, y0, x1, y1] (원본 프레임 픽셀)
    score: float


class LogoDetector:
    """OWLv2 image-guided detection: 기준 로고(쿼리)와 같은 그림을 대상 프레임에서 찾는다."""

    def __init__(self, key: str = "owlv2", device: str | None = None):
        spec = reg.spec(key)
        if spec.role != "detect":
            raise ValueError(f"{key} 는 detect 모델이 아닙니다(role={spec.role}).")
        if not reg.is_prepared(key):
            raise FileNotFoundError(
                f"{key} 모델이 아직 없습니다. 먼저: python scripts/prepare_logo_models.py --models {key}")
        self.key = key
        self.device = device or _device()
        self._load()

    def _load(self):
        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        source = str(reg.model_dir(self.key))
        self.processor = Owlv2Processor.from_pretrained(source)
        self.model = Owlv2ForObjectDetection.from_pretrained(source).to(self.device).eval()
        self._torch = torch

    def guided_detect(self, target, query_logo, threshold: float = 0.9, max_boxes: int = 20) -> list[Box]:
        """대상 프레임에서 쿼리 로고와 닮은 영역을 찾는다. 점수 높은 순으로 돌려준다."""
        target_rgb = load_rgb(target)
        query_rgb = load_rgb(query_logo)
        inputs = self.processor(images=target_rgb, query_images=query_rgb,
                                return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            outputs = self.model.image_guided_detection(**inputs)
        sizes = self._torch.tensor([(target_rgb.height, target_rgb.width)], device=self.device)
        result = self.processor.post_process_image_guided_detection(
            outputs=outputs, threshold=threshold, target_sizes=sizes)[0]
        boxes = [Box([round(float(v), 1) for v in box.tolist()], round(float(score), 4))
                 for box, score in zip(result["boxes"], result["scores"])]
        boxes.sort(key=lambda b: b.score, reverse=True)
        return boxes[:max_boxes]

    # 기본 프롬프트: 찾을 로고를 모를 때 '로고 같은' 영역을 연다(open-vocabulary).
    TEXT_PROMPTS = ["a logo", "an emblem", "a brand symbol", "a watermark", "a logo mark"]

    def text_detect(self, target, prompts: list[str] | None = None,
                    threshold: float = 0.1, max_boxes: int = 20) -> list[Box]:
        """쿼리 로고 없이 텍스트 프롬프트로 '로고 같은' 영역을 찾는다(영상에서 미지의 로고 탐지).
        OWLv2 텍스트 점수는 낮게 나오므로 임계값 기본 0.1. 찾은 영역은 매처로 식별한다."""
        prompts = prompts or self.TEXT_PROMPTS
        target_rgb = load_rgb(target)
        inputs = self.processor(text=[prompts], images=target_rgb, return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            outputs = self.model(**inputs)
        sizes = self._torch.tensor([(target_rgb.height, target_rgb.width)], device=self.device)
        result = self.processor.post_process_object_detection(
            outputs=outputs, threshold=threshold, target_sizes=sizes)[0]
        boxes = [Box([round(float(v), 1) for v in box.tolist()], round(float(score), 4))
                 for box, score in zip(result["boxes"], result["scores"])]
        boxes.sort(key=lambda b: b.score, reverse=True)
        return boxes[:max_boxes]


def crop(image, xyxy, pad: float = 0.08, min_side: int = 24):
    """탐지 상자를 약간 여유를 두고 잘라 매처에 넘길 크롭을 만든다.
    얇은 상자(한두 픽셀)는 이미지 프로세서를 깨뜨리므로 최소 변 길이를 보장한다."""
    rgb = load_rgb(image)
    w, h = rgb.size
    x0, y0, x1, y1 = xyxy
    dx, dy = (x1 - x0) * pad, (y1 - y0) * pad
    x0, y0, x1, y1 = x0 - dx, y0 - dy, x1 + dx, y1 + dy
    if x1 - x0 < min_side:
        cx = (x0 + x1) / 2
        x0, x1 = cx - min_side / 2, cx + min_side / 2
    if y1 - y0 < min_side:
        cy = (y0 + y1) / 2
        y0, y1 = cy - min_side / 2, cy + min_side / 2
    box = (int(max(0, min(x0, w - min_side))), int(max(0, min(y0, h - min_side))),
           int(min(w, max(x1, min_side))), int(min(h, max(y1, min_side))))
    if box[0] >= box[2] or box[1] >= box[3]:
        return rgb
    return rgb.crop(box)


# ---------------------------------------------------------------------------
# 기하 이중검수(조합 B) — LightGlue
# ---------------------------------------------------------------------------
class LogoVerifier:
    """이중검수: DISK 지역특징 + LightGlue 기하 매칭으로 '같은 로고'인지 구조적으로 확인한다.

    임베딩 코사인은 '의미가 비슷한지'를, LightGlue는 '구조·위치가 맞는지'를 본다(서로 다른 원리).
    코사인이 애매한(0.6~0.85) 구간만 2차로 돌려 오탐을 줄인다. 점수 = 매칭 개수(많을수록 같은 로고).
    쿼리 로고 특징은 query_features로 한 번만 뽑아 프레임마다 재사용한다.
    """

    def __init__(self, device: str | None = None, n_feats: int = 1024, side: int = 256):
        import torch
        import kornia.feature as KF

        self.device = device or _device()
        self.n_feats = n_feats
        self.side = side
        self._torch = torch
        self._KF = KF
        self.disk = KF.DISK.from_pretrained("depth").to(self.device).eval()
        self.matcher = KF.LightGlueMatcher("disk").to(self.device).eval()

    def _tensor(self, image):
        import numpy as np
        from kornia import image_to_tensor
        rgb = load_rgb(image).resize((self.side, self.side))
        return (image_to_tensor(np.asarray(rgb), keepdim=False).float() / 255.0).to(self.device)

    def _features(self, image):
        with self._torch.inference_mode():
            feats = self.disk(self._tensor(image), self.n_feats, pad_if_not_divisible=True)[0]
        return feats.keypoints, feats.descriptors

    def query_features(self, query_logo):
        """쿼리 로고의 (keypoints, descriptors). 프레임마다 다시 계산하지 않도록 캐시해 둔다."""
        return self._features(query_logo)

    def count_matches(self, crop_image, query_features) -> int:
        """크롭 한 장과 미리 뽑아 둔 쿼리 특징 사이의 LightGlue 매칭 개수."""
        kp_q, desc_q = query_features
        kp_c, desc_c = self._features(crop_image)
        if kp_c.shape[0] < 4 or kp_q.shape[0] < 4:  # 특징이 너무 적으면 매칭 불가
            return 0
        lafs_c = self._KF.laf_from_center_scale_ori(kp_c[None])
        lafs_q = self._KF.laf_from_center_scale_ori(kp_q[None])
        with self._torch.inference_mode():
            _, idxs = self.matcher(desc_c, desc_q, lafs_c, lafs_q)
        return int(idxs.shape[0])


# ---------------------------------------------------------------------------
# 기준 로고 인덱스(선계산 임베딩)
# ---------------------------------------------------------------------------
class ReferenceIndex:
    """scripts/precompute_logo_embeddings.py 가 만든 142개 기준 임베딩을 읽어 최근접 브랜드를 찾는다."""

    def __init__(self, matcher_key: str = "dinov2", index_name: str | None = None):
        import numpy as np
        name = index_name or matcher_key
        directory = reg.index_dir(name)
        matrix_path, meta_path = directory / "embeddings.npy", directory / "brands.json"
        if not matrix_path.is_file() or not meta_path.is_file():
            raise FileNotFoundError(
                f"'{name}' 기준 임베딩이 없습니다. 먼저 precompute_logo_embeddings.py 로 인덱스를 만드세요.")
        self.matrix = np.load(matrix_path)  # (N, D), 이미 L2 정규화됨
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.brands = self.meta["brands"]
        if self.matrix.shape[0] != len(self.brands):
            raise ValueError("임베딩 행 수와 브랜드 수가 다릅니다.")

    def nearest(self, vector, topk: int = 5) -> list[dict]:
        import numpy as np
        query = np.asarray(vector, dtype=self.matrix.dtype)
        scores = self.matrix @ query  # 정규화 벡터의 내적 = 코사인 유사도
        order = np.argsort(-scores)[:topk]
        return [{"brand": self.brands[i], "cosine": round(float(scores[i]), 4)} for i in order]
