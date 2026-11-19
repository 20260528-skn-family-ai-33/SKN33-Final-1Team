"""Project source-validated model paragraphs into the existing review brief DTO."""
import re

from backend.app.documents.conditions import render_condition
from backend.app.security.errors import AuthError

IMAGE_PAGE=re.compile(r'page_(\d+)_embedded_images_not_read')
INCOMPLETE_PAGE=re.compile(r'page_(\d+)_model_output_incomplete')


def present_requirements(snapshot):
    draft=snapshot.criteria
    if draft is None or draft.extraction is None:
        raise AuthError('REQUIREMENTS_NOT_READY',409)
    if draft.extraction.policy!='a02-semantic-v4':
        raise AuthError('REQUIREMENTS_PRESENTATION_NOT_AVAILABLE',409)
    pending=set(draft.extraction.unresolved_criterion_ids)
    notes=[];conditions=[];grouped={}
    for index,item in enumerate(draft.criteria,1):
        source=item.source
        if item.criterion_id in pending:
            notes.append({'criterion_id':item.criterion_id,'page':source.page,'source':source.quote})
            continue
        display=item.details.presentation if item.details is not None else None
        if display is None:raise AuthError('REQUIREMENTS_PRESENTATION_NOT_AVAILABLE',409)
        label,value=display.label.strip(),display.value.strip()
        inspection='\n'.join(dict.fromkeys(q.quote for q in item.details.qualifiers))
        if len(inspection)>1200:raise AuthError('REQUIREMENTS_PRESENTATION_TOO_LONG',422)
        entry={'label':label,'requirement':value,'level':'필수' if item.required else '명시 없음',
            'inspection':inspection,'type':'visual' if item.details.target=='poster_visual' else 'information',
            'page':source.page,'source':source.quote,'source_kind':'a02_qwen',
            'source_references':[{'source_id':f'p{source.page}-s{index}','page':source.page,'source':source.quote[:350]}],
            'criterion_ids':[item.criterion_id]}
        entry['text']=render_condition(entry)
        if len(entry['text'])>500:raise AuthError('REQUIREMENTS_PRESENTATION_TOO_LONG',422)
        # Group only identical model paragraphs with the same target/source.
        # Keep every underlying criterion and citation (e.g. a complete date
        # range or width/height chosen together), never distinct venue values.
        key=(entry['text'],item.details.target,source.block_id,source.quote)
        if key in grouped:
            existing=grouped[key]
            existing['criterion_ids'].append(item.criterion_id)
            existing['source_references'].extend(entry['source_references'])
            existing['inspection']='\n'.join(dict.fromkeys(filter(None,[existing['inspection'],inspection])))
            if len(existing['inspection'])>1200:raise AuthError('REQUIREMENTS_PRESENTATION_TOO_LONG',422)
            continue
        grouped[key]=entry
        conditions.append(entry)
    if len(conditions)>100 or len('\n\n'.join(c['text'] for c in conditions))>6000:
        raise AuthError('REQUIREMENTS_PRESENTATION_TOO_LONG',422)
    # 화면은 경고를 첫 문장(무슨 일인지)과 나머지(무엇을 하면 되는지)로 나눠 보인다(frontend DocumentInput.jsx).
    warnings=[f'미확정 {len(notes)}개는 조건 목록에 넣지 않았어요. 문서에 값이 아직 정해지지 않은 항목이에요.'] if notes else []
    if draft.extraction.rejected_candidates:
        count=len(draft.extraction.rejected_candidates)
        warnings.append(f'원문과 맞지 않는 후보 {count}개를 뺐어요. 빠진 사유는 기준 확인 화면에서 볼 수 있어요.')
    image_pages=[m.group(1) for m in map(IMAGE_PAGE.fullmatch,draft.warnings) if m]
    if image_pages:
        warnings.append(f'{", ".join(image_pages)}쪽 이미지 속 글자는 읽지 않았어요. 이미지에 요구사항이 있으면 직접 추가해 주세요.')
    skipped=draft.extraction.skipped_pdf_pages
    if skipped:
        warnings.append(f'{", ".join(map(str,skipped))}쪽은 텍스트를 읽지 못했어요. 원본과 대조해 빠진 조건을 보완해 주세요.')
    incomplete=[m.group(1) for m in map(INCOMPLETE_PAGE.fullmatch,draft.warnings) if m]
    if incomplete:
        warnings.append(f'{", ".join(incomplete)}쪽의 일부 분석 응답을 받지 못했어요. 원본과 대조해 주세요.')
    return {'conditions':conditions,'pending':notes,'page_count':len(draft.extraction.sources)+len(skipped),'warnings':warnings,
        'framework':{'run_id':snapshot.context.run_id,'document_version_id':snapshot.context.document_version_id,
            'policy':draft.extraction.policy,'synthetic':draft.synthetic},'approved':False}
