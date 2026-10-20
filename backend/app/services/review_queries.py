"""Allowlist projections of immutable results; paginated evidence/OCR and bounded export."""
from datetime import datetime,timezone
from uuid import UUID
from sqlalchemy import select,func,and_,or_
from ..security.errors import AuthError
from ..persistence.transaction import unit_of_work
from .business import table,one,timestamp,safe_transaction
from .reviews import receipt


class ReviewQueries:
    def __init__(self,reviews):self.reviews=reviews;self.engine=reviews.engine;self.business=reviews.business;self.cursors=self.business.cursors

    def authorized(self,c,subject,dev,run_id):
        return self.business.authorization.resource(c,subject,'review_runs',run_id,developer_allowed=dev)

    def ocr(self,c,run):
        if not run['selected_attempt_id']:return None
        target=table('ocr_runs')
        row=c.execute(select(target).where(target.c.attempt_id==run['selected_attempt_id'],target.c.execution_status=='completed')).mappings().one_or_none()
        return dict(row) if row else None

    def evidence_dto(self,c,row):
        links=table('evidence_ocr_tokens')
        ids=c.execute(select(links.c.token_id).where(links.c.evidence_id==row['evidence_id']).order_by(links.c.token_order)).scalars().all()
        return dict(id=str(row['evidence_id']),source_kind=row['source_kind'],text=row['text'],bbox_xyxy=row['bbox_xyxy'],polygon=row['polygon'],confidence=row['confidence'],
            frame_id=str(row['frame_id']) if row['frame_id'] else None,coordinate_space='normalized_original_pixels' if row['frame_id'] else None,
            token_ids=[str(identifier) for identifier in ids],method_code=row['method_code'],limitations=row['limitations_jsonb'])

    def detail_dto(self,c,run):
        version=self.business.version_dto(c,one(c,'criteria_versions','criteria_version_id',run['criteria_version_id']))
        image=self.business.image_dto(c,one(c,'asset_versions','asset_version_id',run['asset_version_id']))
        results=[];runtime=None;ocr=None;extraction=None
        complete=run['execution_status'] in ('completed','imported') and run['sealed_at'] is not None and run['selected_attempt_id'] is not None
        if run['selected_attempt_id']:
            attempt=one(c,'review_attempts','attempt_id',run['selected_attempt_id'])
            # Runtime is projected from known adapter values; arbitrary source metadata stays private.
            actual=attempt['actual_runtime'] or {}
            runtime={key:actual[key] for key in ('engine','pipeline','device','execution_target','status') if key in actual}
            selections=table('run_item_selections');items=table('item_results');criteria=table('criteria_items')
            query=select(items,criteria.c.external_item_id,criteria.c.text.label('criterion'),criteria.c.category,criteria.c.position).join(selections,selections.c.result_id==items.c.result_id).join(criteria,criteria.c.criteria_item_id==items.c.criteria_item_id).where(selections.c.run_id==run['run_id']).order_by(criteria.c.position)
            rows=c.execute(query).mappings().all()
            for row in rows:
                ev=table('evidence_records');count=c.execute(select(func.count()).select_from(ev).where(ev.c.item_result_id==row['result_id'])).scalar_one()
                observed=row['observed_value']
                results.append(dict(result_id=str(row['result_id']),criteria_item_id=str(row['criteria_item_id']),criterion_id=row['external_item_id'],criterion=row['criterion'],
                    type=row['category'],processing_status=row['execution_status'],verdict=row['verdict'],status=row['verdict'] or 'review',
                    observed_text=observed if isinstance(observed,str) else None,observed_value=observed,reason=row['reason'],check_method=row['method_code'],
                    limitations=row['limitations_jsonb'],evidence=[],evidence_count=count,evidence_complete=count==0,
                    evidence_url='/api/v1/reviews/'+str(run['run_id'])+'/results/'+str(row['result_id'])+'/evidence'))
            complete=complete and len(rows)==len(version['criteria'])
            selected=self.ocr(c,run)
            if selected:
                tokens=table('ocr_tokens');count=c.execute(select(func.count()).select_from(tokens).where(tokens.c.ocr_run_id==selected['ocr_run_id'])).scalar_one()
                ocr=dict(id=str(selected['ocr_run_id']),record_count=count,records_url='/api/v1/reviews/'+str(run['run_id'])+'/ocr-records',
                    coordinate_space=selected['coordinate_space'],frame_id=str(selected['frame_id']),input_dimensions=selected['runtime_metadata'].get('input_dimensions'),scale=selected['runtime_metadata'].get('scale'))
                extractions=table('poster_extractions')
                extra=c.execute(select(extractions).where(extractions.c.ocr_run_id==selected['ocr_run_id'])).mappings().one_or_none()
                if extra:
                    observations=table('poster_field_observations')
                    fields=[]
                    for field in c.execute(select(observations).where(observations.c.extraction_id==extra['extraction_id']).order_by(observations.c.field_code)).mappings():
                        fields.append({key:field[key] for key in ('field_code','presence_status','resolution_status','raw_text','normalized_value')})
                    extraction=dict(id=str(extra['extraction_id']),compatibility_state=extra['compatibility_state'],validation_state=extra['validation_state'],fields=fields)
        return dict(**receipt(run),stage=run['current_stage'],progress=run['progress'],project_name=run['project_name_snapshot'],
            criteria_version=version['version_no'],origin_kind=run['origin_kind'],source=None if run['origin_kind']=='service_execution' else {'status':run['source_status']},
            criteria=version['criteria'],image=image,results=results,results_complete=complete,overall_verdict=run['overall_verdict'],
            aggregation_policy_version=run['aggregation_policy_version'],runtime=runtime,ocr=ocr,event_extraction=extraction,error=run['error_jsonb'],
            **{name:timestamp(run[name]) for name in ('created_at','updated_at','started_at','finished_at','sealed_at')})

    @safe_transaction
    def detail(self,subject,dev,run_id):
        with unit_of_work(self.engine) as c:return self.detail_dto(c,self.authorized(c,subject,dev,run_id))

    @safe_transaction
    def listing(self,subject,dev,pid,filters,limit,cursor):
        context=dict(kind='reviews',actor=str(subject.user_id),project=str(pid),filters=filters,limit=limit)
        last=self.cursors.read(cursor,context) if cursor else None
        with unit_of_work(self.engine) as c:
            self.business.project(c,subject,pid,dev)
            target=table('review_runs');query=select(target).where(target.c.project_id==pid)
            if filters.get('image_id'):
                self.business.same_project(c,'asset_versions','asset_version_id',UUID(filters['image_id']),pid)
                query=query.where(target.c.asset_version_id==UUID(filters['image_id']))
            if filters.get('status'):query=query.where(target.c.execution_status==filters['status'])
            if last:
                try:created=datetime.strptime(last[0],'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc);identifier=UUID(last[1])
                except (ValueError,TypeError):raise AuthError('CURSOR_INVALID',400) from None
                query=query.where(or_(target.c.created_at<created,and_(target.c.created_at==created,target.c.run_id<identifier)))
            rows=c.execute(query.order_by(target.c.created_at.desc(),target.c.run_id.desc()).limit(limit+1)).mappings().all()
            more=len(rows)>limit;rows=rows[:limit]
            items=[dict(**receipt(row),project_name=row['project_name_snapshot'],overall_verdict=row['overall_verdict'],created_at=timestamp(row['created_at'])) for row in rows]
            next_cursor=self.cursors.issue(context,[timestamp(rows[-1]['created_at']),str(rows[-1]['run_id'])]) if more else None
        return dict(items=items,has_more=more,next_cursor=next_cursor)

    @safe_transaction
    def page(self,subject,dev,run_id,kind,limit,cursor,result_id=None):
        with unit_of_work(self.engine) as c:
            run=self.authorized(c,subject,dev,run_id);ocr=self.ocr(c,run)
            if kind=='ocr':
                availability='available' if ocr else ('pending' if run['execution_status'] in ('queued','running') else 'none')
                scope=str(ocr['ocr_run_id']) if ocr else None
            else:
                result=one(c,'item_results','result_id',result_id)
                if result['run_id']!=run_id or result['attempt_id']!=run['selected_attempt_id']:raise AuthError('RESOURCE_NOT_FOUND',404)
                availability='available';scope=str(result_id)
            context=dict(kind=kind,actor=str(subject.user_id),project=str(run['project_id']),run=str(run_id),scope=scope,limit=limit)
            last=self.cursors.read(cursor,context) if cursor else None
            if kind=='ocr' and not ocr:return dict(items=[],has_more=False,next_cursor=None,availability=availability,record_count=0)
            target=table('ocr_tokens' if kind=='ocr' else 'evidence_records');pk=target.c.token_id if kind=='ocr' else target.c.evidence_id
            order=target.c.token_order if kind=='ocr' else pk
            query=select(target).where(target.c.ocr_run_id==ocr['ocr_run_id']) if kind=='ocr' else select(target).where(target.c.item_result_id==result_id)
            count=c.execute(select(func.count()).select_from(query.subquery())).scalar_one()
            if last:
                try:
                    value=int(last[0]) if kind=='ocr' else UUID(last[0]);identifier=UUID(last[1])
                except (ValueError,TypeError):raise AuthError('CURSOR_INVALID',400) from None
                query=query.where(or_(order>value,and_(order==value,pk>identifier)))
            rows=c.execute(query.order_by(order,pk).limit(limit+1)).mappings().all();more=len(rows)>limit;rows=rows[:limit]
            if kind=='ocr':
                items=[dict(id=str(row['token_id']),external_token_id=row['external_token_id'],external_token_type=row['external_token_type'],order=row['token_order'],
                    text=row['text'],confidence=row['confidence'],bbox_xyxy=row['bbox_xyxy'],polygon=row['polygon'],coordinate_space=ocr['coordinate_space'],frame_id=str(ocr['frame_id'])) for row in rows]
            else:items=[self.evidence_dto(c,row) for row in rows]
            next_cursor=self.cursors.issue(context,[rows[-1]['token_order'] if kind=='ocr' else str(rows[-1]['evidence_id']),str(rows[-1]['token_id'] if kind=='ocr' else rows[-1]['evidence_id'])]) if more else None
        return dict(items=items,has_more=more,next_cursor=next_cursor,availability=availability,**{'record_count' if kind=='ocr' else 'evidence_count':count})

    @safe_transaction
    def export(self,subject,dev,run_id):
        with unit_of_work(self.engine) as c:
            run=self.authorized(c,subject,dev,run_id);data=self.detail_dto(c,run)
            data.update(contract_version='cc-review-export-v1',exported_at=timestamp(datetime.now(timezone.utc)),input_manifest_hash=run['input_manifest_hash'],
                        result_manifest_hash=run['result_manifest_hash'],manifest_hash_version=run['manifest_hash_version'])
            evidence=table('evidence_records')
            for result in data['results']:
                result['evidence']=[self.evidence_dto(c,row) for row in c.execute(select(evidence).where(evidence.c.item_result_id==UUID(result['result_id'])).order_by(evidence.c.evidence_id)).mappings()]
                result['evidence_complete']=True
            source_ids=[]
            selected=self.ocr(c,run)
            if selected:
                source_ids.append(selected['source_file_id'])
                extraction=c.execute(select(table('poster_extractions')).where(table('poster_extractions').c.ocr_run_id==selected['ocr_run_id'])).mappings().one_or_none()
                if extraction:source_ids.append(extraction['source_file_id'])
            data['execution']={key:run[key] for key in ('workflow_version','aggregation_policy_version','execution_target')}
            config=run['requested_config'] or {}
            data['execution']['config']={key:config[key] for key in ('pipeline','model_name','model_revision','device_policy','code_revision','preprocessing') if key in config}
            data['ocr_records']=[]
            if selected:
                tokens=table('ocr_tokens')
                for row in c.execute(select(tokens).where(tokens.c.ocr_run_id==selected['ocr_run_id']).order_by(tokens.c.token_order,tokens.c.token_id)).mappings():
                    data['ocr_records'].append(dict(id=str(row['token_id']),external_token_id=row['external_token_id'],external_token_type=row['external_token_type'],
                        order=row['token_order'],text=row['text'],confidence=row['confidence'],bbox_xyxy=row['bbox_xyxy'],polygon=row['polygon'],
                        frame_id=str(selected['frame_id']),coordinate_space=selected['coordinate_space']))
            data['source_files']=[]
            for identifier in source_ids:
                row=one(c,'stored_files','file_id',identifier)
                data['source_files'].append(dict(id=str(identifier),sha256=row['sha256'],byte_size=str(row['byte_size']),embedded=False,
                    reason='private_source_not_embedded',availability='unverified'))
            return data
