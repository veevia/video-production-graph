"""Typed models: the workflow graph definition and the shared production state.

The state is deliberately split into:
  inputs      immutable project inputs, frozen and hashed once sources are validated (n02)
  production  mutable production state, written only through declared node outputs
  artifacts   derived, content-addressed files with lineage
  evaluation  reviews, defects, feedback, gate results, before/after comparisons
  history     versions, decisions, approvals, retries, checkpoints, change requests (the run's memory)
  knowledge   a pointer to the reusable cross-project rule base (knowledge/rules.json) and the rules applied
  runtime     execution control (node status, attempts, fingerprints); vocabulary shared with Workflow OS
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------------------------- workflow definition


class NodeKind(str, Enum):
    AGENT = "agent"    # an agent produces declared outputs from a task packet
    TOOL = "tool"      # deterministic Python function in vpg.tools, runs in-process
    REVIEW = "review"  # deterministic tool checks first, then an agent's rubric review; tool MUST failures cannot be overridden


class EdgeKind(str, Enum):
    FLOW = "flow"          # forward dependency; the target waits for every incoming flow edge (join = all)
    LOOP = "loop"          # bounded back-edge; re-activates an upstream node instead of satisfying a join
    ESCALATE = "escalate"  # stop and report to a human (BLOCKED); never silently retried


class Check(_Strict):
    """A completion criterion the engine verifies from disk and state (never from agent self-report)."""

    type: Literal["artifact", "tool", "state", "feedback_interpreted", "defects_valid", "review_complete"]
    key: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    fn: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    description: str


class OutputSpec(_Strict):
    key: str
    kind: str
    description: str
    schema_name: str | None = Field(default=None, alias="schema")
    state_path: str | None = None
    state_fields: dict[str, str] = Field(default_factory=dict)  # JSON field → state path
    required: bool = True
    per_item: bool = False


class RetryPolicy(_Strict):
    max_attempts: int = Field(ge=1)
    behavior: str


class EscalationPolicy(_Strict):
    after: str
    to: str
    report: str


class HumanReview(_Strict):
    required: bool
    decision: str
    approver: str = "Wei"
    outcomes: list[str] = Field(default_factory=lambda: ["approved", "rejected"])
    dry_run_auto: bool = True


class ControlAccess(_Strict):
    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    lock_on_approval: list[str] = Field(default_factory=list)


class RunSpec(_Strict):
    tool: str | None = None          # "module:function" under vpg.tools (tool nodes, review pre-checks)
    command_hook: bool = False       # the project may bind a render/production command to this node


class NodeDef(_Strict):
    id: str
    num: int
    name: str
    phase: str
    subgraph: str
    kind: NodeKind
    purpose: str
    inputs: list[str]
    context: list[str]
    state_read: list[str]
    state_write: list[str]
    agent: str
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    instructions: str
    prompt: str | None = None
    outputs: list[OutputSpec]
    completion: list[Check]
    rubric: list[str]
    failure_conditions: list[str]
    retry: RetryPolicy
    escalation: EscalationPolicy
    human_review: HumanReview
    provenance: list[str]
    controls: ControlAccess = Field(default_factory=ControlAccess)
    resources: list[str] = Field(default_factory=list)
    map_over: str | None = None
    outcomes: list[str] = Field(default_factory=lambda: ["pass"])
    run: RunSpec | None = None
    gate: str | None = None


class EdgeDef(_Strict):
    id: str
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    kind: EdgeKind
    transition: Literal["deterministic", "conditional"]
    when: str
    transfers: list[str]
    threshold: str
    branching: str
    on_fail: str
    max_traversals: int | None = None


class Subgraph(_Strict):
    id: str
    name: str
    pattern: str
    purpose: str
    nodes: list[str]


class Policies(_Strict):
    max_rework_cycles: int = Field(ge=1)
    max_same_defect_failures: int = Field(ge=1)
    parallel_limit: int = Field(ge=1)
    single_flight_resources: list[str]
    blocking_severities: list[str]
    dry_run_actor: str
    schema_version: str = "vpg-state/v1"


class Workflow(_Strict):
    id: str
    name: str
    version: str
    description: str
    start: str = "START"
    end: str = "END"
    nodes: list[NodeDef]
    edges: list[EdgeDef]
    subgraphs: list[Subgraph]
    policies: Policies

    def node(self, node_id: str) -> NodeDef:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)


# --------------------------------------------------------------------------------------------------- shared state


class NodeStatus(str, Enum):
    # Names follow Workflow OS (02_Workflows/workflow-os) where the meaning is the same; STALE is new here.
    PLANNED = "PLANNED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_RUNTIME = "WAITING_RUNTIME"   # a task packet was issued; waiting for an agent submission
    WAITING_HUMAN = "WAITING_HUMAN"
    VALIDATING = "VALIDATING"
    STEP_PASS = "STEP_PASS"
    ITERATE = "ITERATE"                   # completion failed; will re-dispatch with the failure as context
    STALE = "STALE"                       # invalidated by rework or changed inputs; re-checked by fingerprint
    BLOCKED = "BLOCKED"                   # escalated to a human; the run stops rather than burn attempts
    CANCELLED = "CANCELLED"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    WAITING_RUNTIME = "WAITING_RUNTIME"
    WAITING_HUMAN = "WAITING_HUMAN"
    BLOCKED = "BLOCKED"
    RUN_PASS = "RUN_PASS"


class ProjectMeta(_Strict):
    id: str
    name: str
    created_at: str
    workflow_id: str
    workflow_version: str
    workflow_sha256: str


class SourceRef(_Strict):
    id: str
    path: str
    kind: str
    evidence: Literal["observed", "reported", "assumed"]
    sha256: str | None = None
    bytes: int | None = None
    confidential: bool = False
    notes: str = ""


class GapWaiver(_Strict):
    """A named human's decision to proceed without resolving an intake gap (recorded by `vpg unblock --waive`)."""
    gap: str
    by: str
    reason: str
    at: str


class ImmutableInputs(_Strict):
    brief: str = ""
    sources: list[SourceRef] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    waivers: list[GapWaiver] = Field(default_factory=list)
    frozen: bool = False
    frozen_sha256: str | None = None
    frozen_at: str | None = None


class Question(_Strict):
    id: str
    text: str
    blocking: bool = False
    owner: str = "Wei"
    status: Literal["open", "answered", "waived"] = "open"
    answer: str = ""


class Assumption(_Strict):
    text: str
    status: Literal["open", "confirmed", "rejected"] = "open"
    evidence: str = ""


class ProductionState(_Strict):
    objective: str = ""
    audience: list[str] = Field(default_factory=list)
    business_context: str = ""
    product_context: str = ""
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[Question] = Field(default_factory=list)
    brief_ref: str | None = None
    product_truth_ref: str | None = None
    concepts_ref: str | None = None
    creative_decision_ref: str | None = None
    narrative_ref: str | None = None
    script_ref: str | None = None
    storyboard_ref: str | None = None
    shotlist_ref: str | None = None
    scenes: list[dict[str, Any]] = Field(default_factory=list)
    product_requirements: list[str] = Field(default_factory=list)
    design_system_ref: str | None = None
    visual_references: list[str] = Field(default_factory=list)
    asset_plan_ref: str | None = None
    asset_plan: list[dict[str, Any]] = Field(default_factory=list)
    asset_registry: dict[str, str] = Field(default_factory=dict)
    audio_spec: dict[str, Any] = Field(default_factory=dict)
    voice_ref: str | None = None
    timings_ref: str | None = None
    scenes_ref: str | None = None
    text_layers_ref: str | None = None
    master_ref: str | None = None
    captions_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    refs: dict[str, str] = Field(default_factory=dict)


class ArtifactRecord(_Strict):
    id: str
    key: str
    path: str
    sha256: str
    bytes: int
    node: str
    attempt: int
    item: str | None = None
    produced_at: str
    inputs: list[str] = Field(default_factory=list)
    tool: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    version: int = 0
    status: Literal["current", "superseded"] = "current"


class Finding(_Strict):
    check: str
    dimension: str
    severity: Literal["P1", "P2", "P3", "nit"]
    message: str
    evidence: str = ""
    traced_node: str | None = None
    artifact_key: str | None = None
    smallest_fix: str = ""
    level: Literal["MUST", "SHOULD"] = "MUST"
    element: str | None = None   # which item a per-item check is about (a layer, beat, cue, unit or asset id); informational


class Score(_Strict):
    dimension: str
    score: float
    threshold: float
    level: Literal["MUST", "SHOULD"] = "MUST"
    passed: bool
    evidence: str = ""


class Review(_Strict):
    id: str
    node: str
    version: int
    reviewer: str
    rubric_ids: list[str]
    scores: list[Score] = Field(default_factory=list)
    tool_findings: list[Finding] = Field(default_factory=list)
    defect_ids: list[str] = Field(default_factory=list)
    verdict: Literal["pass", "fail"]
    overridden_agent_passes: list[str] = Field(default_factory=list)
    pending_human: list[str] = Field(default_factory=list)   # dimensions only a human can judge (listening, taste)
    carried_forward: bool = False
    created_at: str
    # v1.7 (#62/#67): who submitted it, the bytes the engine gave it to judge, the bytes it says it judged, and whether it is
    # independent of the producers of those bytes (None: not checked, e.g. a review recorded before v1.7 or one the engine ran)
    actor: str = ""
    inputs: dict[str, str] = Field(default_factory=dict)
    reviewed: dict[str, str] = Field(default_factory=dict)
    independent: bool | None = None


class Defect(_Strict):
    id: str
    signature: str
    severity: Literal["P1", "P2", "P3", "nit"]
    dimension: str
    description: str
    evidence: str = ""
    source: str
    traced_node: str
    artifact_key: str | None = None
    smallest_fix: str = ""
    acceptance_check: str = ""
    status: Literal["open", "in_rework", "resolved", "persisting", "waived"] = "open"
    needs_human: bool = False
    first_seen_version: int = 0
    last_seen_version: int = 0
    rework_count: int = 0
    elements: list[str] = Field(default_factory=list)   # the items (layer, beat, cue, unit, asset ids) its latest sighting names
    human_hold: str = ""   # why a named human must see it before rework routes it (a re-trace off a human-gated node)


class Feedback(_Strict):
    id: str
    text: str
    actor: str
    at: str = ""
    severity: str = ""
    version: int = 0
    created_at: str
    status: Literal["open", "interpreted", "triage", "dismissed"] = "open"   # triage: an agent's note awaiting a human (#14)
    defect_ids: list[str] = Field(default_factory=list)
    no_action_reason: str = ""
    hold: str = ""   # set when the interpreter closed a human's feedback with no action: no version passes until a human accepts it
    # v1.7 (#14): an agent's feedback never re-opens the gate by itself. It waits for a named human (or a granted agent) to
    # triage it: accepted, it counts as that human's feedback; dismissed, it is closed with the reason.
    triage: Literal["", "pending", "accepted", "dismissed"] = ""
    triaged_by: str = ""   # who accepted or dismissed it; an accepted note's hold is theirs (or the gate owner's) to release


class GateResult(_Strict):
    id: str
    node: str
    version: int
    outcome: Literal["pass", "rework", "blocked"]
    reasons: list[str]
    open_blocking: list[str] = Field(default_factory=list)
    created_at: str
    trend: dict[str, Any] | None = None   # the blocking defects this round saw (budget.gate_snapshot); None before v1.6


class Comparison(_Strict):
    rework_cycle: int
    defect_id: str
    before_version: int
    after_version: int
    resolved: bool
    before: str
    after: str
    artifacts_changed: dict[str, list[str]] = Field(default_factory=dict)


class EvaluationState(_Strict):
    reviews: list[Review] = Field(default_factory=list)
    defects: list[Defect] = Field(default_factory=list)
    feedback: list[Feedback] = Field(default_factory=list)
    gate_results: list[GateResult] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)


class ItemRuntime(_Strict):
    status: NodeStatus = NodeStatus.PLANNED
    attempts: int = 0
    fingerprint: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)


class NodeRuntime(_Strict):
    status: NodeStatus = NodeStatus.PLANNED
    attempts: int = 0
    fail_streak: int = 0
    executions: int = 0
    memo_hits: int = 0
    outcome: str | None = None
    result_data: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str | None = None
    last_pass_fingerprint: str | None = None
    command_code: dict[str, str] = Field(default_factory=dict)   # path → sha256 of the bound command's code at dispatch
    fingerprint_scheme: int | None = None   # the fingerprint scheme `fingerprint` was computed under (engine.FINGERPRINT_SCHEME)
    settled_fingerprint: str | None = None  # `fingerprint` without the rework defects it was dispatched to fix: what a pass records
    last_pass_scheme: int | None = None     # the scheme of `last_pass_fingerprint`; None: recorded before v1.5
    dispatch_token: str | None = None
    task_packet: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    failures: list[str] = Field(default_factory=list)
    pre_findings: list[Finding] = Field(default_factory=list)
    rework_defects: list[str] = Field(default_factory=list)
    items: dict[str, ItemRuntime] = Field(default_factory=dict)
    traversals: dict[str, int] = Field(default_factory=dict)
    interruptions: int = 0           # consecutive attempts cut short by a dead process; escalates at retry.max_attempts
    force_rerun: bool = False        # set by an explicit invalidate or a loop unblock: the next dispatch skips the memo once
    executed: bool = False           # this attempt's execution returned; a crash before it is judged is not the node's fault
    rechecks: int = 0                # depth of the control-document re-check chain that sent this node back (0 = none)
    preflight: dict[str, Any] | None = None   # a finished attempt waiting for its independent pre-flight verdict (v1.6)
    preflight_revisions: int = 0     # consecutive pre-flight revisions, counted apart from technical failures (v1.7, #106)
    command_code_at_run: dict[str, str] = Field(default_factory=dict)   # the code when its command started, if after dispatch (v1.7)
    self_written_code: dict[str, str] = Field(default_factory=dict)   # code files its command rewrote while it ran → sha256 as left (v1.7, #105)


class Escalation(_Strict):
    node: str
    reason: str
    report: str
    to: str
    at: str
    resolved: bool = False
    edge: str | None = None          # the loop edge whose traversal budget ran out, reset by a human unblock
    edges: list[str] = Field(default_factory=list)   # every loop edge the same plan needed whose budget ran out (edge is the first)
    reopened: str = ""               # why its node is no longer BLOCKED while it stays open (an invalidation, a rollback)
    holds: list[str] = Field(default_factory=list)   # defect and feedback holds the gate reported; an unblock releases only these


class RuntimeState(_Strict):
    run_id: str
    status: RunStatus = RunStatus.RUNNING
    nodes: dict[str, NodeRuntime] = Field(default_factory=dict)
    sequence: int = 0
    rework_cycles: int = 0
    defect_failures: dict[str, int] = Field(default_factory=dict)
    escalations: list[Escalation] = Field(default_factory=list)
    current_version: int = 0
    dry_run: bool = False
    batches: list[list[str]] = Field(default_factory=list)
    resumes: int = 0
    config_sha256: str = ""          # workflow + rules + rubrics + thresholds + project config accepted for this run
    pending_ledger: list[str] = Field(default_factory=list)   # write-ahead: a human action's events, committed with the state
    ledger_tail: list[Any] = Field(default_factory=list)       # [seq, hash] of the last ledger event when the state was saved
    ledger_breaks: list[dict[str, Any]] = Field(default_factory=list)   # losses of ledger events a named human accepted
    config_parts: dict[str, Any] = Field(default_factory=dict) # per-file hashes behind config_sha256, to say what changed
    loop_extra: dict[str, int] = Field(default_factory=dict)   # traversals granted to converging rework loops (v1.6)
    rework_extra: int = 0                                       # rework cycles granted to a converging run (v1.6)
    grant_versions: dict[str, int] = Field(default_factory=dict)  # budget ("rework" or a loop id) → version it was last granted at


class VersionRecord(_Strict):
    version: int
    created_at: str
    node_attempt: str
    artifacts: dict[str, str]


class Decision(_Strict):
    id: str
    node: str
    text: str
    by: str
    at: str


class Approval(_Strict):
    node: str
    decision: str
    actor: str
    human: bool
    at: str
    reason: str = ""
    outputs: dict[str, str] = Field(default_factory=dict)   # the node's outputs it decided on, key → sha256[:16] (v1.7, #43)
    grant: str = ""   # the grant an agent decided under (v1.7, #18); empty for a named human


class Grant(_Strict):
    """A named human's delegation of named human-only actions to one agent, until a date (v1.7, #18). An action taken under
    it is recorded as the agent's, with the grant and its grantor, never as the human's."""
    id: str
    grantor: str
    grantee: str
    actions: list[str]
    reason: str
    at: str
    expires: str          # an ISO date; the grant covers actions through the end of that day (UTC)
    revoked_at: str = ""
    revoked_by: str = ""
    revoke_reason: str = ""


class RetryRecord(_Strict):
    node: str
    attempt: int
    reasons: list[str]
    at: str


class CheckpointRef(_Strict):
    seq: int
    path: str
    event: str
    node: str | None = None
    at: str


class ChangeRequest(_Strict):
    id: str
    doc: str
    reason: str
    requested_by: str
    status: Literal["open", "approved", "rejected", "applied", "deferred"] = "open"
    approved_by: str | None = None
    at: str
    revisit: str = ""   # a deferred request: when it is taken up again (v1.7, #102)


class HistoryState(_Strict):
    versions: list[VersionRecord] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    retries: list[RetryRecord] = Field(default_factory=list)
    checkpoints: list[CheckpointRef] = Field(default_factory=list)
    change_requests: list[ChangeRequest] = Field(default_factory=list)
    grants: list[Grant] = Field(default_factory=list)


class ControlDocState(_Strict):
    doc: str
    path: str
    status: Literal["draft", "approved"] = "draft"
    version: int = 0
    sha256: str | None = None
    approved_sha256: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    snapshot: str | None = None


class KnowledgeRef(_Strict):
    rules_path: str
    rules_version: str
    rules_sha256: str
    enforced: list[str] = Field(default_factory=list)
    advisory: list[str] = Field(default_factory=list)
    proposals: list[str] = Field(default_factory=list)


class Deliverable(_Strict):
    key: str
    path: str
    sha256: str


class ProjectState(_Strict):
    schema_version: Literal["vpg-state/v1"] = "vpg-state/v1"
    project: ProjectMeta
    inputs: ImmutableInputs = Field(default_factory=ImmutableInputs)
    production: ProductionState = Field(default_factory=ProductionState)
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)
    evaluation: EvaluationState = Field(default_factory=EvaluationState)
    history: HistoryState = Field(default_factory=HistoryState)
    controls: dict[str, ControlDocState] = Field(default_factory=dict)
    knowledge: KnowledgeRef
    runtime: RuntimeState
    deliverables: list[Deliverable] = Field(default_factory=list)


STATE_SECTIONS = {
    "inputs": "immutable project inputs",
    "production": "mutable production state",
    "artifacts": "derived artifacts",
    "evaluation": "evaluation state",
    "history": "historical memory (plus the append-only ledger)",
    "knowledge": "reusable cross-project knowledge (by reference)",
    "runtime": "execution control",
    "controls": "control-document registry (hashes, approval locks)",
}
