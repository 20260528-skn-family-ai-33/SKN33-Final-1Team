"""A01/A08 agent output contracts. Drafts come from the model; notes add code facts."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..contracts import Contract, ExecutionStatus, FrameworkError, Identifier, Verdict

NextAction = Literal['wait_for_worker', 'request_criteria_approval', 'request_final_approval', 'request_human_review',
                     'request_change_approval', 'request_exception_approval', 'submit_revision', 'submit_poster',
                     'start_new_run', 'reconcile_manually', 'none']


class CoordinationDraft(Contract):
    """What the A01 model answers."""
    next_action: NextAction
    summary: str = Field(min_length=1, max_length=2000)
    blockers: tuple[str, ...] = ()


class CoordinationNote(Contract):
    """A01 result: model judgment checked against the run state read by code.

    `summary` is written by code from the facts. `model_*` fields are the model's
    own words and are not fact-checked; show them labelled as model text.
    """
    role: Literal['A01'] = 'A01'
    run_id: Identifier
    execution_status: ExecutionStatus
    machine_verdict: Verdict | None = None
    waiting_on: Literal['worker', 'human', 'creator', 'none']
    next_action: NextAction
    summary: str = Field(min_length=1, max_length=2000)
    model_summary: str = Field(min_length=1, max_length=2000)
    model_blockers: tuple[str, ...] = ()
    tools_used: tuple[str, ...] = ()
    synthetic: bool = False


class RereviewDraft(Contract):
    """What the A08 model answers."""
    rereview_requested: bool = False
    resolved: tuple[Identifier, ...] = ()
    unresolved: tuple[Identifier, ...] = ()
    new_violations: tuple[Identifier, ...] = ()
    summary: str = Field(min_length=1, max_length=2000)


class RereviewSummary(Contract):
    """A08 result. Criterion lists must equal the code-side verdict comparison.

    `request_error` is set when request_rereview was called and rejected, so it is
    distinguishable from the model deciding not to call it. `summary` is written by
    code; `model_summary` is the model's unchecked explanation.
    """
    role: Literal['A08'] = 'A08'
    parent_run_id: Identifier
    child_run_id: Identifier | None = None
    child_status: ExecutionStatus | None = None
    rereview_requested: bool
    request_error: FrameworkError | None = None
    compared: bool = False
    resolved: tuple[Identifier, ...] = ()
    unresolved: tuple[Identifier, ...] = ()
    new_violations: tuple[Identifier, ...] = ()
    summary: str = Field(min_length=1, max_length=2000)
    model_summary: str = Field(min_length=1, max_length=2000)
    tools_used: tuple[str, ...] = ()
    synthetic: bool = False
