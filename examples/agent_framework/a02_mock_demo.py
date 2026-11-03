"""Read one explicitly selected PDF locally; mock wiring only, no DB or model."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4

from backend.agent_framework.a02 import RegisteredPDFExtractor
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.contracts import Context,DocumentRef,ExtractRequest
from backend.agent_framework.files import VerifiedDocument,inspect_document
from backend.agent_framework.invocation import invoke_role
from backend.agent_framework.ports import AdapterFailure


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',required=True,type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    print('SYNTHETIC A02 MOCK: manual-only fixture; no Qwen, database or human approval.',file=sys.stderr)
    try:
        with args.pdf.open('rb') as stream:raw=stream.read(20*1024*1024+1)
        pages=inspect_document(raw)
        context=Context(project_id=str(uuid4()),actor_id=str(uuid4()),run_id=str(uuid4()),
            document_version_id=str(uuid4()),criteria_version_id=str(uuid4()),asset_version_id=None)
        reference=DocumentRef(file_id=str(uuid4()),document_version_id=context.document_version_id,
            sha256=sha256(raw).hexdigest(),page_count=pages)
        document=VerifiedDocument(context,reference,raw,pages)
        result=invoke_role('A02',RegisteredPDFExtractor(lambda _:document,PDFExtractionConfiguration(mode='mock')),
            ExtractRequest(context=context,document=reference,synthetic=True))
        if args.output:args.output.write_text(result.model_dump_json(indent=2)+'\n',encoding='utf-8')
        # ASCII on stdout remains parseable in Windows redirection as well.
        print(json.dumps(result.model_dump(mode='json'),ensure_ascii=True,indent=2))
        return 0
    except AdapterFailure as error:
        print(json.dumps({'error':{'code':error.code,'message':error.message}}));return 1
    except (OSError,ValueError):
        print('{"error":{"code":"A02_DEMO_INPUT_UNAVAILABLE"}}');return 2


if __name__=='__main__':raise SystemExit(main())
