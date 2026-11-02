"""Development-only, in-memory LangGraph workflow.

All runs and approvals disappear when this Workflow instance is discarded or the
process exits. This is an integration scaffold, not a durable worker, an identity
provider, or evidence that a real model/database was executed. The service that
adopts it must implement authorization and persistence at its own boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from threading import RLock
from time import perf_counter_ns
from typing import Any, TypeVar, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .contracts import (
    Approval, AssetRef, Context, CriteriaDraft, DocumentRef, EvidenceBundle,
    ExtractRequest, FrameworkError, InspectionResult, InspectRequest, ReportDraft,
    ReportRequest, RunSnapshot, StepRecord, StepTimeline, ValidatedResult, ValidateRequest,
)
from .ports import AdapterFailure, Agents
from .invocation import ROLE_CONTRACTS, InvocationFailure, invoke_role
from .validation import (
    validate_draft,
)

T = TypeVar('T', bound=BaseModel)
_EXCEPTION_BLOCKERS = frozenset({
    'missing_observation', 'missing_expected_value', 'missing_requirement_evidence', 'missing_citation',
    'uncertain_observation', 'unsupported_criterion', 'context_incomplete',
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
    # Commands keep lock for the entire graph; readers never wait on adapters.
    view_lock: Any = field(default_factory=RLock)
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
        nodes = [('A04_A05', self._inspect),
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
        with record.view_lock:
            record.snapshot = _boundary(RunSnapshot, record.snapshot.model_copy(update=updates))

    @staticmethod
    def _step_error(exc: Exception) -> FrameworkError:
        # AdapterFailure is an explicitly safe error channel, but execution
        # telemetry only needs a bounded code; never echo arbitrary messages.
        code = exc.code if isinstance(exc, AdapterFailure) else 'ADAPTER_FAILED'
        if not isinstance(code, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', code) is None:
            code = 'ADAPTER_FAILED'
        unknown = isinstance(exc, AdapterFailure) and exc.outcome_unknown is True
        return FrameworkError(code=code,
            message='The role did not produce an accepted result. Inspect the adapter in your development environment.',
            retryable=isinstance(exc, AdapterFailure) and exc.retryable is True,
            outcome_unknown=unknown)

    def _run_stage(self, record: _Run, role: str, field_name: str, request_factory,
                   **final_updates: Any) -> None:
        """Record a whole role boundary, including request/result validation.

        Only the command owner writes. Result, terminal timing, and run state
        become visible atomically. Approval wait time is not adapter duration.
        """
        started = datetime.now(timezone.utc)
        clock = perf_counter_ns()
        step = StepRecord(ordinal=len(record.snapshot.step_records) + 1, role=role,
                          status='running', recorded_at=started, started_at=started,
                          synthetic=record.snapshot.synthetic)
        self._save(record, step_records=record.snapshot.step_records + (step,))
        try:
            request = request_factory()
            adapter = getattr(self.agents, ROLE_CONTRACTS[role][0])
            result = invoke_role(role, adapter, request)
            # Build a prospective snapshot before changing state. A malformed
            # output must never leave a completed marker or partially stored result.
            synthetic = record.snapshot.synthetic or result.synthetic
            result = _boundary(type(result), result.model_copy(update={'synthetic': synthetic}))
            updates = {field_name: result, 'synthetic': synthetic,
                       'stages': record.snapshot.stages + (role,), **final_updates}
            if role == 'A06':
                updates['machine_verdict'] = result.verdict
            with record.view_lock:
                _boundary(RunSnapshot, record.snapshot.model_copy(update=updates))
                terminal = step.model_copy(update={'status': 'completed', 'synthetic': synthetic,
                    'finished_at': max(started, datetime.now(timezone.utc)),
                    'duration_ms': max(0, perf_counter_ns() - clock) / 1_000_000})
                self._save(record, **updates,
                           step_records=record.snapshot.step_records[:-1] + (terminal,))
        except Exception as exc:
            error = self._step_error(exc)
            terminal = step.model_copy(update={
                'status': 'outcome_unknown' if error.outcome_unknown else 'failed',
                'finished_at': max(started, datetime.now(timezone.utc)),
                'duration_ms': max(0, perf_counter_ns() - clock) / 1_000_000,
                'error': error})
            with record.view_lock:
                self._save(record, step_records=record.snapshot.step_records[:-1] + (terminal,))
                self._failed(record, exc)
            raise

    def _failed(self, record: _Run, exc: Exception) -> RunSnapshot:
        if isinstance(exc, InvocationFailure):
            # Keep the public workflow failure category stable while standalone
            # role callers receive the more specific shared boundary code.
            error = FrameworkError(code='contract_violation',
                message='The stage failed. Inspect the adapter contract before an explicit new run.')
        elif isinstance(exc, AdapterFailure):
            error = self._step_error(exc)
        else:
            # Do not include arbitrary provider errors or invalid payloads in output.
            error = FrameworkError(code='contract_violation' if isinstance(exc, ValueError) else 'adapter_failed',
                                   message='The stage failed. Inspect the adapter contract before an explicit new run.')
        self._save(record, execution_status='outcome_unknown' if error.outcome_unknown else 'failed', error=error)
        return _copy(record.snapshot)

    def start(self, context: Context, document: DocumentRef, asset: AssetRef, *, defer=False) -> RunSnapshot:
        context = _boundary(Context, context)
        document = _boundary(DocumentRef, document)
        asset = _boundary(AssetRef, asset)
        if context.parent_run_id is not None:
            raise ValueError('use rereview to create a child run')
        self._input_scope(context, document, asset)
        record = _Run(document, asset, RunSnapshot(context=context, profile=self.profile,
            synthetic=False, execution_status='queued' if defer else 'extracting', stages=('A01',)))
        # Reserve the run before calling an adapter. Readers may observe its
        # explicit extracting state, while competing commands remain serialized.
        with record.lock:
            with self._registry_lock:
                if context.run_id in self._runs:
                    raise ValueError('run_id already exists')
                self._runs[context.run_id] = record
            if defer:
                return _copy(record.snapshot)
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
        with record.view_lock:
            return _copy(record.snapshot)

    def get_steps(self, run_id: str) -> StepTimeline:
        record = self._record(run_id)
        with record.view_lock:
            state = record.snapshot
            return _copy(StepTimeline(context=state.context, profile=state.profile,
                                     execution_status=state.execution_status, records=state.step_records))

    def restore(self, snapshot: RunSnapshot, document: DocumentRef, asset: AssetRef,
                *, child_run_id: str | None = None) -> None:
        """Load a trusted durable snapshot without invoking any adapter.

        The caller owns DB authorization/transactions. This method only enforces
        contract isolation and fixed input scope; it is not an import API.
        """
        snapshot = _boundary(RunSnapshot, snapshot)
        document, asset = _boundary(DocumentRef, document), _boundary(AssetRef, asset)
        self._input_scope(snapshot.context, document, asset)
        if snapshot.criteria is not None:
            validate_draft(snapshot.context, document, snapshot.criteria)
        with self._registry_lock:
            if snapshot.context.run_id in self._runs:
                raise ValueError('run_id already exists')
            self._runs[snapshot.context.run_id] = _Run(document, asset, snapshot, child_run_id=child_run_id)

    def approve(self, run_id: str, approval: Approval, *, defer=False) -> RunSnapshot:
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
                from .context import freeze_approved_context
                evidence=freeze_approved_context(state.context,state.criteria.criteria,synthetic=state.synthetic)
                self._save(record, approvals=state.approvals + (approval,), evidence=evidence, execution_status='queued' if defer else 'reviewing')
                if defer:
                    return _copy(record.snapshot)
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
            raise ValueError('incomplete context cannot be approved as an exception')
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
                stages=('A08', 'A01', 'A02_draft_reused'),
                step_records=(StepRecord(ordinal=1, role='A02', status='reused',
                    recorded_at=datetime.now(timezone.utc), source_run_id=parent_run_id,
                    synthetic=old.synthetic),)))
            with self._registry_lock:
                if new_context.run_id in self._runs:
                    raise ValueError('run_id already exists')
                self._runs[new_context.run_id] = child
                parent.child_run_id = new_context.run_id
            return _copy(child.snapshot)

    def _extract(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        self._run_stage(record, 'A02', 'criteria', lambda: ExtractRequest(
            context=_copy(record.snapshot.context), document=_copy(record.document),
            synthetic=record.snapshot.synthetic), execution_status='waiting_criteria')
        return {}

    def _inspect_request(self, record: _Run) -> InspectRequest:
        return InspectRequest(context=_copy(record.snapshot.context), criteria=_copy(record.snapshot.criteria).criteria,
                              synthetic=record.snapshot.synthetic,
                              asset=_copy(record.asset), evidence=_copy(record.snapshot.evidence))

    def _inspect(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        self._run_stage(record, 'A04_A05', 'inspection', lambda: self._inspect_request(record))
        return {}

    def _validate(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        self._run_stage(record, 'A06', 'validated', lambda: ValidateRequest(
            **self._inspect_request(record).model_dump(), inspection=_copy(record.snapshot.inspection)))
        return {}

    def _report(self, state: _GraphState) -> dict:
        record = self._record(state['run_id'])
        self._run_stage(record, 'A07', 'report', lambda: ReportRequest(
            context=_copy(record.snapshot.context), validated=_copy(record.snapshot.validated),
            synthetic=record.snapshot.synthetic, evidence=_copy(record.snapshot.evidence)),
            execution_status='waiting_review')
        return {}
