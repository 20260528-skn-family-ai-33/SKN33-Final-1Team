"""Explicit local Paddle adapter. No legacy API import or automatic download/fallback."""
import hashlib
from pathlib import Path
from ..security.errors import AuthError

BASE=Path(__file__).resolve().parents[1]


def code_revision():
    names=('review_rules.py','extract_event.py','event_layout.py','paddle_ocr.py','paddle_full_worker.py','execution/output.py','execution/pipeline.py','services/review_results.py',
           'execution/queue.py','execution/worker_config.py','worker.py')
    hasher=hashlib.sha256()
    for name in names:
        hasher.update(name.encode());hasher.update((BASE/name).read_bytes())
    return hasher.hexdigest()


class DisabledPipeline:
    def describe(self): raise AuthError('REVIEW_RUNTIME_UNAVAILABLE',503)


class PaddlePipeline:
    """Readiness is not inference success; both are checked at their real boundary."""
    def describe(self):
        from ..paddle_ocr import paddle_readiness,paddle_paths
        from scripts.prepare_paddle import DETECTION_MODEL,MODEL,verify_model
        if not paddle_readiness()[0]: raise AuthError('REVIEW_RUNTIME_UNAVAILABLE',503)
        _,detection,recognition=paddle_paths()
        def identity(path,name):
            actual=verify_model(path,name)
            return {key:actual[key] for key in ('model','repository','revision','sha256')}
        return dict(pipeline='paddle_full_v1',model_name=MODEL,
                    model_revision={'detection':identity(detection,DETECTION_MODEL),'recognition':identity(recognition,MODEL)},
                    device_policy=['cpu'],execution_target='local',code_revision=code_revision(),
                    preprocessing={'version':'cc-display-2x-lanczos-v1','ocr_scale':2})

    def recognize(self,path,directory):
        from ..paddle_ocr import recognize_paddle
        return recognize_paddle(path,directory)

    def check(self,criteria,records):
        from ..review_rules import review_criteria
        from ..event_layout import extract_event_multiline
        # The existing extractor uses tuples for date ranges in groups.values.
        # Project internal sequences without casting unsupported/unknown values.
        def project(value):
            if isinstance(value,dict): return {key:project(item) for key,item in value.items()}
            if isinstance(value,(list,tuple)): return [project(item) for item in value]
            return value
        return review_criteria(criteria,records,multiline=True),project(extract_event_multiline(records))
