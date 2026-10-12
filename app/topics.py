"""Predeclared demo orders and input images. Reviewer answers are never loaded."""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

from app.contracts import normalize_rules

DEFAULT_TOPIC = 'strawberry-latte'


class TopicCatalog:
    def __init__(self, criteria_file, samples, topics_dir=None):
        self.entries = {DEFAULT_TOPIC: ('딸기라떼', Path(criteria_file), Path(samples))}
        if topics_dir is not None:
            for topic, label in [('festival-v1', '페스티벌'), ('croissant-v2', '크루아상 v2'),
                                 ('fnb-ongi-v1', '온기식탁(F&B)'), ('festival-dalbit-v1', '달빛 페스타(페스티벌)'),
                                 ('virtual-seoha-v1', '가상인물 서하')]:
                folder = Path(topics_dir) / topic
                self.entries[topic] = (label, folder / 'criteria.json', folder / 'images')

    def entry(self, topic):
        if topic not in self.entries:
            raise HTTPException(404, detail={'code': 'TOPIC_NOT_FOUND', 'message': '등록되지 않은 주제입니다.'})
        return self.entries[topic]

    def sample_paths(self, topic):
        _, _, folder = self.entry(topic)
        return {p.stem: p for p in sorted(folder.iterdir())
                if p.is_file() and p.name.startswith('sample-') and p.suffix == '.png'}

    def criteria(self, topic, condition_id=None):
        label, path, _ = self.entry(topic)
        raw = path.read_bytes()
        data = json.loads(raw)
        try:
            data['rules'] = normalize_rules(data['rules'], keep_legacy_id=True)
        except (KeyError, ValueError, TypeError):
            raise HTTPException(422, detail={'code': 'INVALID_CRITERIA',
                                           'message': '검수 기준의 항목 ID가 유효하지 않습니다.'})
        conditions = data.get('sample_conditions', {})
        if condition_id is not None and condition_id not in conditions:
            raise HTTPException(404, detail={'code': 'CONDITION_NOT_FOUND', 'message': '등록되지 않은 발주 조건입니다.'})
        if condition_id:
            for rule in data['rules']:
                if rule['rule_id'] in conditions[condition_id]:
                    counts = conditions[condition_id][rule['rule_id']]
                    rule['expected'] = f"큰 크루아상 {counts['large']}개 · 미니 {counts['mini']}개 · 총 {counts['total']}개"
                    rule['expected_counts'] = counts
        elif conditions:
            for rule in data['rules']:
                if rule['rule_id'] == 'C04':
                    rule['expected'] = '발주 조건 선택 후 수량 표시'
        # Preserve old strawberry confirmation versions and API clients.
        version_input = raw if topic == DEFAULT_TOPIC else raw + json.dumps([topic, condition_id]).encode()
        version = hashlib.sha256(version_input).hexdigest()[:16]
        return {**data, 'version': version, 'topic_id': topic, 'topic_label': label,
                'condition_id': condition_id, 'can_confirm': not conditions or condition_id is not None,
                'condition_options': [
                    {'id': key, 'label': '발주 ' + key.rsplit('-', 1)[-1],
                     'expected': f"큰 {value['C04']['large']}개 · 미니 {value['C04']['mini']}개"}
                    for key, value in conditions.items()]}

    def list(self):
        return [{'id': topic, 'label': label, 'sample_count': len(self.sample_paths(topic))}
                for topic, (label, _, _) in self.entries.items()]
