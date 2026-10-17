"""accept-config promises that "no earlier verdict survives a change in how work is judged". On the first real run
(SRA, ledger 851-863) the six reviews it invalidated memo-hit and carried their v1.0 verdicts forward instead."""

from __future__ import annotations

import json

from tests.conftest import FnExecutor
from vpg.engine import Engine
from vpg.models import NodeKind


def test_accept_config_re_runs_every_review_instead_of_carrying_its_verdict(project):
    proj, _ = project
    eng = Engine(proj, executor=FnExecutor())
    eng.run(max_steps=200)
    assert eng.state.runtime.status.value == "RUN_PASS"
    reviews = {n.id for n in eng.wf.nodes if n.kind == NodeKind.REVIEW}
    assert reviews
    cfg = proj / "vpg.project.yaml"
    cfg.write_text(cfg.read_text() + "note: a stricter rubric was adopted mid-run\n")
    eng = Engine(proj, executor=FnExecutor())
    eng.accept_config("Wei", "stricter rubric")
    eng.run(max_steps=200)
    events = [json.loads(l) for l in (proj / ".vpg/ledger.jsonl").read_text().splitlines()]
    after = events[max(i for i, e in enumerate(events) if e["type"] == "config_accepted"):]
    skipped = {e["data"]["node"] for e in after if e["type"] == "memo_hit"} & reviews
    judged = {e["data"]["node"] for e in after if e["type"] == "node_dispatched"} & reviews
    assert not skipped and judged == reviews, f"carried forward: {sorted(skipped)}"
    current = eng.state.runtime.current_version
    assert not [r for r in eng.state.evaluation.reviews if r.version == current and r.carried_forward and r.node in reviews]
