"""Development-only, in-memory LangGraph workflow.

All runs and approvals disappear when this Workflow instance is discarded or the
process exits. This is an integration scaffold, not a durable worker, an identity
provider, or evidence that a real model/database was executed. The service that
adopts it must implement authorization and persistence at its own boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, TypeVar, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .contracts import (
    Approval, AssetRef, Context, CriteriaDraft, DocumentRef, EvidenceBundle,
    ExtractRequest, FrameworkError, InspectionResult, InspectRequest, ReportDraft,
    ReportRequest, RetrieveRequest, RunSnapshot, ValidatedResult, ValidateRequest,
)
from .ports import AdapterFailure, Agents
from .validation import (
    validate_draft, validate_evidence, validate_inspection, validate_report,
    validate_validated,
)

T = TypeVar('T', bound=BaseModel)
_EXCEPTION_BLOCKERS = frozenset({
    'missing_observation', 'missing_requirement_evidence', 'missing_citation',
    'uncertain_observation', 'unsupported_criterion', 'retrieval_incomplete',
    'numeric_observation_invalid', 'verdict_conflict',
})


def _boundary(contract: type[T], value: Any) -> T:
    """Revalidate even model_construct/model_copy values; do not trust frozen models."""
    try:
        if isinstance(value, BaseModel):
            if not isinstance(value, contract):
                raise ValueError('unexpected contract type')
            value = value.model_dump(mode='python')
        return contract.model_validate(value).model_copy(deep=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'invalid {contract.__name__} contract') from exc


def _copy(value: T) -> T:
    return value.model_copy(deep=True)


class _GraphState(TypedDict):
    run_id: str


@dataclass
class _Run:
    document: DocumentRef
    asset: AssetRef
    snapshot: RunSnapshot
    lock: Any = field(default_factory=RLock)
    child_run_id: str | None = None


class Workflow:
    """Synchronous adapters behind replaceable contracts and real LangGraph nodes.

    Different runs may execute concurrently. A per-run lock serializes approvals;
    repeated or stale commands raise ValueError and leave existing runs unchanged.
    Adapter/output failures close the affected run; no adapter is auto-retried.
    """

    persistence = 'memory_only'

    def __init__(self, agents: Agents, profile: str = 'development'):
        if profile not in ('development', 'test'):
            raise ValueError('Workflow supports development/test only; memory is not production persistence')
        self.agents = agents
        self.profile = profile
        self._runs: dict[str, _Run] = {}
        self._registry_lock = RLock()
        extraction = StateGraph(_GraphState)
        extraction.add_node('A02', self._extract)
        extraction.add_edge(START, 'A02')
        extraction.add_edge('A02', END)
        self._extraction_graph = extraction.compile()
        review = StateGraph(_GraphState)
        nodes = [('A03', self._retrieve), ('A04_A05', self._inspect),
                 ('A06', self._validate), ('A07', self._report)]
        for name, fn in nodes:
            review.add_node(name, fn)
        for left, right in zip([START] + [name for name, _ in nodes],
                               [name for name, _ in nodes] + [END]):
            review.add_edge(left, right)
        self._review_graph = review.compile()

    def _record(self, run_id: str) -> _Run:
        with self._registry_lock:
            record = self._runs.get(run_id)
        if record is None:
            raise KeyError('unknown run; this runtime has no persistent storage')
        return record

    def _save(self, record: _Run, **updates: Any) -> None:
        record.snapshot = _boundary(RunSnapshot, record.snapshot.model_copy(update=updates))

    def _output(self, record: _Run, stage: str, field_name: str, value: T) -> None:
        synthetic = record.snapshot.synthetic or bool(getattr(value, 'synthetic', False))
        value = _boundary(type(value), value.model_copy(update={'synthetic': synthetic}))
        self._save(record, **{field_name: value}, synthetic=synthetic,
                   stages=record.snapshot.stages + (stage,))

    def _failed(self, record: _Run, exc: Exception) -> RunSnapshot:
        if isinstance(exc, AdapterFailure):
            error = FrameworkError(code=exc.code, message=exc.message,
                                   retryable=exc.retryable, outcome_unknown=exc.outcome_unknown)
        else:
            # Do not include arbitrary provider errors or invalid payloads in output.
            error = FrameworkError(code='contract_violation' if isinstance(exc, ValueError) else 'adapter_failed',
                                   message='The stage failed. Inspect the adapter contract before an explicit new run.')
        self._save(record, execution_status='outcome_unknown' if error.outcome_unknown else 'failed', error=error)
        return _copy(record.snapshot)

    def start(self, context: Context, document: DocumentRef, asset: AssetRef) -> RunSnapshot:
        context = _boundary(Context, context)
        document = _boundary(DocumentRef, document)
        asset = _boundary(AssetRef, asset)
        if context.parent_run_id is not None:
            raise ValueError('use rereview to create a child run')
        self._input_scope(context, document, asset)
        record = _Run(document, asset, RunSnapshot(context=context, profile=self.profile,
            synthetic=False, execution_status='waiting_criteria', stages=('A01',)))
        # Publish only after holding this record's lock: get cannot observe a partial start.
        with record.lock:
            with self._registry_lock:
                if context.run_id in self._runs:
                    raise ValueError('run_id already exists')
                self._runs[context.run_id] = record
            try:
                self._extraction_graph.invoke({'run_id': context.run_id}, {'recursion_limit': 8})
            except Exception as exc:
                return self._failed(record, exc)
            return _copy(record.snapshot)

    @staticmethod
    def _input_scope(context: Context, document: DocumentRef, asset: AssetRef) -> None:
        if context.document_version_id != document.document_version_id:
            raise ValueError('document version differs from context')
        if context.asset_version_id != asset.asset_version_id:
            raise ValueError('asset version differs from context')

    def get(self, run_id: str) -> RunSnapshot:
        record = self._record(run_id)
        with record.lock:
            return _copy(record.snapshot)

    def approve(self, run_id: str, approval: Approval) -> RunSnapshot:
        approval = _boundary(Approval, approval)
        record = self._record(run_id)
        with record.lock:
            state = record.snapshot
            if approval.context != state.context or approval.actor_id != state.context.actor_id:
                raise ValueError('approval actor/context does not match the exact run snapshot')
            if any(item.decision == approval.decision for item in state.approvals):
                raise ValueError('approval already recorded')
            decision = approval.decision
            if decision == 'approve_criteria':
                if state.execution_status != 'waiting_criteria' or state.criteria is None:
                    raise ValueError('criteria approval is not available')
                self._save(record, approvals=state.approvals + (approval,))
                try:
                    self._review_graph.invoke({'run_id': run_id}, {'recursion_limit': 12})
                except Exception as exc:
                    return self._failed(record, exc)
            else:
                if state.execution_status != 'waiting_review' or state.validated is None or state.report is None:
                    raise ValueError('review approval is not available')
                if decision == 'approve_changes':
                    self._save(record, execution_status='waiting_revision', human_decision='changes_approved',
                               approvals=state.approvals + (approval,))
                elif decision == 'approve_final':
                    if state.machine_verdict != 'pass':
                        raise ValueError('normal final approval requires a pass; exceptions need explicit scope')
                    self._save(record, execution_status='completed', human_decision='final_approved',
                               approvals=state.approvals + (approval,))
                elif decision == 'approve_exception':
                    self._exception_scope(state, approval)
                    self._save(record, execution_status='completed', human_decision='exception_approved',
                               approvals=state.approvals + (approval,))
            return _copy(record.snapshot)

    @staticmethod
    def _exception_scope(state: RunSnapshot, approval: Approval) -> None:
        if state.criteria is None or state.validated is None or state.evidence is None:
            raise ValueError('validated evidence is required for an exception')
        if state.evidence.status != 'complete':
            raise ValueError('incomplete retrieval cannot be approved as an exception')
        if any(_EXCEPTION_BLOCKERS.intersection(item.issues) for item in state.validated.items):
            raise ValueError('unknown or unsupported evidence cannot be approved as an exception')
        # Custom A06 adapters cannot silently turn new uncertainty codes into an
        # exception path. New eligible issue codes require a shared policy change.
        if any(item.verdict == 'review' or set(item.issues) - {'value_mismatch'}
               for item in state.validated.items):
            raise ValueError('unknown or unsupported evidence cannot be approved as an exception')
        ids = set(approval.criterion_ids)
        criteria = {item.criterion_id: item for item in state.criteria.criteria}
        if len(ids) != len(approval.criterion_ids) or not ids or ids - criteria.keys():
            raise ValueError('exception targets must be unique existing criteria')
        required = {item.criterion_id for item in state.validated.items
                    if criteria[item.criterion_id].required and item.verdict != 'pass'}
        if not required or not required.issubset(ids):
            raise ValueError('exception must cover every non-pass required criterion')

    def rereview(self, parent_run_id: str, new_context: Context, asset: AssetRef) -> RunSnapshot:
        new_context = _boundary(Context, new_context)
        asset = _boundary(AssetRef, asset)
        parent = self._record(parent_run_id)
        with parent.lock:
            old = parent.snapshot
            if old.execution_status != 'waiting_revision' or old.human_decision != 'changes_approved':
                raise ValueError('an approved change request is required before rereview')
            if parent.child_run_id is not None:
                raise ValueError('this change request already has a child run')
            if (new_context.parent_run_id != parent_run_id or new_context.run_id == parent_run_id
                    or new_context.project_id != old.context.project_id
                    or new_context.document_version_id != old.context.document_version_id
                    or new_context.criteria_version_id != old.context.criteria_version_id
                    or asset.asset_version_id == parent.asset.asset_version_id):
                raise ValueError('rereview requires the same project/document/criteria and a new run/asset version')
            self._input_scope(new_context, parent.document, asset)
            if old.criteria is None:
                raise ValueError('parent has no reusable criteria draft')
            draft = _boundary(CriteriaDraft, old.criteria.model_copy(update={'context': new_context}))
            validate_draft(new_context, parent.document, draft)
            child = _Run(_copy(parent.document), asset, RunSnapshot(context=new_context, profile=self.profile,
                synthetic=old.synthetic, execution_status='waiting_criteria', criteria=draft,
                stages=('A08', 'A01', 'A02_draft_reused')))
            with self._registry_lock:
                if new_context.run_id in self._runs:
                    raise ValueError('run_id already exists')
                self._runs[new_context.run_id] = child
                parent.child_run_id = new_context.run_id
            return _copy(child.snapshot)

    def _extract(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        context = record.snapshot.context
        request = ExtractRequest(context=_copy(context), document=_copy(record.document), synthetic=record.snapshot.synthetic)
        result = _boundary(CriteriaDraft, self.agents.extractor.extract(request))
        validate_draft(context, record.document, result)
        self._output(record, 'A02', 'criteria', result)
        return {}

    def _retrieve(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        request = RetrieveRequest(context=_copy(record.snapshot.context), criteria=_copy(record.snapshot.criteria).criteria,
                                  synthetic=record.snapshot.synthetic)
        result = _boundary(EvidenceBundle, self.agents.retriever.retrieve(_copy(request)))
        validate_evidence(request, result)
        self._output(record, 'A03', 'evidence', result)
        return {}

    def _inspect_request(self, record: _Run) -> InspectRequest:
        return InspectRequest(context=_copy(record.snapshot.context), criteria=_copy(record.snapshot.criteria).criteria,
                              synthetic=record.snapshot.synthetic,
                              asset=_copy(record.asset), evidence=_copy(record.snapshot.evidence))

    def _inspect(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        request = self._inspect_request(record)
        result = _boundary(InspectionResult, self.agents.inspector.inspect(_copy(request)))
        validate_inspection(request, result)
        self._output(record, 'A04_A05', 'inspection', result)
        return {}

    def _validate(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        request = ValidateRequest(**self._inspect_request(record).model_dump(), inspection=_copy(record.snapshot.inspection))
        result = _boundary(ValidatedResult, self.agents.validator.validate(_copy(request)))
        validate_validated(request, result)
        self._output(record, 'A06', 'validated', result)
        self._save(record, machine_verdict=result.verdict)
        return {}

    def _report(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        request = ReportRequest(context=_copy(record.snapshot.context), validated=_copy(record.snapshot.validated),
                                synthetic=record.snapshot.synthetic,
                                evidence=_copy(record.snapshot.evidence))
        result = _boundary(ReportDraft, self.agents.reporter.report(_copy(request)))
        validate_report(request, result)
        self._output(record, 'A07', 'report', result)
        self._save(record, execution_status='waiting_review')
        return {}
