"""Run real offline OCR and MCP against development samples on either OS.

Does not write inspection history or download models. The tiny sample expectations
below are regression checks, not an independent model accuracy benchmark.
"""
from pathlib import Path
import argparse
import json
import os
import platform
import sys
import time
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUNTIME = Path(os.environ.get('LATTE_RUNTIME_DIR', ROOT / '.runtime/ocr-smoke-runtime')).resolve()
os.environ['LATTE_RUNTIME_DIR'] = str(RUNTIME)

from app.inspector import get_capabilities, inspect_image
from app.mcp_client import inspect_via_mcp
from app.ocr import method_name


def main():
    parser = argparse.ArgumentParser(description='Actual offline OCR and MCP regression check.')
    parser.add_argument('--expect-device', choices=('cpu', 'cuda', 'mps'))
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    report = {'platform': platform.platform(), 'python': platform.python_version(),
              'scope': '실제 로컬 OCR·MCP 시연 검증, 범용 정확도 평가 아님',
              'capabilities': get_capabilities(), 'samples': [], 'passed': False}
    output = ROOT / '.runtime/ocr-smoke-report.json'
    output.parent.mkdir(exist_ok=True)
    try:
        assert report['capabilities']['ocr_backend'] == 'rapidocr', '공통 엔진 rapidocr로 실행하세요.'
        assert report['capabilities']['ocr_available'], '의존성과 모델을 먼저 준비하세요.'
        rules = json.loads((ROOT / 'data/criteria.json').read_text(encoding='utf-8'))['rules']
        for sample, discount, verdict in [('sample-01.png', '30% 할인', '충족'), ('sample-02.png', '20% 할인', '미충족')]:
            started = time.monotonic()
            # A hidden model/dictionary download during inference must fail this check.
            with patch('rapidocr.utils.download_file.DownloadFile.run', side_effect=RuntimeError('Inference must not download')):
                result = inspect_image(ROOT / 'data/samples' / sample, rules)
            report['samples'].append({'sample': sample, 'seconds': round(time.monotonic() - started, 3), **result})
            by_id = {row['rule_id']: row for row in result['results']}
            actual = result['ocr'].get('device')
            assert result['method'] == method_name(actual)
            if args.expect_device:
                assert actual == args.expect_device, result['ocr'].get('fallback_reason', 'Unexpected device')
            assert by_id['C01']['observed'] == '딸기라떼'
            assert by_id['C03']['observed'] == 'MORNING CUP'
            assert by_id['C02']['observed'].replace(' ', '') == discount.replace(' ', '')
            assert by_id['C02']['verdict'] == verdict
            assert all(by_id[key]['execution_status'] == 'not_run' for key in ('C04', 'C05'))
            for line in result['ocr']['lines']:
                x, y, width, height = line['bbox']
                assert 0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1
                assert x + width <= 1.001 and y + height <= 1.001
            print(f'{sample}: 할인율 {discount} → {verdict}', flush=True)
        # Exercise a Korean filename and non-default runtime through a real MCP child.
        uploads = RUNTIME / 'uploads'
        uploads.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='한글 경로 ', dir=uploads) as directory:
            image = Path(directory) / '할인 이미지 02.png'
            image.write_bytes((ROOT / 'data/samples/sample-02.png').read_bytes())
            result = inspect_via_mcp(image, rules)
        report['mcp'] = result
        assert result['method'] == method_name(result['ocr'].get('device'))
        if args.expect_device:
            assert result['ocr'].get('device') == args.expect_device, result['ocr'].get('fallback_reason')
        assert result['results'][1]['observed'].replace(' ', '') == '20%할인'
        assert any(row['method'] == 'tools/call' and row['status'] == 'succeeded' for row in result['trace'])
        report['passed'] = True
        print('실제 공통 OCR 2건·MCP 호출 검증 통과.', flush=True)
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(report['error'], file=sys.stderr)
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Report: {output}')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
