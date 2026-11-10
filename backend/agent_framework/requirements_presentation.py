"""Project source-validated model paragraphs into the existing review brief DTO."""
from backend.app.documents.conditions import render_condition
from backend.app.security.errors import AuthError


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
    warnings=['확인 중인 항목은 검수 입력에서 분리했습니다.'] if notes else []
    if draft.extraction.rejected_candidates:
        count=len(draft.extraction.rejected_candidates)
        warnings.append(f'원문 검증에 실패한 후보 {count}개를 제외했습니다. 기준 확인 화면에서 사유를 확인해 주세요.')
    return {'conditions':conditions,'pending':notes,'page_count':len(draft.extraction.sources),'warnings':warnings,
        'framework':{'run_id':snapshot.context.run_id,'document_version_id':snapshot.context.document_version_id,
            'policy':draft.extraction.policy,'synthetic':draft.synthetic},'approved':False}
