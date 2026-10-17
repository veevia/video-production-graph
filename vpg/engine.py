"""The production orchestrator: a deterministic scheduler over the workflow graph.

One `step()`:
  1. Keeps a copy of each frozen source, then checks integrity: the frozen inputs are unchanged and no approved control
     document was edited outside the workflow.
  2. Collects ready nodes: every incoming flow edge satisfied (join = all), in topological order.
  3. Selects a conflict-free batch. No two nodes share a state write path, a control document they may write, or a
     single-flight resource (for example the TTS model or the renderer), up to `parallel_limit` nodes.
  4. Prepares each node. A STALE node whose fingerprint (input artifact hashes + control hashes + node definition
     + prompt + command and its code + rework context) equals its last passing fingerprint is memo-skipped:
     approved outputs are never regenerated.
  5. Executes the batch in parallel (tools, scripted agents and project commands), or issues task packets for real agents.
  6. Fans in on the main thread: registers outputs (content-addressed, with lineage), checks control-document
     changes, runs the completion checks, merges reviews (a tool MUST failure beats an agent pass), then passes,
     retries (ITERATE) or escalates (BLOCKED) the node.
  7. Routes loop edges (bounded) and escalate edges, invalidating targets and their flow descendants.
Every transition is appended to the hash-chained ledger, and state is checkpointed after every step.
"""

from __future__ import annotations

import concurrent.futures as cf
import functools
import json
import os
import re
import shlex
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

import jsonschema
import yaml
from pydantic import ValidationError

from .budget import budget_problems, convergence, gate_snapshot, hours_since, load_budget, trend_lines, trend_rows
from .context import CheckResult, NodeContext, NodeResult
from .controls import CONTROL_DOCS, ControlStore
from .evaluation import (binding_problems, compare_after_rework, gate, load_rubrics, merge_review, record_findings,
                         review_bindings)
from .executors import Executor, ScriptedExecutor, TaskPacketExecutor, run_command
from .graph import (DEFAULT_WORKFLOW, GraphIndex, control_reads, load_workflow, outcome_matches, resolve_tool, structural_errors,
                    workflow_sha256)
from .memory import DEFAULT_RULES, RuleBase, enforcement, summarize
from .models import (Approval, ArtifactRecord, ChangeRequest, CheckpointRef, Comparison, Decision, Defect, EdgeDef, EdgeKind, Escalation,
                     Feedback, GapWaiver, Finding, Grant, KnowledgeRef, NodeDef, NodeKind, NodeRuntime, NodeStatus, ProjectMeta,
                     ProjectState, RetryRecord, RunStatus, RuntimeState, VersionRecord)
from .provenance import Ledger, LedgerBehind, LedgerCorrupt
from .source_store import SourceStore
from .util import (PKG_ROOT, ConcurrentChange, NotRegularFile, file_identity, get_path, glob_files, is_human_name, now,
                   read_json, read_text, rel, set_path, sha256_file, sha256_json, write_json_atomic)

S = NodeStatus
# What a node's fingerprint covers. 0: before v1.1 (a bound command's code was left out). 1: v1.1–v1.4 (a `defects:`
# input hashed every defect of the run; a review's verdict was not tied to the configuration that judged it; a pass
# recorded the rework defects it was dispatched to fix, so it re-ran once after the fix). 2: v1.5.
# A pass records the scheme it was fingerprinted under (NodeRuntime.last_pass_scheme); `vpg accept-config` re-derives
# an older one where that is provable (Engine._carry_over_plan).
FINGERPRINT_SCHEME = 2
_NO_MATCH = object()


class EngineError(Exception):
    pass


class StaleSubmission(EngineError):
    pass


CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".zsh", ".rb", ".pl", ".lua", ".r", ".jl"}
CODE_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".git", ".venv", "venv"}
RESERVED_DIRS = {".vpg", "work", "controls", "sources", "outputs", "renders"}   # the workflow's own trees, never code
REUSE_READ_RE = re.compile(r"(work/[A-Za-z0-9_-]+/a\d+|glob\([^)]*a\*|/a\*/)")
# v1.7 (#18): the human-only actions a named human may delegate to one agent with `vpg grant`. A decision is delegable per
# node (`approve:<node>`), except at a node whose rubric has human-judged dimensions, or that decides on the reviewed film
# (the release): only a human decides those. Never delegable: accept-config (a configuration change can rewrite the
# workflow, rubrics, rules or engine, including this boundary), a rollback, a ledger break and a grant itself. Within a
# delegable action a grant still never touches what needs a human: a hold on a human's feedback, or a defect on a
# human-judged dimension, marked needs_human or held for a human.
GRANTABLE = ("unblock", "invalidate", "change-request", "resolve", "restore-control", "feedback-triage")
GRANT_MAX_DAYS = 90
REVIEW_POLICY_KEYS = {"binding", "independence"}   # vpg.project.yaml `reviews:` (v1.7, #62/#67): each warn | require
REUSE_HELPER_RE = re.compile(r"^\s*(from\s+vpg\.reuse\s+import\b|from\s+vpg\s+import\s+reuse\b|import\s+vpg\.reuse\b)", re.M)


def command_tokens(cmd: str) -> list[str]:
    """The words of a shell command line, with shell punctuation (; && | >) split off and comments dropped. A line the
    shell accepts but shlex cannot parse (e.g. an unbalanced quote) falls back to whitespace splitting."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return cmd.split()


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.vpg = self.root / ".vpg"
        self.work = self.root / "work"
        cfg = self.root / "vpg.project.yaml"
        self.config: dict[str, Any] = (yaml.safe_load(read_text(cfg)) or {}) if os.path.lexists(cfg) else {}

    @property
    def state_path(self) -> Path:
        return self.vpg / "state.json"

    def command_for(self, node_id: str) -> str | None:
        return (self.config.get("commands") or {}).get(node_id)

    def command_code(self, node_id: str) -> list[tuple[str, str]]:
        """(path, sha256) of every file whose content decides what the node's bound command does.

        The script files the command names, plus, by default, the whole top-level project directory each one sits in
        (`{python} pipeline/scenes.py` → every file under pipeline/, so a scene engine the script loads is covered).
        `command_deps: {<node>: [globs]}` in vpg.project.yaml replaces that directory rule for one node; a glob starting
        with `!` excludes. `command_code_exclude: [globs]` excludes files from every node's code, for files a pipeline
        generates into its own tree (they are outputs, not code). Named scripts always count; caches and the
        workflow's own directories never do."""
        cmd = self.command_for(node_id)
        if not cmd:
            return []
        root = self.root
        named: dict[str, Path] = {}
        for token in command_tokens(str(cmd)):
            try:
                path = Path(token) if os.path.isabs(token) else root / token
                if path.suffix.lower() in CODE_SUFFIXES and path.is_file():
                    named[rel(path, root)] = path
            except (OSError, ValueError):
                continue
        files: dict[str, Path] = {}
        excluded: set[str] = set()
        for pattern in self.config.get("command_code_exclude") or []:
            excluded |= {rel(f, root) for f in glob_files(root, str(pattern))}
        deps = (self.config.get("command_deps") or {}).get(node_id)
        if deps is not None:
            for pattern in map(str, deps):
                if pattern.startswith("!"):
                    excluded |= {rel(f, root) for f in glob_files(root, pattern[1:])}
                else:
                    files.update({rel(f, root): f for f in glob_files(root, pattern) if not self._skipped_code(f)})
        else:
            for path in named.values():
                try:
                    parts = path.resolve().relative_to(root).parts
                except ValueError:
                    continue   # outside the project: only the named file counts (list more with command_deps)
                if len(parts) > 1 and parts[0] not in RESERVED_DIRS:
                    for dirpath, dirnames, filenames in os.walk(root / parts[0]):
                        dirnames[:] = sorted(d for d in dirnames if d not in CODE_SKIP_DIRS)
                        for name in filenames:
                            f = Path(dirpath) / name
                            if not self._skipped_code(f):
                                files[rel(f, root)] = f
        files = {k: v for k, v in files.items() if k not in excluded}
        files.update(named)
        return [(k, self._code_hash(files[k])) for k in sorted(files)]

    def _skipped_code(self, path: Path) -> bool:
        try:
            parts = path.resolve().relative_to(self.root).parts
        except ValueError:
            parts = path.parts
        return path.name == ".DS_Store" or path.suffix in (".pyc", ".pyo") or any(p in CODE_SKIP_DIRS for p in parts)

    def config_problems(self, node_ids: set[str]) -> list[str]:
        """Shape errors in the command bindings, found before any dispatch instead of as a traceback in one."""
        problems = []

        def check_glob(where: str, pattern: Any, negation_ok: bool) -> None:
            text = pattern[1:] if negation_ok and isinstance(pattern, str) and pattern.startswith("!") else pattern
            if not isinstance(text, str) or not text.strip() or os.path.isabs(text) or ".." in Path(text).parts:
                problems.append(f"{where}: {pattern!r} is not a project-relative glob")
                return
            try:
                glob_files(self.root, text)
            except Exception as exc:   # any glob that cannot run is unusable, whatever pathlib raises for it
                problems.append(f"{where}: {pattern!r} is not a usable glob ({type(exc).__name__}: {exc})")

        cmds = self.config.get("commands")
        if cmds is not None and not isinstance(cmds, dict):
            problems.append("commands must map node ids to command lines")
        deps = self.config.get("command_deps")
        if deps is not None and not isinstance(deps, dict):
            problems.append("command_deps must map node ids to lists of project-relative globs")
        elif deps:
            for nid, globs in deps.items():
                if nid not in node_ids:
                    problems.append(f"command_deps names {nid!r}, which is not a node of this workflow")
                if not isinstance(globs, list):
                    problems.append(f"command_deps.{nid} must be a list of globs, not {type(globs).__name__}")
                    continue
                for g in globs:
                    check_glob(f"command_deps.{nid}", g, negation_ok=True)
        excl = self.config.get("command_code_exclude")
        if excl is not None and not isinstance(excl, list):
            problems.append("command_code_exclude must be a list of project-relative globs")
        elif excl:
            for g in excl:
                check_glob("command_code_exclude", g, negation_ok=False)
        problems += budget_problems(self.config)
        problems += self._preflight_problems(node_ids)
        problems += self._review_policy_problems()
        if "publish" in self.config:
            from .publish import config_problems as publish_config_problems
            problems += publish_config_problems(self.config.get("publish"))
        return problems

    def _review_policy_problems(self) -> list[str]:
        block = self.config.get("reviews")
        if block is None:
            return []
        if not isinstance(block, dict):
            return ["reviews must be a mapping, e.g. {binding: require, independence: require}"]
        problems = [f"reviews.{k} is not a setting (use {', '.join(sorted(REVIEW_POLICY_KEYS))})"
                    for k in sorted(set(block) - REVIEW_POLICY_KEYS)]
        problems += [f"reviews.{k} must be warn or require, not {block[k]!r}"
                     for k in sorted(REVIEW_POLICY_KEYS & set(block)) if block[k] not in ("warn", "require")]
        return problems

    def review_policy(self, key: str) -> str:
        """warn (the default) or require: vpg.project.yaml `reviews.binding` / `reviews.independence` (v1.7, #62/#67)."""
        block = self.config.get("reviews")
        value = block.get(key, "warn") if isinstance(block, dict) else "warn"
        return value if value in ("warn", "require") else "warn"

    def _preflight_problems(self, node_ids: set[str]) -> list[str]:
        block = self.config.get("preflight")
        if block is None:
            return []
        if not isinstance(block, dict):
            return ["preflight must be a mapping, e.g. {nodes: [n14_video_assembly]}"]
        problems = [f"preflight.{k} is not a setting (use nodes, when, max_revisions)"
                    for k in sorted(set(block) - {"nodes", "when", "max_revisions"})]
        rev = block.get("max_revisions")
        if "max_revisions" in block and (isinstance(rev, bool) or not isinstance(rev, int) or rev < 1):
            problems.append(f"preflight.max_revisions must be a whole number ≥ 1, not {rev!r}")
        nodes = block.get("nodes")
        if not isinstance(nodes, list) or not nodes or not all(isinstance(n, str) for n in nodes):
            problems.append("preflight.nodes must be a non-empty list of node ids")
        else:
            problems += [f"preflight.nodes names {n!r}, which is not a node of this workflow" for n in nodes if n not in node_ids]
        if block.get("when", "rework") not in ("rework", "always"):
            problems.append(f"preflight.when must be rework or always, not {block.get('when')!r}")
        return problems

    def _code_hash(self, path: Path) -> str:
        """sha256 of a code file, cached per process by its stat identity (the fingerprint runs at every dispatch)."""
        try:
            st = path.stat()
        except OSError as exc:
            return f"unreadable: {type(exc).__name__}"
        ident = (str(path), st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        cache = self.__dict__.setdefault("_code_cache", {})
        if ident not in cache:
            try:
                cache[ident] = sha256_file(path)
            except (OSError, NotRegularFile) as exc:
                cache[ident] = f"unreadable: {type(exc).__name__}"
        return cache[ident]

    def reuse_advisories(self, node_ids: set[str]) -> list[str]:
        """Advisory (never blocking): a code file that reads earlier attempts' work directories (`work/<node>/a<N>`, or a
        glob over `a*` attempt folders) without importing vpg.reuse may carry an old output past a fix to its producer.
        Judged per file, on the import, so a project's own `stamp()` or a `timestamp()` call never counts as the helper.
        The engine can't see intent without running the code, so this only warns."""
        users: dict[str, list[str]] = {}
        for nid in sorted(node_ids):
            if not self.command_for(nid):
                continue
            try:
                code = self.command_code(nid)
            except Exception as exc:
                users.setdefault(f"<{nid}: code unreadable ({type(exc).__name__})>", []).append(nid)
                continue
            for path, sha in code:
                if not sha.startswith("unreadable:"):
                    users.setdefault(path, []).append(nid)
        warnings = []
        for path, nids in sorted(users.items()):
            if path.startswith("<"):
                warnings.append(f"reuse advisory could not inspect {path[1:-1]}")
                continue
            try:
                text = (self.root / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if REUSE_READ_RE.search(text) and not REUSE_HELPER_RE.search(text):
                warnings.append(f"{path} (code of {', '.join(nids)}) appears to read earlier attempts' outputs without vpg.reuse; "
                                "key that reuse on the producer's code (vpg.reuse.may_reuse), or a fix to the producer is skipped")
        return warnings


def _human_action(event: str):
    """Run an Engine method as one durable human action (see Engine._action)."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(self, *args, **kwargs):
            with self._action(event):
                result = fn(self, *args, **kwargs)
            self._drain_publish()   # after the action's single commit
            return result
        return inner
    return wrap


class Engine:
    def __init__(self, project_dir: str | Path, executor: Executor | None = None, workflow_path: str | Path | None = None,
                 auto_approve: bool = False, rules_path: str | Path | None = None, read_only: bool = False,
                 accept_ledger_break: tuple[str, str] | None = None):
        """Open (or create) a run. `read_only` inspects the last saved state without resuming, saving or ledgering;
        callers that change the run must hold the project lock (vpg.lock) so parallel processes cannot lose updates."""
        self._in_action = False   # inside a human action, saves are deferred to its single commit
        self._publish_queue: list[int] = []   # versions to publish once the state that cut them is saved (auto publish)
        self._undo: list[Callable[[], None]] = []   # disk side effects of the current human action, undone if it fails
        self._undo_failures: list[str] = []
        self._recovered_ledger = 0
        self._state_identity: tuple[int, int, int] | None = None   # state.json as this engine last read or wrote it
        self.project = Project(project_dir)
        self.read_only = read_only
        cfg = self.project.config
        wf_ref = workflow_path or cfg.get("workflow")
        self.workflow_path = Path(wf_ref) if wf_ref and wf_ref != "default" else DEFAULT_WORKFLOW
        self.wf = load_workflow(self.workflow_path)
        if not read_only:
            # Structure, references and rubric placement: a missing rubric, tool, prompt or schema would silently drop
            # its checks. (The brief's documentation bar is `vpg validate`'s job, not a reason to refuse a run.)
            from .graph import _rubric_placement_errors, node_resolution_errors
            unsafe = structural_errors(self.wf) or [e for n in self.wf.nodes for e in node_resolution_errors(n, PKG_ROOT)]
            if not unsafe:
                unsafe = _rubric_placement_errors(self.wf, PKG_ROOT)
            if unsafe:
                raise EngineError(f"the workflow graph fails validation, so it will not run ({len(unsafe)} errors; see "
                                  f"`vpg validate --workflow {self.workflow_path}`): " + "; ".join(unsafe[:5]))
        self.index = GraphIndex(self.wf)
        if not read_only:
            bad = self.project.config_problems(set(self.index.ids)) + self._preflight_node_problems()
            if bad:
                raise EngineError("vpg.project.yaml has invalid command settings, so the run will not start: " + "; ".join(bad))
        self.rules_path = Path(rules_path or cfg.get("rules") or DEFAULT_RULES)
        if not self.rules_path.is_absolute() and cfg.get("rules"):
            self.rules_path = self.project.root / self.rules_path
        self.rubrics = self._effective_rubrics(load_rubrics(PKG_ROOT))
        self.executor = executor or self._default_executor()
        self.auto_approve = auto_approve
        if read_only and not self.project.state_path.exists():
            raise EngineError(f"no run in {self.project.root} yet; start one with `vpg run`")
        self.ledger = Ledger(self.project.vpg / "ledger.jsonl", read_only=read_only)
        self.on_step: Callable[["Engine"], None] | None = None
        self._interpreter_resolved: list[str] = []
        self._interpreted_now: list[str] = []
        self._defect_events: list[dict[str, Any]] = []
        if self.project.state_path.exists():
            self._state_identity = file_identity(self.project.state_path)
            self.state = ProjectState.model_validate(read_json(self.project.state_path))
            self.controls = ControlStore(self.project.root, self.state, self.project.vpg)
            if not read_only:
                if self.state.runtime.pending_ledger:   # a human action committed but its ledger write did not land
                    written = self.ledger.recover(self.state.runtime.pending_ledger)
                    self.state.runtime.pending_ledger = []
                    self._recovered_ledger = written
                self._check_ledger_anchor(accept_ledger_break)   # before anything new is written to a damaged ledger
                self._resume_fixups()
        else:
            self.state = self._new_state()
            self.controls = ControlStore(self.project.root, self.state, self.project.vpg)
            self.controls.register_all()
            self.controls.accept_all()
            self.ledger.append("run_created", run=self.state.runtime.run_id, workflow=self.wf.version,
                               rules=self.state.knowledge.rules_version)
            self._save("run_created")
        if not read_only and not self.state.runtime.dry_run:
            # A real production's gates need a named human, and its agents' work is never replayed from fixtures.
            if auto_approve:
                raise EngineError("auto-approval is for dry-run projects only: this run is a real production "
                                  "(vpg.project.yaml has no `dry_run: true`), so every human gate needs a named human")
            if isinstance(self.executor, ScriptedExecutor):
                raise EngineError("replaying agent outputs from fixtures is for dry-run projects only: this run is a real "
                                  "production, so agents submit their work through task packets")

    # ------------------------------------------------------------------ setup
    def _default_executor(self) -> Executor:
        if self.project.config.get("executor") == "scripted":
            return ScriptedExecutor(self.project.root / self.project.config.get("fixtures", "fixtures"))
        return TaskPacketExecutor()

    def _effective_rubrics(self, rubrics: dict[str, Any]) -> dict[str, Any]:
        """Apply the rule lifecycle: dimensions whose rule is advisory become SHOULD, and inactive ones are dropped."""
        try:
            rb = RuleBase(self.rules_path)
            modes = {r["id"]: enforcement(r) for r in rb.rules}
        except (FileNotFoundError, KeyError):
            modes = {}
        out = {}
        for rid, rub in rubrics.items():
            rub = json.loads(json.dumps(rub))
            dims = []
            for d in rub["dimensions"]:
                mode = modes.get(d.get("rule"), "enforced") if d.get("rule") else "enforced"
                if mode == "inactive":
                    continue
                if mode == "advisory":
                    d["level"] = "SHOULD"
                    d["advisory_reason"] = f"rule {d['rule']} is not yet adopted"
                dims.append(d)
            rub["dimensions"] = dims
            out[rid] = rub
        return out

    def _dimension_levels(self) -> dict[str, str]:
        return {d["id"]: d.get("level", "MUST") for rub in self.rubrics.values() for d in rub["dimensions"]}

    def _new_state(self) -> ProjectState:
        cfg = self.project.config
        pid = cfg.get("id", self.project.root.name)
        kb = summarize(self.rules_path)
        run_id = f"{pid}-{now().replace(':', '').replace('-', '').replace('+0000', 'Z')}"
        st = ProjectState(
            project=ProjectMeta(id=pid, name=cfg.get("name", pid), created_at=now(), workflow_id=self.wf.id,
                                workflow_version=self.wf.version, workflow_sha256=workflow_sha256(self.workflow_path)),
            knowledge=KnowledgeRef(rules_path=str(self.rules_path), rules_version=kb["version"], rules_sha256=kb["sha256"],
                                   enforced=kb["enforced"], advisory=kb["advisory"]),
            runtime=RuntimeState(run_id=run_id, dry_run=bool(cfg.get("dry_run")), config_sha256=self._config_fingerprint(),
                                 config_parts=self._config_parts(),
                                 nodes={n.id: NodeRuntime() for n in self.wf.nodes}),
        )
        brief = cfg.get("brief")
        if brief and (self.project.root / brief).exists():
            st.inputs.brief = read_text(self.project.root / brief)
        st.inputs.constraints = list(cfg.get("constraints", []))
        return st

    def _check_ledger_anchor(self, accept: tuple[str, str] | None) -> None:
        """The ledger must still contain the last event the state recorded. Otherwise events were deleted, cut off or the
        ledger was replaced, and appending now would hide that under a new, valid-looking end. Only a named human may
        accept the loss (`vpg accept-ledger-break`); the break is then ledgered, kept in the state and shown at release."""
        tail = self.state.runtime.ledger_tail
        lost = self.ledger.reaches(tail)
        if not lost:
            if accept:
                raise EngineError("the ledger reaches the last event the state recorded; there is no break to accept")
            return
        here = rel(self.project.root, Path.cwd())
        if not accept:
            raise EngineError(f"{lost}. Nothing was written. If no backup exists, a named human may accept the loss: "
                              f"`vpg accept-ledger-break {here} --actor <name> --reason \"…\"`")
        actor, reason = accept
        self._require_human(actor, "accepting a ledger break")
        if not reason.strip():
            raise EngineError("accepting a ledger break needs the human's reason (--reason)")
        brk = {"actor": actor, "reason": reason.strip(), "at": now(), "lost_after_seq": tail[0], "lost_hash": str(tail[1])[:16],
               "problem": lost, "resumes_at_seq": self.ledger._last[0] + 1}
        self.state.runtime.ledger_breaks.append(brk)
        self.ledger.append("ledger_break_accepted", **brk)

    def _resume_fixups(self) -> None:
        self.state.runtime.resumes += 1
        if not self.state.runtime.config_sha256:   # a run recorded before configuration fingerprints existed
            self.state.runtime.config_sha256 = self._config_fingerprint()
        for nid, rt in self.state.runtime.nodes.items():
            if rt.status in (S.RUNNING, S.VALIDATING):
                # RUNNING without `executed`: the process died mid-execution (or in a command bound at submit).
                # VALIDATING: it died while the node was judged.
                # RUNNING with `executed`: the execution had returned and the crash hit another node's fan-in.
                was = rt.status
                cut_short = was == S.VALIDATING or not rt.executed
                rt.status, rt.executed = S.READY, False
                rt.attempts = max(0, rt.attempts - 1)   # an interrupted attempt does not consume the retry budget …
                if cut_short:
                    rt.interruptions += 1               # … but repeated interruptions escalate instead of looping forever
                self.ledger.append("resume_reset", node=nid, interruptions=rt.interruptions, cut_short=cut_short)
                node = self.node(nid)
                where = ("while its submission was being judged" if was == S.VALIDATING else
                         "while it was executing or running its bound command" if cut_short else
                         "after it had finished executing, while another node was being judged")
                budget = (f"interruption {rt.interruptions} of {node.retry.max_attempts} before it escalates" if cut_short
                          else "not counted against it")
                notes = [f"the previous attempt was interrupted: the process stopped {where} ({budget}); this attempt starts again"]
                for doc in node.controls.write:   # it re-runs from scratch, so a partial edit is rolled back (and kept)
                    keep = self.project.vpg / "controls" / f"{doc}.interrupted-{nid}-r{self.state.runtime.resumes}.md"
                    if self.controls.undo_interrupted(doc, keep):
                        self.ledger.append("interrupted_edit_undone", node=nid, doc=doc, kept=rel(keep, self.project.root))
                        notes.append(f"its unfinished edit to controls/{doc}.md was rolled back to the accepted version; the "
                                     f"discarded text is kept at {rel(keep, self.project.root)}")
                earlier = [f for f in rt.failures if not f.startswith(("the previous attempt was interrupted", "its unfinished edit"))]
                rt.failures = notes + earlier   # shown to the next attempt under "The previous attempt failed because"
                if cut_short and rt.interruptions >= node.retry.max_attempts:
                    self._escalate(node, f"the process stopped {rt.interruptions} times while this node was executing or being judged",
                                   ["each resume re-dispatched it and it never finished; check its command, its completion checks, "
                                    "its inputs and memory use"])
        self.ledger.append("resumed", run=self.state.runtime.run_id, resumes=self.state.runtime.resumes)
        self._save("resumed")

    def _config_fingerprint(self) -> str:
        """Everything that decides how work is produced and judged: the graph, the rule base, the rubrics, the default
        thresholds, the artifact schemas, the prompts, the agent role files, the project config and the engine's code. A
        change mid-run needs a named human's acceptance (`vpg accept-config`)."""
        return sha256_json(self._config_parts())

    def _config_changes(self) -> list[str]:
        """Which parts of the configuration differ from the ones this run accepted (empty when unknown)."""
        old, new = self.state.runtime.config_parts, self._config_parts()
        if not old:
            return []
        out = []
        for key in sorted(set(old) | set(new)):
            if key not in old:   # a part an earlier engine did not cover: name it once, not each of its files
                out.append(f"{key} (newly part of the configuration)")
                continue
            a, b = old.get(key), new.get(key)
            if isinstance(a, dict) or isinstance(b, dict):
                a, b = a or {}, b or {}
                out += [f"{key}/{name}" for name in sorted(set(a) | set(b)) if a.get(name) != b.get(name)]
            elif a != b:
                out.append(key)
        return out

    def _config_parts(self) -> dict[str, Any]:
        cfg = self.project.root / "vpg.project.yaml"
        return ({
            "project": sha256_file(cfg) if cfg.exists() else None,
            "workflow": sha256_file(self.workflow_path),
            "rules": sha256_file(self.rules_path) if self.rules_path.exists() else None,
            "rubrics": {q.name: sha256_file(q) for q in sorted((PKG_ROOT / "rubrics").glob("*.yaml"))},
            "defaults": sha256_file(PKG_ROOT / "config" / "defaults.yaml"),
            "schemas": {q.name: sha256_file(q) for q in sorted((PKG_ROOT / "schemas" / "artifacts").glob("*.json"))},
            "prompts": {q.name: sha256_file(q) for q in sorted((PKG_ROOT / "prompts").glob("*.md"))},
            "agents": {q.name: sha256_file(q) for q in sorted((PKG_ROOT / "agents").glob("*.yaml"))},
            # the engine and its tools decide how work is judged as much as the rubrics do: a mid-run code change is
            # named and needs the same acceptance, instead of applying silently
            "engine": {str(q.relative_to(PKG_ROOT / "vpg")): sha256_file(q) for q in sorted((PKG_ROOT / "vpg").rglob("*.py"))
                       if "__pycache__" not in q.parts},
        })

    # ------------------------------------------------------------------ helpers
    def rt(self, node_id: str) -> NodeRuntime:
        return self.state.runtime.nodes[node_id]

    def node(self, node_id: str) -> NodeDef:
        return self.wf.node(node_id)

    def current(self, key: str, state: ProjectState | None = None) -> list[ArtifactRecord]:
        st = self.state if state is None else state
        return sorted((a for a in st.artifacts.values() if a.key == key and a.status == "current"), key=lambda a: a.id)

    def reviewer_nodes(self) -> list[str]:
        gate_node = next(n for n in self.wf.nodes if n.gate == "review_gate")
        return [e.source for e in self.index.incoming(gate_node.id, EdgeKind.FLOW)]

    def _inflight(self) -> list[str]:
        return [nid for nid, rt in self.state.runtime.nodes.items()
                if rt.status in (S.RUNNING, S.VALIDATING, S.WAITING_RUNTIME)]

    def _inflight_writers(self, exclude: str | None = None) -> set[str]:
        docs: set[str] = set()
        for nid in self._inflight():
            if nid != exclude:
                docs |= set(self.node(nid).controls.write)
        return docs

    def _edge_satisfied(self, e) -> bool:
        if e.source == "START":
            return True
        rt = self.rt(e.source)
        return rt.status == S.STEP_PASS and outcome_matches(e.when, rt.outcome, rt.result_data)

    def ready_nodes(self) -> list[str]:
        out = []
        for nid in self.index.topo:
            rt = self.rt(nid)
            if rt.status not in (S.PLANNED, S.READY, S.ITERATE, S.STALE):
                continue
            if all(self._edge_satisfied(e) for e in self.index.incoming(nid, EdgeKind.FLOW)):
                out.append(nid)
        return out

    @staticmethod
    def _overlaps(a: set[str], b: set[str]) -> bool:
        for x in a:
            for y in b:
                if x == y or x.startswith(y + ".") or y.startswith(x + "."):
                    return True
        return False

    # Engine-managed append-only collections: records are appended at fan-in on the main thread, so concurrent
    # nodes that each add their own records (for example the six reviewers) do not conflict.
    APPEND_ONLY = ("evaluation.reviews", "evaluation.defects", "evaluation.feedback", "evaluation.comparisons",
                   "evaluation.gate_results", "history")

    def conflict_keys(self, node: NodeDef) -> tuple[set[str], set[str]]:
        writes = {w for w in node.state_write if not any(w == a or w.startswith(a + ".") for a in self.APPEND_ONLY)}
        writes |= {f"controls.{d}" for d in node.controls.write}
        res = set(node.resources) & set(self.wf.policies.single_flight_resources)
        return writes, res

    def select_batch(self, ready: list[str]) -> list[str]:
        # A control-document writer runs alone: nothing else is in flight while it may edit a control document, so
        # every control change is attributable, and no reader sees a half-written document.
        inflight = self._inflight()
        if any(self.node(n).controls.write for n in inflight):
            return []
        writers = [n for n in ready if self.node(n).controls.write]
        if writers:
            return [] if inflight else [writers[0]]
        batch: list[str] = []
        writes: set[str] = set()
        res: set[str] = set()
        for nid in inflight:
            w, r = self.conflict_keys(self.node(nid))
            writes |= w
            res |= r
        for nid in ready:
            w, r = self.conflict_keys(self.node(nid))
            if self._overlaps(w, writes) or (r & res):
                continue
            batch.append(nid)
            writes |= w
            res |= r
            if len(batch) >= self.wf.policies.parallel_limit:
                break
        return batch

    def fingerprint(self, node: NodeDef, **kw: Any) -> str:
        return sha256_json(self._fingerprint_parts(node, **kw))

    def _fingerprint_parts(self, node: NodeDef, *, state: ProjectState | None = None, scheme: int | None = None,
                           code: Any = "current", rework: list[str] | None = None) -> dict[str, Any]:
        """What decides a node's output, under fingerprint `scheme` (see FINGERPRINT_SCHEME). The keywords re-derive a
        pass recorded earlier (_carry_over_plan): `state` is the run state to read (a pass checkpoint), `code` is
        "current" (the bound command's files on disk) or recorded (path, sha256) pairs, and `rework` stands in for the
        node's rework defects. Whatever lives outside the run state (the node's definition, prompt, control documents,
        rubrics, project files and command line) is always read as it is now."""
        st = self.state if state is None else state
        scheme = FINGERPRINT_SCHEME if scheme is None else scheme
        node_rt = st.runtime.nodes.get(node.id)
        rework_ids = sorted((node_rt.rework_defects if node_rt else []) if rework is None else rework)
        parts: dict[str, Any] = {"node": sha256_json(node.model_dump(mode="json", by_alias=True)), "wf": self.wf.version}
        hashes = self.controls.hashes()
        version = st.runtime.current_version
        for ref in node.inputs:
            kind, _, name = ref.partition(":")
            if kind == "artifact":
                parts[ref] = [a.sha256 for a in self.current(name, st)]
            elif kind == "control":
                parts[ref] = hashes.get(name)
            elif kind == "state":
                parts[ref] = sha256_json(get_path(st, name))
            elif kind == "rubric":
                parts[ref] = sha256_json(self.rubrics.get(name))
            elif kind == "reviews":
                parts[ref] = sha256_json([(r.node, r.version, r.verdict, r.defect_ids) for r in st.evaluation.reviews if r.version == version])
            elif kind == "feedback":
                parts[ref] = sha256_json([f.id for f in st.evaluation.feedback if f.status == "open"])
            elif kind == "defects":   # since scheme 2, `defects:rework` is what the node is given (_context): its own rework
                ds = st.evaluation.defects
                if name == "rework" and scheme >= 2:
                    ds = [d for d in ds if d.id in rework_ids]
                parts[ref] = sha256_json([(d.id, d.status) for d in ds])
            elif kind == "knowledge":
                parts[ref] = st.knowledge.rules_sha256
            elif kind == "files":   # project files, e.g. files:sources/** — adding a source changes the fingerprint
                parts[ref] = [(rel(f, self.project.root), sha256_file(f)) for f in glob_files(self.project.root, name)]
        for d in sorted(control_reads(node)):   # a document read through controls.read shapes the output as much as an input
            parts.setdefault(f"control:{d}", hashes.get(d))
        parts["rework"] = rework_ids
        parts["prompt"] = sha256_file(PKG_ROOT / node.prompt) if node.prompt and (PKG_ROOT / node.prompt).exists() else None
        parts["command"] = self.project.command_for(node.id)
        if node.kind == NodeKind.REVIEW and scheme >= 2:   # a verdict belongs to the configuration that judged it
            parts["judged_under"] = st.runtime.config_sha256
        if parts["command"] and scheme >= 1:   # the code it runs: editing a script it calls re-runs the node, not a memo-skip
            parts["command_code"] = self.project.command_code(node.id) if code == "current" else [tuple(c) for c in code]
        if node.map_over:
            parts["items"] = sha256_json(get_path(st, node.map_over))
        return parts

    def _context(self, node: NodeDef, attempt: int, fp: str, token: str) -> NodeContext:
        rt = self.rt(node.id)
        defects = [d for d in self.state.evaluation.defects if d.id in rt.rework_defects]
        if node.gate == "review_gate":
            defects = [d for d in self.state.evaluation.defects if d.status in ("open", "persisting", "in_rework")]
        items = None
        if node.map_over:
            specs = get_path(self.state, node.map_over) or []
            items = [s["id"] for s in specs]
        return NodeContext(node=node, attempt=attempt, workdir=self.project.work / node.id / f"a{attempt}",
                           project_dir=self.project.root, pkg_root=PKG_ROOT, state=self.state, fingerprint=fp,
                           dispatch_token=token, config=self.project.config, rubrics=self.rubrics, controls=self.controls,
                           defects=defects, failures=list(rt.failures), pre_findings=list(rt.pre_findings), items=items,
                           producer_code=self._producer_code(node.id))

    def _producer_code(self, node_id: str) -> dict[str, str]:
        """The bound command's code identity for its context (vpg.reuse); never a reason for a dispatch to fail."""
        try:
            return dict(self.project.command_code(node_id)) if self.project.command_for(node_id) else {}
        except Exception as exc:
            return {"<unreadable>": f"unreadable: {type(exc).__name__}"}

    # ------------------------------------------------------------------ scheduling
    def step(self) -> list[str]:
        if self.state.inputs.frozen and self._keep_sources() and self.state.runtime.status in (RunStatus.BLOCKED, RunStatus.RUN_PASS):
            self._save("sources_kept")   # a run frozen before v1.5 gets its copies too; a blocked one is not saved below
        if self.state.runtime.status in (RunStatus.BLOCKED, RunStatus.RUN_PASS):
            return []
        problems = self._integrity_problems()
        if problems:
            self._escalate_run("integrity", "; ".join(problems))
            self._save("integrity_blocked")
            return []
        ready = self.ready_nodes()
        if not ready:
            self._update_status()
            self._save("idle")
            return []
        batch = self.select_batch(ready)
        if not batch:
            self._update_status()
            self._save("waiting")
            return []
        tampered = self._tampered_inputs(batch)
        if tampered:
            self._escalate_run("integrity", "; ".join(tampered))
            self._save("integrity_blocked")
            return []
        self.state.runtime.batches.append(batch)
        self.ledger.append("batch", nodes=batch, parallel=len(batch) > 1)
        ctxs = [c for c in (self._prepare(nid) for nid in batch) if c is not None]
        if ctxs:
            self._save("dispatch")   # a process that dies mid-execution leaves these nodes RUNNING, so the resume counts it
        results: dict[str, NodeResult] = {}
        if len(ctxs) > 1:
            with cf.ThreadPoolExecutor(max_workers=self.wf.policies.parallel_limit) as pool:
                futs = {pool.submit(self._execute, c): c.node.id for c in ctxs}
                for fut in cf.as_completed(futs):
                    results[futs[fut]] = fut.result()
                    self._executed(futs[fut])
        else:
            for c in ctxs:
                results[c.node.id] = self._execute(c)
                self._executed(c.node.id)
        for c in ctxs:   # fan-in: all state mutation happens here, sequentially
            self._handle(c, results[c.node.id])
        self._update_status()
        self._save("step")
        if self.on_step:
            self.on_step(self)
        return batch

    def _executed(self, nid: str) -> None:
        """Record that a node's execution returned, so a crash during another node's fan-in is not charged to it. Code
        its command changed while it ran is recorded now, with its hash as left (#105): not later, after a pre-flight
        hold, when a developer's edit would pass for the node's own write."""
        rt = self.rt(nid)
        if rt.status == S.RUNNING:
            rt.executed = True
            changed = self._code_changed_since_dispatch(nid, since_run=True)
            now_code = dict(self.project.command_code(nid)) if changed else {}
            rt.self_written_code = {p: now_code[p] for p in changed if p in now_code}
            self._save("executed")

    def run(self, max_steps: int = 1000) -> dict[str, Any]:
        for _ in range(max_steps):
            progressed = self.step()
            self._drain_publish()   # a version the step cut is published after the step's save
            if not progressed:
                break
        return self.status()

    def _integrity_problems(self) -> list[str]:
        special = [f"controls/{d}.md is not a regular file (a directory, pipe or device). Recreate the accepted version with "
                   f"`vpg restore-control {rel(self.project.root, Path.cwd())} --doc {d} --actor <name>` (move a directory away "
                   "first), then `vpg unblock … integrity`" for d in CONTROL_DOCS
                   if self.controls.path(d).exists() and not self.controls.path(d).is_file()]
        if special:   # checked before anything hashes the documents
            return special
        problems = []
        inp = self.state.inputs
        if inp.frozen and sha256_json(inp.model_dump(exclude={"frozen", "frozen_sha256", "frozen_at"})) != inp.frozen_sha256:
            problems.append("immutable project inputs changed after they were frozen")
        for doc in self.controls.unaccounted(self._inflight_writers()):
            if not self.controls.path(doc).exists():
                problems.append(f"{doc}.md was deleted outside the workflow. Recreate the accepted version with `vpg restore-control "
                                f"{rel(self.project.root, Path.cwd())} --doc {doc} --actor <name>`, then `vpg unblock … integrity`")
                continue
            problems.append(f"{doc}.md changed outside the workflow. Restore the accepted version with `vpg restore-control "
                            f"{rel(self.project.root, Path.cwd())} --doc {doc} --actor <name>` (the edit is kept aside), then "
                            "`vpg unblock … integrity`. To make the change, have its writer make it: reject with feedback at its "
                            "gate, or open `vpg change-request` for an approved document")
        if self.state.runtime.config_sha256 and self._config_fingerprint() != self.state.runtime.config_sha256:
            changed = self._config_changes()
            problems.append(f"the configuration changed since this run started ({', '.join(changed) if changed else 'the workflow, rule base, rubrics, default thresholds or project config'}); "
                            f"a named human must accept it: `vpg accept-config {rel(self.project.root, Path.cwd())} --actor <name> --reason '…'`")
        return problems

    def _tampered_inputs(self, nids: list[str]) -> list[str]:
        """Registered input artifacts must still match their recorded hash before a node consumes them. The node that
        packages the delivery also re-checks every deliverable listed in production.md."""
        out, seen = [], set()
        for nid in nids:
            node = self.node(nid)
            keys = [ref.partition(":")[2] for ref in node.inputs if ref.startswith("artifact:")]
            if node.gate == "package":
                keys += [d["key"] for d in self.controls.front_matter("production").get("deliverables", []) if d.get("key")]
            for name in dict.fromkeys(keys):
                for a in self.current(name):
                    if a.id in seen:
                        continue
                    seen.add(a.id)
                    path = self.project.root / a.path
                    if not path.exists():
                        out.append(f"artifact {a.id} ({a.path}) registered by {a.node} is missing on disk")
                    elif not path.is_file():
                        out.append(f"artifact {a.id} ({a.path}) registered by {a.node} is no longer a regular file; restore it")
                    elif sha256_file(path) != a.sha256:
                        out.append(f"artifact {a.id} ({a.path}) changed on disk after {a.node} registered it; restore it, "
                                   f"or invalidate {a.node} to regenerate it (`vpg invalidate`)")
        return out

    def _prepare(self, nid: str) -> NodeContext | None:
        node, rt = self.node(nid), self.rt(nid)
        fp = self.fingerprint(node)
        # Memoisation is for pure producers. Gates and routers (gate hooks, loop/escalate edges) always re-evaluate,
        # because skipping them would skip their routing.
        router = bool(node.gate) or any(e.kind != EdgeKind.FLOW for e in self.index.outgoing(nid))
        if rt.status == S.STALE and rt.last_pass_fingerprint == fp and not rt.rework_defects and not router and not rt.force_rerun:
            rt.status = S.STEP_PASS
            rt.memo_hits += 1
            self._carry_forward_review(node)
            self.ledger.append("memo_hit", node=nid, fingerprint=fp[:16], reason="inputs unchanged since the last approved pass")
            return None
        rt.attempts += 1
        rt.executions += 1
        rt.status = S.RUNNING
        rt.force_rerun, rt.executed = False, False
        rt.fingerprint, rt.fingerprint_scheme = fp, FINGERPRINT_SCHEME
        # a rework dispatch never memo-skips (above); once it passes, a re-check whose inputs are unchanged keeps the fix
        rt.settled_fingerprint = self.fingerprint(node, rework=[]) if rt.rework_defects else fp
        rt.dispatch_token = f"{nid}-a{rt.attempts}-{fp[:10]}"
        rt.command_code = dict(self.project.command_code(nid)) if self.project.command_for(nid) else {}
        rt.self_written_code, rt.command_code_at_run = {}, {}
        rt.started_at = now()
        ctx = self._context(node, rt.attempts, fp, rt.dispatch_token)
        reads_sources = any(r in ("state:inputs.sources", "state:inputs") or r.startswith("files:sources") for r in node.inputs)
        seen = self._source_bytes() if reads_sources else {}
        self.ledger.append("node_dispatched", node=nid, attempt=rt.attempts, fingerprint=fp[:16],
                           executor=getattr(self.executor, "name", "?"), rework=rt.rework_defects,
                           **({"sources": seen} if seen else {}))
        return ctx

    def _source_bytes(self) -> dict[str, str]:
        """#100: each registered source's bytes (sha256, 16 hex) as a node that reads them is dispatched, so the release
        can name the exact bytes behind a pass even when a source drifts afterwards."""
        out = {}
        for s in self.state.inputs.sources:
            try:
                out[s.id] = sha256_file(self.project.root / s.path)[:16]
            except (OSError, NotRegularFile):
                out[s.id] = "missing"
        return out

    def _carry_forward_review(self, node: NodeDef) -> None:
        if node.kind != NodeKind.REVIEW:
            return
        version = self.state.runtime.current_version
        last = [r for r in self.state.evaluation.reviews if r.node == node.id]
        if last and last[-1].version != version:
            carried = last[-1].model_copy(deep=True)
            carried.id, carried.version, carried.carried_forward, carried.created_at = f"rev-{node.id}-v{version}", version, True, now()
            self.state.evaluation.reviews.append(carried)
            if carried.tool_findings:
                self._record_findings(carried.tool_findings, carried.id, version)
                self._flush_defect_events()

    def _execute(self, ctx: NodeContext) -> NodeResult:
        """Run the node's work. Called from worker threads: must not mutate engine state."""
        node = ctx.node
        try:
            if node.kind == NodeKind.TOOL:
                return resolve_tool(node.run.tool)(ctx)
            if node.run and node.run.tool:
                pre = resolve_tool(node.run.tool)(ctx)
                ctx.pre_result = pre
                ctx.pre_findings = self._govern_findings(pre.findings)
            if node.kind == NodeKind.REVIEW and all(d["check"]["type"] in ("tool", "human") for d in _dims(node, self.rubrics)):
                res = NodeResult(outcome="pass", executor="tool-review")
            else:
                res = self.executor.execute(ctx)
            if ctx.pre_result:
                merged = dict(ctx.pre_result.outputs)
                merged.update(res.outputs)
                res.outputs = merged
                res.data = {**ctx.pre_result.data, **res.data}
            cmd = self.project.command_for(node.id)
            if cmd and not res.pending:
                res = run_command(ctx, cmd, res, int(self.project.config.get("command_timeout_s", 900)))
            return res
        except Exception as exc:  # recorded as a failed attempt, never swallowed
            return NodeResult(outcome="__error__", data={"error": f"{type(exc).__name__}: {exc}",
                                                         "trace": traceback.format_exc()[-1500:]})

    def _govern_findings(self, findings: list[Finding]) -> list[Finding]:
        levels = self._dimension_levels()
        out = []
        for f in findings:
            lvl = levels.get(f.dimension)
            if lvl is None and f.dimension in _all_dim_ids(load_rubrics(PKG_ROOT)):
                continue   # dimension disabled: its rule is inactive
            if lvl == "SHOULD":
                f = f.model_copy(update={"level": "SHOULD", "severity": "nit"})
            elif lvl == "MUST" and f.level != "MUST":
                f = f.model_copy(update={"level": "MUST"})   # the rubric sets the level; a reviewer cannot downgrade it
            out.append(f)
        return out

    def _handle(self, ctx: NodeContext, res: NodeResult) -> None:
        rt = self.rt(ctx.node.id)
        if res.outcome == "__error__":
            self._fail(ctx.node, rt, [f"execution error: {res.data.get('error')}"])
            return
        if res.pending:
            rt.status = S.WAITING_RUNTIME
            rt.task_packet = res.task_packet
            rt.pre_findings = list(ctx.pre_findings)
            if ctx.pre_result:
                rt.result_data = {"pre_outputs": ctx.pre_result.outputs, **ctx.pre_result.data}
            self.ledger.append("task_issued", node=ctx.node.id, attempt=ctx.attempt, packet=res.task_packet, token=ctx.dispatch_token)
            return
        self._complete(ctx, res)

    # ------------------------------------------------------------------ completion
    def _complete(self, ctx: NodeContext, res: NodeResult) -> None:
        node, rt = ctx.node, self.rt(ctx.node.id)
        rt.status = S.VALIDATING
        self._save("judging")   # a crash while this node is judged is charged to it on resume
        failures: list[str] = []
        others = [n for n in self._inflight() if n != node.id]
        accepted_before = {d: self.state.controls[d].sha256 for d in CONTROL_DOCS}
        violations, ambiguous = self.controls.check_changes(node, others)
        changed_docs = [d for d in CONTROL_DOCS if self.state.controls[d].sha256 != accepted_before[d]]
        failures += violations
        if ambiguous:
            self._escalate_run("control-integrity", f"unauthorized edits to {', '.join(d + '.md' for d in ambiguous)} while "
                                                    f"{', '.join([node.id] + others)} were in flight; the editor cannot be identified, "
                                                    "so the edits were reverted and a human must review")
        backup_prod = self.state.production.model_copy(deep=True)
        backup_inputs = self.state.inputs.model_copy(deep=True)
        backup_eval = self.state.evaluation.model_copy(deep=True)
        backup_failures = dict(self.state.runtime.defect_failures)
        self._defect_events = []
        registered: dict[str, list[ArtifactRecord]] = {}
        review, findings, version = None, [], self.state.runtime.current_version
        try:   # anything that raises while judging a submission is a failed attempt with a message, never a traceback
            registered, reg_fail = self._register_outputs(ctx, res)
            failures += reg_fail
            ctx.registered = registered
            if not reg_fail:
                failures += self._apply_state_writes(node, registered)
            if node.kind == NodeKind.REVIEW:
                review, findings, unscored = merge_review(node, version, ctx.pre_findings, res.review, self.rubrics)
                failures += self._bind_review(node, review, res)
                findings = self._govern_findings(findings)
                if unscored:
                    failures.append(f"reviewer left MUST dimensions unscored: {unscored} (score the whole rubric)")
                for s in review.scores:
                    if not s.passed and s.level == "MUST" and not any(f.dimension == s.dimension for f in findings):
                        failures.append(f"dimension {s.dimension} failed without a traced defect")
            if node.gate == "review_gate" and not failures:
                failures += self._apply_interpretation(ctx)
            for chk in node.completion:
                cr = self._run_check(chk, ctx, res, review)
                if not cr.ok:
                    failures += [f"{chk.description} — {m}" for m in (cr.messages or ["failed"])]
        except Exception as exc:
            failures.append(f"judging this submission raised {type(exc).__name__}: {exc}")
        if failures:
            self.state.production, self.state.inputs, self.state.evaluation = backup_prod, backup_inputs, backup_eval
            self.state.runtime.defect_failures, self._defect_events = backup_failures, []
            for recs in registered.values():
                for r in recs:
                    self.state.artifacts[r.id] = r.model_copy(update={"status": "superseded"})
            self._fail(node, rt, failures)
            return
        self._commit_outputs(registered)
        self._flush_defect_events()   # the interpretation's defect changes are kept now, so their events are too
        if changed_docs and not self._recheck_readers(node, changed_docs):
            return   # a re-check cycle escalated: the writer stays BLOCKED and re-runs once a human unblocks it
        data = dict(res.data)
        if review is not None:
            ids = self._record_findings(findings, review.id, version)
            self._flush_defect_events()
            review.defect_ids = sorted(set(ids))
            self.state.evaluation.reviews.append(review)
            data["verdict"] = review.verdict
        outcome = res.outcome
        if node.gate == "review_gate":
            outcome, gdata = self._review_gate(node)
            data.update(gdata)
        elif node.gate == "freeze_inputs" and outcome == "pass":
            self._freeze_inputs()
        elif node.gate == "package":
            self._record_deliverables(ctx)
        if node.human_review.required:
            rt.result_data = data
            if self.auto_approve and node.human_review.dry_run_auto and self.state.runtime.dry_run:
                decision = node.human_review.outcomes[0]
                locked = self._lock_on_approval(node, decision, self.wf.policies.dry_run_actor)
                self._record_approval(node, decision, self.wf.policies.dry_run_actor, human=False,
                                      reason="dry-run auto-approval — NOT a human decision", locked=locked)
                self._pass(node, rt, decision, data)
            else:
                rt.status = S.WAITING_HUMAN
                self.ledger.append("awaiting_human", node=node.id, decision=node.human_review.decision,
                                   approver=node.human_review.approver)
            return
        if outcome not in node.outcomes:
            self._fail(node, rt, [f"outcome '{outcome}' is not one of {node.outcomes}"])
            return
        if self._preflight_due(node):
            self._request_preflight(node, rt, outcome, data)
            return
        self._pass(node, rt, outcome, data)

    # ------------------------------------------------------------------ independent pre-flight (v1.6)
    def _preflight_node_problems(self) -> list[str]:
        """A pre-flight holds a producer's finished attempt. A review, a gate, the freeze or the package node has already
        acted when it finishes (defects filed, a gate decided), which a revision can't take back; a human gate is decided
        by its human; and a per-item node's items pass one by one."""
        block = self.project.config.get("preflight")
        if not isinstance(block, dict) or not isinstance(block.get("nodes"), list):
            return []
        out = []
        for nid in block["nodes"]:
            if nid not in self.index.ids:
                continue
            n = self.node(nid)
            why = ("a review node" if n.kind == NodeKind.REVIEW else f"a {n.gate} node" if n.gate else
                   "a human-gated node" if n.human_review.required else "a per-item node" if n.map_over else None)
            if why:
                out.append(f"preflight.nodes names {nid}, {why}; only a producer's attempt can be held")
        return out

    def _preflight_revisions(self) -> int | None:
        """How many consecutive pre-flight revisions a node may take before a human is asked (`preflight.max_revisions`;
        None: the node's own retry budget). A revision is a quality iteration, so a project may allow more of them than
        technical failures; the bound stays, as the runaway guard."""
        block = self.project.config.get("preflight")
        return block.get("max_revisions") if isinstance(block, dict) else None

    def _preflight_due(self, node: NodeDef) -> bool:
        """The project asks for an independent check of this node's finished attempt before the graph spends a review
        round on it: `preflight: {nodes: [...], when: rework | always}` (rework: only once a rework cycle has started)."""
        block = self.project.config.get("preflight")
        if not isinstance(block, dict) or node.id not in (block.get("nodes") or []):
            return False
        return block.get("when", "rework") == "always" or self.state.runtime.rework_cycles > 0

    # ------------------------------------------------------------------ review binding and independence (v1.7, #62/#67)
    def _judged_producers(self, node: NodeDef) -> dict[str, set[str]]:
        """Each artifact a review node judges → the identities that produced its current files: the attempt's submitter
        and the producing node's bound agent."""
        submitted = {(e["data"].get("node"), e["data"].get("attempt")): e["data"].get("actor")
                     for e in self.ledger.events("node_submitted") if e["data"].get("actor")}
        out: dict[str, set[str]] = {}
        for ref in node.inputs:
            kind, _, name = ref.partition(":")
            if kind != "artifact":
                continue
            for a in self.current(name):
                keys = out.setdefault(name, set())
                if submitted.get((a.node, a.attempt)):
                    keys.add(self._actor_key(submitted[(a.node, a.attempt)]))
                if a.node in self.index.ids and self.node(a.node).agent:
                    keys.add(self._actor_key(self.node(a.node).agent))
        return out

    def _shared_with_producers(self, node: NodeDef, actor: str) -> list[str]:
        key = self._actor_key(actor)
        return sorted(name for name, keys in self._judged_producers(node).items() if key and key in keys)

    def _review_bind_problems(self, node: NodeDef, reviewed: Any, actor: str) -> list[str]:
        out = binding_problems(review_bindings(node, self.current), reviewed, self.project.review_policy("binding") == "require")
        if actor.startswith("agent:") and self.project.review_policy("independence") == "require":
            shared = self._shared_with_producers(node, actor)
            if self._anonymous(actor):
                out.append("a review names its reviewer: --actor agent:<id> (vpg.project.yaml reviews.independence: require)")
            elif shared:
                out.append(f"{actor} produced {', '.join(shared)}, which this review judges; an independent reviewer submits "
                           "it (vpg.project.yaml reviews.independence: require)")
        return out

    def _bind_review(self, node: NodeDef, review: Any, res: NodeResult) -> list[str]:
        """Record what the review judged and who judged it; return why it can't count (the same rules as a submission,
        for executors that don't submit). The reviewer is the attempt's submitter; for an executor that doesn't submit,
        the agent it names (never a bound command's `+command` suffix)."""
        echo = res.review.get("reviewed") if isinstance(res.review, dict) else None
        submitter = self._producer(node.id, self.rt(node.id).attempts)
        named = (res.executor or "").split("+", 1)[0]
        actor = (self._agent_identity(submitter) if submitter != "engine" else
                 self._agent_identity(named) if named.strip().lower().startswith("agent:") else "")
        review.actor = actor or (res.executor or "")
        review.inputs = {k: v[:16] for k, v in review_bindings(node, self.current).items()}
        review.reviewed = {str(k): str(v).strip().lower()[:16] for k, v in echo.items()} if isinstance(echo, dict) else {}
        review.independent = (not self._shared_with_producers(node, actor)
                              if actor.startswith("agent:") and not self._anonymous(actor) else None)
        if not isinstance(res.review, dict):   # the engine's own tools judged the current bytes: nothing to bind
            return []
        return self._review_bind_problems(node, echo, actor)

    def _producer(self, node_id: str, attempt: int) -> str:
        """Who submitted this attempt (the engine itself for a command with no agent)."""
        actors = [e["data"].get("actor") for e in self.ledger.events("node_submitted")
                  if e["data"].get("node") == node_id and e["data"].get("attempt") == attempt]
        return (actors[-1] if actors and actors[-1] else "engine")

    def _attempt_outputs(self, node_id: str, attempt: int) -> dict[str, tuple[str, str]]:
        """This attempt's current artifacts: key[item] → (path, sha256)."""
        return {a.key + (f"[{a.item}]" if a.item else ""): (a.path, a.sha256) for a in self.state.artifacts.values()
                if a.node == node_id and a.attempt == attempt and a.status == "current"}

    def _request_preflight(self, node: NodeDef, rt: NodeRuntime, outcome: str, data: dict[str, Any]) -> None:
        """Hold a finished attempt (outputs registered, checks passed, no version cut, nothing downstream started) until
        an independent reviewer passes it or returns it with notes (`vpg preflight`)."""
        outputs = self._attempt_outputs(node.id, rt.attempts)
        producer = self._producer(node.id, rt.attempts)
        token = f"{node.id}-a{rt.attempts}-preflight-{sha256_json({k: v[1] for k, v in outputs.items()})[:10]}"
        tasks = self.project.vpg / "tasks"
        tasks.mkdir(parents=True, exist_ok=True)
        stem = f"{node.id}-a{rt.attempts}-preflight"
        here = rel(self.project.root, Path.cwd())
        defects = [d for d in self.state.evaluation.defects if d.id in rt.rework_defects]
        cmd = f"python -m vpg preflight {here} {node.id} --token {token} --actor agent:<your id>"
        (tasks / f"{stem}.md").write_text(
            f"# Pre-flight — N{node.num:02d} {node.name} (attempt {rt.attempts})\n\n"
            "Before the graph spends a review round on this attempt, an independent reviewer checks it.\n\n"
            f"- **Token:** `{token}`\n"
            f"- **Produced by:** `{producer}`. The verdict must come from someone else: another agent id or a named human.\n"
            f"- **Rework cycle:** {self.state.runtime.rework_cycles}. No version is cut until the pre-flight passes.\n\n"
            "## What this attempt was meant to fix\n"
            + ("\n".join(f"- {d.id} [{d.severity}] {d.dimension}: {d.description} | accept when: {d.acceptance_check}" for d in defects)
               or "- no routed defect (a re-check); judge it against the rubric and the last review round")
            + "\n\n## Files to judge (the verdict is refused if any of them changes)\n"
            + ("\n".join(f"- `{k}` → `{path}` (sha256 `{sha[:16]}`)" for k, (path, sha) in sorted(outputs.items())) or "- none")
            + f"\n\n## Record the verdict (from the vpg checkout: `{PKG_ROOT}`)\n"
            f"```\n{cmd} --verdict pass [--notes notes.md]\n{cmd} --verdict revise --notes notes.md\n```\n"
            "A revision fails this attempt with your notes as its failure reasons. The producer then revises it, and no "
            "version or review round is spent. Repeated revisions escalate like any failed attempt.\n")
        rt.preflight = {"token": token, "attempt": rt.attempts, "outcome": outcome, "data": data, "producer": producer,
                        "outputs": {k: sha for k, (_, sha) in outputs.items()}, "requested_at": now()}
        rt.status, rt.task_packet = S.WAITING_RUNTIME, rel(tasks / f"{stem}.md", self.project.root)
        self.ledger.append("preflight_requested", node=node.id, attempt=rt.attempts, producer=producer,
                           outputs={k: sha[:16] for k, (_, sha) in outputs.items()}, packet=rt.task_packet)
        self._checkpoint("preflight_requested", node.id)

    def _anonymous(self, actor: str | None) -> bool:
        """No identity at all: empty, `agent:`, `cli` or a dry-run actor, in any spelling. Never a reviewer, never independent."""
        key = self._actor_key(actor)
        return not key or key == "cli" or key.startswith("dry-run") or key == self._actor_key(self.wf.policies.dry_run_actor)

    def _agent_identity(self, actor: str | None) -> str:
        """The one spelling of a submitting agent's identity: `agent:<key>`, case-free (`AGENT:X`, `x` and `agent:x` agree)."""
        return f"agent:{self._actor_key(actor)}"

    @staticmethod
    def _actor_key(actor: str | None) -> str:
        """One identity per actor, as the engine's other checks see it: case-free, with or without `agent:`."""
        a = (actor or "").strip().lower()
        return a[len("agent:"):].strip() if a.startswith("agent:") else a

    @_human_action("preflight")
    def preflight(self, node_id: str, token: str, verdict: str, actor: str, notes: str = "") -> dict[str, Any]:
        """`vpg preflight`: an independent reviewer passes a held attempt (it then passes as it would have) or returns it
        with notes (a failed attempt). Refused unless the reviewer is not the attempt's producer or the node's bound agent,
        and every file it was shown is byte-identical on disk."""
        node, rt = self.node(node_id), self.rt(node_id)
        pf = rt.preflight
        if rt.status != S.WAITING_RUNTIME or not pf:
            raise EngineError(f"{node_id} is not waiting for a pre-flight verdict ({rt.status.value})")
        if token != pf["token"]:
            raise StaleSubmission(f"stale pre-flight for {node_id}: token {token} ≠ current {pf['token']}")
        if verdict not in ("pass", "revise"):
            raise EngineError("the pre-flight verdict is pass or revise")
        actor = (actor or "").strip()
        if self._anonymous(actor):
            raise EngineError("a pre-flight verdict needs the reviewer's identity: --actor agent:<id>, or a named human")
        taken = {self._actor_key(pf["producer"])} | ({self._actor_key(node.agent)} if node.agent else set())
        if self._actor_key(actor) in taken:
            raise EngineError(f"the pre-flight must be independent of the work it judges: {actor} produced attempt "
                              f"{pf['attempt']} of {node_id} (or is its bound agent)")
        blocked_by = self._integrity_problems()
        if blocked_by:
            raise EngineError("the run has an integrity problem, so no verdict is accepted: " + "; ".join(blocked_by))
        now_out = self._attempt_outputs(node_id, pf["attempt"])
        changed = sorted(k for k, sha in pf["outputs"].items()
                         if k not in now_out or not (self.project.root / now_out[k][0]).is_file()
                         or sha256_file(self.project.root / now_out[k][0]) != sha)
        if changed:
            raise EngineError(f"the files the pre-flight was asked to judge changed since it was requested ({', '.join(changed)}); "
                              f"nothing was recorded. Re-run the node: `vpg invalidate {rel(self.project.root, Path.cwd())} {node_id} "
                              "--reason …`")
        notes = (notes or "").strip()
        if verdict == "revise" and not notes:
            raise EngineError("a revise verdict needs the reviewer's notes (--notes): what to change, and how to check it")
        rt.preflight = None
        judged = {k: sha[:16] for k, sha in pf["outputs"].items()}
        if verdict == "pass":
            self.ledger.append("preflight_passed", node=node_id, attempt=pf["attempt"], actor=actor, outputs=judged, notes=notes[:2000])
            data = {**pf["data"], "preflight": {"by": actor, "verdict": "pass", "outputs": judged}}
            self._pass(node, rt, pf["outcome"], data)
        else:
            self.ledger.append("preflight_revise", node=node_id, attempt=pf["attempt"], actor=actor, outputs=judged, notes=notes[:2000])
            rt.force_rerun = True   # set before _fail checkpoints: its files stay registered, and must never memo-pass
            self._fail(node, rt, [f"the independent pre-flight by {actor} returned this attempt before any review round: {notes}"],
                       limit=self._preflight_revisions(), revision=True)
        self._update_status()
        self._save("preflight")
        return self.status()

    def _recheck_readers(self, writer: NodeDef, docs: list[str]) -> bool:
        """A control document changed at a writer's completion: nodes that already passed while reading it are re-checked
        (they re-run or memo-skip by fingerprint). Upstream readers are left alone: for them the edit is for later work.

        Each re-checked node carries the depth of the re-check chain that sent it back. The validator rejects graphs
        whose re-checks can cycle (graph.recheck_targets); as a run-time bound, a chain longer than the number of
        control writers must revisit a writer, so it is a cycle and escalates. Returns False when it escalated."""
        upstream = self.index.ancestors(writer.id)
        readers = [n.id for n in self.wf.nodes if n.id != writer.id and n.id not in upstream and self.rt(n.id).status == S.STEP_PASS
                   and control_reads(n) & set(docs)]
        if not readers:
            return True
        depth, reason = self.rt(writer.id).rechecks + 1, f"{writer.id} changed {', '.join(d + '.md' for d in docs)}, which these nodes read"
        limit = sum(1 for n in self.wf.nodes if n.controls.write)
        self.invalidate(readers, reason=reason, recheck_depth=depth)   # nothing that read the old document stays passed
        if depth > limit:
            self._escalate(writer, f"a chain of {depth} control-document re-checks came back round (re-check cycle)",
                           [f"{writer.id} changed {', '.join(d + '.md' for d in docs)}, re-checking {', '.join(readers)}; this chain "
                            f"of re-checks is longer than the {limit} control writers, so the nodes keep re-running each other"],
                           advice=f"- Order these nodes in the flow so one runs after the other (a graph change needs `vpg accept-config`), "
                                  f"then run `vpg unblock {rel(self.project.root, Path.cwd())} {writer.id} --actor <name>`; unblocking "
                                  "restarts the re-check chains.")
            return False
        return True

    def _register_outputs(self, ctx: NodeContext, res: NodeResult) -> tuple[dict[str, list[ArtifactRecord]], list[str]]:
        node = ctx.node
        registered: dict[str, list[ArtifactRecord]] = {}
        failures = []
        lineage = [a.id for ref in node.inputs if ref.startswith("artifact:") for a in self.current(ref.split(":", 1)[1])]
        for spec in node.outputs:
            val = res.outputs.get(spec.key)
            if val is None:
                if spec.required and not (spec.per_item and self._items_complete(node, spec.key)):
                    failures.append(f"missing required output '{spec.key}'")
                continue
            entries = list(val.items()) if isinstance(val, dict) else [(None, v) for v in (val if isinstance(val, list) else [val])]
            recs = []
            for i, (item, v) in enumerate(entries):
                p = ctx.output_path(spec.key, v).resolve()
                if not p.is_relative_to(self.project.root):
                    failures.append(f"output '{spec.key}' points outside the project: {v}")
                    continue
                if not p.exists():
                    failures.append(f"output '{spec.key}' file is missing: {rel(p, self.project.root)}")
                    continue
                suffix = f"[{item}]" if item else (f"[{i}]" if len(entries) > 1 else "")
                recs.append(ArtifactRecord(id=f"{spec.key}@{node.id}#a{ctx.attempt}{suffix}", key=spec.key,
                                           path=rel(p, self.project.root), sha256=sha256_file(p), bytes=p.stat().st_size,
                                           node=node.id, attempt=ctx.attempt, item=item, produced_at=now(), inputs=lineage,
                                           tool=res.executor, version=self.state.runtime.current_version))
            registered[spec.key] = recs
        return registered, failures

    def _items_complete(self, node: NodeDef, key: str) -> bool:
        specs = get_path(self.state, node.map_over) if node.map_over else None
        have = {a.item for a in self.current(key)}
        return bool(specs) and all(s["id"] in have for s in specs)

    def _commit_outputs(self, registered: dict[str, list[ArtifactRecord]]) -> None:
        for key, recs in registered.items():
            items = {r.item for r in recs}
            for a in list(self.state.artifacts.values()):
                if a.key == key and a.status == "current" and (a.item in items or None in items):
                    self.state.artifacts[a.id] = a.model_copy(update={"status": "superseded"})
            for r in recs:
                self.state.artifacts[r.id] = r

    def _apply_state_writes(self, node: NodeDef, registered: dict[str, list[ArtifactRecord]]) -> list[str]:
        """Apply declared output mappings, then re-validate every touched state section against the typed model.

        `state_path` records where an artifact lives (a project-relative path; per-item outputs give {item: path}).
        `state_fields` copies named fields of a JSON artifact into typed state. Nothing else is written.
        """
        failures = []
        declared = set(node.state_write)
        touched: set[str] = set()

        def allowed(path: str) -> bool:
            return any(path == d or path.startswith(d + ".") for d in declared)

        for spec in node.outputs:
            recs = registered.get(spec.key)
            if not recs:
                continue
            targets = ([spec.state_path] if spec.state_path else []) + list(spec.state_fields.values())
            bad = [t for t in targets if not allowed(t)]
            bad += [t for t in targets if t.startswith("inputs.") and self.state.inputs.frozen]
            if bad:
                failures += [f"output '{spec.key}' may not write state path {t} (undeclared or frozen)" for t in bad]
                continue
            if spec.state_path:
                if spec.per_item:
                    merged = dict(get_path(self.state, spec.state_path) or {})
                    merged.update({r.item: r.path for r in recs})
                    set_path(self.state, spec.state_path, merged)
                else:
                    set_path(self.state, spec.state_path, recs[-1].path)
                touched.add(spec.state_path.split(".")[0])
            if spec.state_fields:
                try:
                    data = read_json(self.project.root / recs[-1].path)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    failures.append(f"output '{spec.key}' is not JSON: {exc}")
                    continue
                for field, path in spec.state_fields.items():
                    if field in data:
                        set_path(self.state, path, data[field])
                        touched.add(path.split(".")[0])
        for section in touched:
            model = type(getattr(self.state, section))
            try:
                setattr(self.state, section, model.model_validate(getattr(self.state, section).model_dump(mode="json", warnings=False)))
            except ValidationError as exc:
                failures += [f"state {section}.{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors()]
            except Exception as exc:
                failures.append(f"state section '{section}' violates the typed model: {exc}")
        self.controls.state = self.state
        return failures

    def _run_check(self, chk, ctx: NodeContext, res: NodeResult, review) -> CheckResult:
        if chk.type == "artifact":
            recs = ctx.current(chk.key)
            if not recs:
                return CheckResult(False, [f"artifact '{chk.key}' was not produced"])
            if chk.schema_name:
                schema = read_json(PKG_ROOT / "schemas" / "artifacts" / f"{chk.schema_name}.schema.json")
                validator = jsonschema.validators.validator_for(schema)(schema)
                msgs = []
                for r in recs:
                    try:
                        data = read_json(self.project.root / r.path)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        msgs.append(f"{r.path}: not valid JSON ({exc})")
                        continue
                    msgs += [f"{r.path} {err.json_path}: {err.message}" for err in sorted(validator.iter_errors(data), key=lambda x: list(x.path))]
                return CheckResult(not msgs, msgs)
            return CheckResult(True)
        if chk.type == "tool":
            return resolve_tool(chk.fn)(ctx, **chk.args)
        if chk.type == "state":
            ok = bool(get_path(self.state, chk.key))
            return CheckResult(ok, [] if ok else [f"state {chk.key} is empty"])
        if chk.type == "feedback_interpreted":
            open_fb = [f.id for f in self.state.evaluation.feedback if f.status == "open"]
            return CheckResult(not open_fb, [f"uninterpreted feedback: {open_fb}"] if open_fb else [])
        if chk.type == "defects_valid":
            msgs = [f"defect {d.id} traces to unknown node {d.traced_node}" for d in self.state.evaluation.defects
                    if d.traced_node not in self.index.ids]
            return CheckResult(not msgs, msgs)
        if chk.type == "review_complete":
            return CheckResult(review is not None, [] if review is not None else ["no review record"])
        return CheckResult(False, [f"unknown check type {chk.type}"])

    # ------------------------------------------------------------------ gates and routing
    def _apply_interpretation(self, ctx: NodeContext) -> list[str]:
        """Apply the interpreter's output: new defects, and a mapping for every open feedback item."""
        data = ctx.load("interpretation") or {}
        version = self.state.runtime.current_version
        failures = []
        self._interpreter_resolved = [r["id"] if isinstance(r, dict) else r for r in data.get("resolved", [])]
        self._interpreted_now = []
        new = [Finding(check=f"interpretation:{x.get('feedback', 'review')}", dimension=x["dimension"], severity=x["severity"],
                       message=x["description"], evidence=x.get("evidence", ""), traced_node=x["traced_node"],
                       artifact_key=x.get("artifact_key"), smallest_fix=x.get("smallest_fix", ""),
                       element=str(x["element"]) if x.get("element") else None)
               for x in data.get("defects", [])]
        created = self._record_findings(new, "interpretation", version) if new else []
        for i, x in enumerate(data.get("defects", [])):
            if x.get("feedback"):
                d = next(d for d in self.state.evaluation.defects if d.id == created[i])
                d.source = f"feedback:{x['feedback']}"
        defects_by_id = {d.id: d for d in self.state.evaluation.defects}
        for did, fix in (data.get("fixes") or {}).items():
            d = defects_by_id.get(did)
            if d is None or d.status not in ("open", "persisting"):
                failures.append(f"interpretation gives a fix for {did}, which is not an open defect")
            elif str(fix).strip():
                d.smallest_fix = str(fix).strip()
        by_id = {f.id: f for f in self.state.evaluation.feedback}
        for m in data.get("feedback", []):
            fb = by_id.get(m["id"])
            if fb is None:
                failures.append(f"interpretation maps unknown feedback {m['id']}")
                continue
            if fb.status in ("triage", "dismissed"):
                failures.append(f"interpretation maps {fb.id}, an agent's note that "
                                + ("awaits a human's triage" if fb.status == "triage" else "a human dismissed") + "; leave it out")
                continue
            ids = list(m.get("defect_ids", []))
            match = m.get("matches")
            if match:
                ids += [d.id for d in self.state.evaluation.defects if d.status in ("open", "persisting")
                        and all(getattr(d, k) == v for k, v in match.items())]
            for i, x in enumerate(data.get("defects", [])):
                if x.get("feedback") == fb.id:
                    ids.append(created[i])
            if not ids and not m.get("no_action_reason"):
                failures.append(f"feedback {fb.id} is mapped to no defect and gives no no-action reason")
                continue
            unknown = sorted({i for i in ids if i not in {d.id for d in self.state.evaluation.defects}})
            if unknown:
                failures.append(f"feedback {fb.id} is mapped to unknown defect(s) {unknown}")
                continue
            fb.defect_ids = sorted(set(ids))
            fb.no_action_reason = m.get("no_action_reason", "")
            fb.status = "interpreted"
            self._interpreted_now.append(fb.id)
            if not self._feedback_live(fb) and self._humans_feedback(fb):
                # An agent may not overrule a human: feedback that leads to no open blocking work (no action, a closed
                # defect, a nit) waits for its author or the gate's owner.
                why = fb.no_action_reason or f"mapped only to closed or non-blocking defect(s) {', '.join(fb.defect_ids)}"
                fb.hold = f"{fb.actor}'s feedback leads to no open blocking defect ({why})"
                self._defect_events.append({"event": "feedback_held", "id": fb.id, "actor": fb.actor, "reason": why})
        return failures

    def _review_gate(self, node: NodeDef) -> tuple[str, dict[str, Any]]:
        version = self.state.runtime.current_version
        plan = self.state.runtime.nodes.get(self._regen_node(), NodeRuntime()).result_data
        changed = {}
        for key, old in (plan.get("baseline") or {}).items():
            new = [a.sha256 for a in self.current(key)]
            if new != old:
                changed[key] = [",".join(h[:12] for h in old), ",".join(h[:12] for h in new)]
        # Every unresolved defect, not only those in rework: one fixed outside a rework cycle (an approved change request)
        # is resolved by the fresh review that no longer reports it, so it cannot hold the gate.
        this_round = compare_after_rework(self.state, version, changed, set(self._interpreter_resolved))
        for fb in self.state.evaluation.feedback:   # a mapping this gate made can close in this same gate
            if (fb.id in self._interpreted_now and not fb.hold and not self._feedback_live(fb)
                    and self._humans_feedback(fb)):
                why = f"its defect(s) {', '.join(fb.defect_ids)} closed at the gate that mapped them, with no rework"
                fb.hold = f"{fb.actor}'s feedback leads to no open blocking defect ({why})"
                self.ledger.append("feedback_held", id=fb.id, actor=fb.actor, reason=why)
        loopable = {e.when.split(":", 1)[1] for e in self.index.outgoing(self._regen_node()) if e.when.startswith("target:")}
        for d in self.state.evaluation.defects:
            blocking = d.status in ("open", "persisting") and d.severity in self.wf.policies.blocking_severities
            # recomputed from the current routing (an approved change request counts) and the defect's hold
            d.needs_human = blocking and bool(d.human_hold)
            if not blocking:
                continue
            if d.traced_node not in loopable:
                d.needs_human = True   # e.g. product truth: only a human change request can alter it
                continue
            locked = [doc for doc in self.node(d.traced_node).controls.write
                      if self.state.controls[doc].status == "approved" and self.controls._open_cr(doc) is None]
            if locked:
                d.needs_human = True   # the target owns locked controls; rework needs an approved change request
        snapshot = gate_snapshot(self.state, version, self.wf.policies.blocking_severities, this_round)
        policies = self.wf.policies.model_copy(update={"max_rework_cycles": self._max_rework_cycles()})
        self._refused_rework = None
        g = gate(self.state, policies, version, self.reviewer_nodes(), node.id, extend=lambda: self._extend_rework(snapshot))
        g.trend = snapshot
        if g.outcome == "blocked" and self._refused_rework:
            g.reasons.append(f"not extended: {self._refused_rework}")
        self.state.evaluation.gate_results.append(g)
        self.ledger.append("gate", node=node.id, version=version, outcome=g.outcome, reasons=g.reasons, open_blocking=g.open_blocking)
        holds = ([d.id for d in self.state.evaluation.defects if d.human_hold and d.status in ("open", "persisting", "in_rework")]
                 + [f.id for f in self.state.evaluation.feedback if f.hold])
        return g.outcome, {"gate": g.id, "reasons": g.reasons, "open_blocking": g.open_blocking, "holds": holds}

    def _regen_node(self) -> str:
        return next(n.id for n in self.wf.nodes if n.gate == "regeneration_plan")

    def _gate_node(self) -> str:
        return next(n.id for n in self.wf.nodes if n.gate == "review_gate")

    def _needs_a_human(self, d: Defect) -> str:
        """Why a defect is a human's to close, or '' (#18: what a grant never reaches)."""
        human_dims = {x["id"] for r in self.rubrics.values() for x in r.get("dimensions", []) if x["check"]["type"] == "human"}
        fb = next((f for f in self.state.evaluation.feedback if self._humans_feedback(f)
                   and (d.source == f"feedback:{f.id}" or d.id in f.defect_ids)), None)   # created from it, or mapped onto it
        return ("is on a human-judged dimension" if d.dimension in human_dims else
                "is held for a named human" if d.human_hold else
                "needs a human decision" if d.needs_human else
                f"comes from {fb.actor}'s feedback" if fb is not None and self._humans_feedback(fb) else "")

    def _humans_feedback(self, fb: Feedback) -> bool:
        """A named human's feedback, or an agent's note a human accepted at triage (#14): an agent may not overrule either."""
        return is_human_name(fb.actor, self.wf.policies.dry_run_actor) or fb.triage == "accepted"

    def _feedback_live(self, fb: Feedback) -> bool:
        """The feedback leads to at least one unresolved defect of a blocking severity."""
        known = {d.id: d for d in self.state.evaluation.defects}
        return any(i in known and known[i].status in ("open", "persisting", "in_rework")
                   and known[i].severity in self.wf.policies.blocking_severities for i in fb.defect_ids)

    def _record_findings(self, findings: list[Finding], source: str, version: int) -> list[str]:
        """Record findings as defects. Their identity changes (re-trace, severity change) are buffered and ledgered by
        `_flush_defect_events` once the state that holds them is kept.

        A reviewer that re-traces a problem off a human-gated node (a human gate, or a locked approved document) onto
        one rework can reach puts the defect on hold: the gate blocks until a named human unblocks it, so a re-trace
        never routes around a human decision."""
        log: list[dict[str, Any]] = []
        ids = record_findings(self.state, findings, source, version, default_trace=self._gate_node(), log=log)
        by_id = {d.id: d for d in self.state.evaluation.defects}
        for e in log:
            if e["event"] == "defect_retraced" and self._human_gated(e["from"]) and not self._human_gated(e["to"]):
                d = by_id[e["id"]]
                if not d.human_hold:
                    d.human_hold = f"{e['by']} re-traced it from {e['from']}, which only a named human may change, to {e['to']}"
                    log.append({"event": "defect_held", "id": d.id, "reason": d.human_hold})
        self._defect_events.extend(log)
        return ids

    def _human_gated(self, node_id: str) -> bool:
        """Only a named human may change this node's output: it has a human gate, or it writes an approved (locked)
        control document with no approved change request."""
        if node_id not in self.index.ids:
            return False
        n = self.node(node_id)
        return n.human_review.required or any(self.state.controls[doc].status == "approved" and self.controls._open_cr(doc) is None
                                              for doc in n.controls.write)

    def _code_changed_since_dispatch(self, node_id: str, since_run: bool = False) -> list[str]:
        """Code files that changed since the node was dispatched (or, with since_run, since its command started)."""
        rt = self.rt(node_id)
        base = (rt.command_code_at_run or rt.command_code) if since_run else rt.command_code
        if not base or not self.project.command_for(node_id):
            return []
        now_code = dict(self.project.command_code(node_id))
        return sorted(p for p in set(base) | set(now_code) if base.get(p) != now_code.get(p))

    def _flush_defect_events(self) -> None:
        events, self._defect_events = self._defect_events, []
        for e in events:
            self.ledger.append(e.pop("event"), **e)

    def _apply_regeneration_plan(self, data: dict[str, Any]) -> None:
        """Start the rework a regeneration plan routes: one rework cycle, and its defects in rework. Called only once every
        loop the plan needs is within its bound (_pass), so a plan that blocks spends nothing."""
        self.state.runtime.rework_cycles += 1
        for d in self.state.evaluation.defects:
            if d.id in data.get("defect_ids", []):
                d.status = "in_rework"
        self.ledger.append("regeneration_plan", cycle=self.state.runtime.rework_cycles, targets=data.get("targets"),
                           defects=data.get("defect_ids"), preserved=data.get("preserved"))

    def _freeze_inputs(self) -> None:
        inp = self.state.inputs
        inp.frozen, inp.frozen_at = True, now()
        inp.frozen_sha256 = sha256_json(inp.model_dump(exclude={"frozen", "frozen_sha256", "frozen_at"}))
        self.ledger.append("inputs_frozen", sha256=inp.frozen_sha256[:16], sources=len(inp.sources))
        self._keep_sources()

    @functools.cached_property
    def source_store(self) -> SourceStore:
        return SourceStore(self.project.root, self.project.vpg)

    def _keep_sources(self) -> bool:
        """Copy each frozen source that still matches its registered hash to .vpg/sources/<sha256> (SourceStore.keep)."""
        kept = self.source_store.keep(self.state.inputs.sources)
        if kept:
            self.ledger.append("sources_kept", sources=kept)
        return bool(kept)

    def _source_warnings(self) -> list[str]:
        return self.source_store.warnings(self.state.inputs.sources) if self.state.inputs.frozen else []

    def _record_deliverables(self, ctx: NodeContext) -> None:
        from .models import Deliverable
        manifest = ctx.load("manifest") or {}
        self.state.deliverables = [Deliverable(key=f["path"], path=f["path"], sha256=f["sha256"]) for f in manifest.get("files", [])]

    def _pass(self, node: NodeDef, rt: NodeRuntime, outcome: str, data: dict[str, Any]) -> None:
        rt.status, rt.outcome, rt.result_data, rt.finished_at = S.STEP_PASS, outcome, data, now()
        rt.last_pass_fingerprint, rt.failures, rt.rework_defects, rt.fail_streak = rt.settled_fingerprint or rt.fingerprint, [], [], 0
        rt.preflight_revisions = 0
        rt.last_pass_scheme = rt.fingerprint_scheme
        rt.interruptions, rt.executed = 0, False
        if any(o.state_path == "production.master_ref" for o in node.outputs):
            self.state.runtime.current_version += 1
            v = self.state.runtime.current_version
            for a in self.state.artifacts.values():
                if a.node == node.id and a.attempt == rt.attempts:
                    a.version = v
            self.state.history.versions.append(VersionRecord(
                version=v, created_at=now(), node_attempt=f"{node.id}#a{rt.attempts}",
                artifacts={a.key + (f"[{a.item}]" if a.item else ""): a.sha256 for a in self.state.artifacts.values() if a.status == "current"}))
            self._queue_publish(v)
        self.ledger.append("node_passed", node=node.id, attempt=rt.attempts, outcome=outcome)
        if node.gate == "package":   # the released version is published again with its package
            self._queue_publish(self.state.runtime.current_version)
        changed = sorted(rt.self_written_code)
        if changed:
            # #105: written by its own command while it ran (recorded at _executed). The code as the node left them is its
            # pass's code, so a rework cycle or an accepted configuration doesn't re-run it for them; only a later edit does.
            self.ledger.append("command_code_changed", node=node.id, files=changed[:20],
                               note="changed while the node was in flight; if its command writes these files, list them "
                                    "in command_code_exclude (until then they count as its output, not its code)")
        loops, spent = [], []
        for e in self.index.outgoing(node.id):
            if e.kind == EdgeKind.FLOW or not outcome_matches(e.when, outcome, data):
                continue
            if e.kind == EdgeKind.ESCALATE:
                self._escalate(node, f"{e.id}: {e.branching}", data.get("reasons") or [outcome], holds=data.get("holds"))
                continue
            loops.append(e)
            if self._loop_spent(rt, e):
                spent.append(e)
        # Every bound is checked before anything moves (#77): a plan that needs a spent loop traverses no loop, spends no
        # rework cycle and puts no defect in rework, and one escalation names every spent loop and the node's budgets.
        # A converging run's rework loops are extended first when the project's loop_budget allows it (budget.py).
        refused = None
        if spent and node.gate == "regeneration_plan":
            spent, refused = self._extend_loops(node, rt, spent)
        if spent:
            self._escalate(node, "; ".join(f"loop {e.id} has no traversal left ({rt.traversals.get(e.id, 0)} of "
                                           f"{self._bound(e)} used)" for e in spent),
                           [e.on_fail for e in spent] + [f"loop budgets of {node.id}: {self._loop_budgets(node.id)}"]
                           + ([f"not extended: {refused}"] if refused else []),
                           edge=spent[0].id, edges=[e.id for e in spent])
        elif node.gate == "regeneration_plan" and not loops and rt.status != S.BLOCKED:
            self._escalate(node, "the regeneration plan has no routable target, so rework cannot start",
                           [f"unroutable defects: {data.get('unroutable') or 'none'}; blocking defects must trace to a node N22 can loop to"])
        elif loops and rt.status != S.BLOCKED:
            for e in loops:
                rt.traversals[e.id] = rt.traversals.get(e.id, 0) + 1
            targets = [e.target for e in loops]
            extra = []
            if node.gate == "regeneration_plan":
                self._apply_regeneration_plan(data)
                # fixed code rides along with the rework: a command node whose code changed re-runs in this cycle (#99)
                extra = [n for n in self._code_changed_nodes() if n not in targets]
                if extra:
                    self.ledger.append("code_changed_rerun", nodes=extra, cycle=self.state.runtime.rework_cycles,
                                       reason="their code changed since they last ran, so this rework cycle runs the new code")
            self.invalidate(targets + extra, reason=f"{node.id} → {', '.join(e.id for e in loops)} ({outcome})",
                            defects_by_target=data.get("defects_by_target"))
        self._checkpoint("node_passed", node.id)

    def _loop_edges(self, node_id: str) -> list[EdgeDef]:
        return [e for e in self.index.outgoing(node_id) if e.kind == EdgeKind.LOOP]

    @functools.cached_property
    def budget(self):
        """The project's loop_budget (budget.py); the workflow's own bounds when it declares none."""
        return load_budget(self.project.config)

    def _rework_loop(self, e: EdgeDef) -> bool:
        return any(n.id == e.source and n.gate == "regeneration_plan" for n in self.wf.nodes)

    def _bound(self, e: EdgeDef) -> int:
        """A loop's traversal bound in this run: the project's loop_budget.max_traversals for rework loops (else the
        edge's own), plus what converging review rounds were granted (runtime.loop_extra)."""
        base = e.max_traversals or 0
        if self.budget.max_traversals and self._rework_loop(e):
            base = max(base, self.budget.max_traversals)   # a profile widens a rework loop; it never narrows one
        return base + self.state.runtime.loop_extra.get(e.id, 0)

    def _max_rework_cycles(self) -> int:
        return (self.budget.rework_cycles or self.wf.policies.max_rework_cycles) + self.state.runtime.rework_extra

    def _loop_spent(self, rt: NodeRuntime, e: EdgeDef) -> bool:
        """One more traversal would exceed the loop's bound (a missing bound counts as spent)."""
        bound = self._bound(e)
        return bound < 1 or rt.traversals.get(e.id, 0) + 1 > bound

    def _loop_budgets(self, node_id: str) -> str:
        """Each loop of a node that has been traversed, or has no traversal left: "E38 2/3, E44 3/3 (at its bound)"."""
        rt = self.rt(node_id)
        shown = [e for e in self._loop_edges(node_id) if rt.traversals.get(e.id, 0) or self._loop_spent(rt, e)]
        return ", ".join(f"{e.id} {rt.traversals.get(e.id, 0)}/{self._bound(e)}" + (" (at its bound)" if self._loop_spent(rt, e) else "")
                         for e in shown) or "none traversed"

    def _trend(self, latest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return trend_rows(self.state, self.wf.policies.blocking_severities, latest)

    def _extension_verdict(self, key: str, latest: dict[str, Any] | None = None) -> tuple[bool, str] | None:
        """None when the project's budget never extends; else whether the latest review round converged, and why. Each
        budget (`rework`, or a loop id) is extended at most once per version: re-judging the same version is not new
        progress. The wall-clock guard, when set, refuses every extension once it is spent."""
        if not self.budget.extends:
            return None
        granted_at = self.state.runtime.grant_versions.get(key)
        if granted_at is not None and self.state.runtime.current_version <= granted_at:
            return False, (f"{key} was already extended at version {granted_at}, and no newer version has been reviewed since; "
                           "re-judging a version is not new progress")
        if self.budget.max_hours is not None:
            spent = hours_since(self.state.project.created_at)
            if spent is not None and spent >= self.budget.max_hours:
                return False, (f"the wall-clock guard of {self.budget.max_hours:g} h is spent ({spent:.1f} h since the run "
                               "was created)")
        return convergence(self._trend(latest))

    def _extend_loops(self, node: NodeDef, rt: NodeRuntime, spent: list[EdgeDef]) -> tuple[list[EdgeDef], str | None]:
        """Grant spent rework loops `extend_by` more traversals when the latest review round converged. Returns the loops
        still spent and, when none was extended, why (None when the budget never extends)."""
        verdicts = {e.id: self._extension_verdict(e.id) for e in spent}
        if any(v is None for v in verdicts.values()):
            return spent, None
        refused = next((why for ok, why in verdicts.values() if not ok), None)
        if refused:
            return spent, refused
        grants, why = {}, next(iter(verdicts.values()))[1]
        for e in spent:
            before = self._bound(e)
            self.state.runtime.loop_extra[e.id] = self.state.runtime.loop_extra.get(e.id, 0) + self.budget.extend_by
            self.state.runtime.grant_versions[e.id] = self.state.runtime.current_version
            grants[e.id] = [before, self._bound(e)]
        self.ledger.append("loop_budget_extended", node=node.id, loops=grants, profile=self.budget.profile, reason=why,
                           version=self.state.runtime.current_version)
        return [e for e in spent if self._loop_spent(rt, e)], None

    def _extend_rework(self, latest: dict[str, Any]) -> str | None:
        """The review gate's extension hook: more rework cycles for a converging run, or None (and why, for the gate)."""
        verdict = self._extension_verdict("rework", latest)
        if verdict is None:
            return None
        ok, why = verdict
        if not ok:
            self._refused_rework = why
            return None
        before = self._max_rework_cycles()
        self.state.runtime.rework_extra += self.budget.extend_by
        self.state.runtime.grant_versions["rework"] = self.state.runtime.current_version
        self.ledger.append("rework_budget_extended", before=before, after=self._max_rework_cycles(), profile=self.budget.profile,
                           reason=why, version=self.state.runtime.current_version)
        return f"rework budget extended from {before} to {self._max_rework_cycles()} cycles: the run is converging ({why})"

    def _code_changed_nodes(self) -> list[str]:
        """Command nodes that passed with other code than is there now: a file they count changed or appeared (#99). A
        narrowed code set (files dropped, the rest unchanged) is not a change (#97). A human-gated node is left to a human."""
        out = []
        for nid in self.index.topo:
            if nid not in self.state.runtime.nodes or not self.project.command_for(nid) or self._human_gated(nid):
                continue
            rt = self.rt(nid)
            if rt.status != S.STEP_PASS or not rt.command_code:
                continue
            try:
                now_code = dict(self.project.command_code(nid))
            except Exception:   # unreadable code is reported by status; re-running would fail the same way
                continue
            if any(rt.command_code.get(p) != h and rt.self_written_code.get(p) != h for p, h in now_code.items()):
                out.append(nid)
        return out

    def _fail(self, node: NodeDef, rt: NodeRuntime, failures: list[str], limit: int | None = None, revision: bool = False) -> None:
        """A failed attempt. Technical failures (checks, errors) count against the node's retry budget; an independent
        pre-flight's revisions are quality iterations and count apart, against `preflight.max_revisions` (#106). A revised
        attempt had completed its checks, so it ends a run of technical failures."""
        self.state.history.retries.append(RetryRecord(node=node.id, attempt=rt.attempts, reasons=failures, at=now()))
        rt.failures, rt.finished_at = failures, now()
        if revision:
            rt.preflight_revisions += 1
            rt.fail_streak = 0
            streak, bound = rt.preflight_revisions, limit or node.retry.max_attempts
        else:
            rt.fail_streak += 1
            streak, bound = rt.fail_streak, node.retry.max_attempts
        rt.interruptions, rt.executed = 0, False
        self.ledger.append("node_failed", node=node.id, attempt=rt.attempts, streak=streak, failures=failures,
                           **({"revision": True} if revision else {}))
        if streak >= bound:
            what = (f"the independent pre-flight returned {streak} attempts without a pass (preflight.max_revisions {bound})"
                    if revision else f"{streak} consecutive attempts failed completion")
            self._escalate(node, what, failures)
        else:
            rt.status = S.ITERATE
        self._checkpoint("node_failed", node.id)

    def _escalate(self, node: NodeDef, reason: str, report: list[str], edge: str | None = None, advice: str | None = None,
                  holds: list[str] | None = None, edges: list[str] | None = None) -> None:
        rt = self.rt(node.id)
        rt.status = S.BLOCKED
        esc = Escalation(node=node.id, reason=reason, report="; ".join(map(str, report)), to=node.escalation.to, at=now(), edge=edge,
                         holds=list(holds or []), edges=list(edges or ([edge] if edge else [])))
        self.state.runtime.escalations.append(esc)
        self.state.runtime.status = RunStatus.BLOCKED
        path = self.project.vpg / "escalations" / f"{node.id}-{len(self.state.runtime.escalations)}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        open_defects = [f"{d.id} [{d.severity}] {d.dimension}: {d.description} → {d.traced_node}" for d in self.state.evaluation.defects
                        if d.status in ("open", "persisting")]
        path.write_text(f"# 🔴 Blocked — N{node.num:02d} {node.name}\n\n- **Reason:** {reason}\n- **Escalate to:** {node.escalation.to}\n"
                        f"- **Policy:** {node.escalation.after}\n- **Report:** {node.escalation.report}\n\n## What was tried\n"
                        + ("\n".join(f"- attempt {r.attempt}: {'; '.join(r.reasons)}" for r in self.state.history.retries if r.node == node.id)
                           or "- no failed attempts of this node")
                        + "\n\n## Rework history (before → after)\n"
                        + ("\n".join(f"- cycle {c.rework_cycle}: {c.defect_id} v{c.before_version}→v{c.after_version} "
                                      f"{'resolved' if c.resolved else 'NOT resolved'} — {c.after}" for c in self.state.evaluation.comparisons)
                           or "- none")
                        + "\n\n## Details\n" + "\n".join(f"- {x}" for x in report)
                        + "\n\n## Open defects\n" + ("\n".join(f"- {x}" for x in open_defects) or "- none")
                        + "\n\n## Quality trend (blocking P1–P3 per review round)\n" + self._trend_section()
                        + "\n\n## What unblocks it\n" + (advice or self._unblock_advice(node, edge, esc.edges)) + "\n")
        self.ledger.append("escalated", node=node.id, reason=reason, report=esc.report, file=rel(path, self.project.root),
                           **self._trend_event())

    def _trend_section(self) -> str:
        rows = self._trend()
        if not rows:
            return "- no review round yet"
        ok, why = convergence(rows)
        return ("\n".join(f"- {x}" for x in trend_lines(rows))
                + (f"\n- **Latest round:** {'converging' if ok else 'not converging'}: {why}" if len(rows) >= 2 else ""))

    def _trend_event(self) -> dict[str, Any]:
        """The last two review rounds for an escalation's ledger event (empty before any review round)."""
        rows = self._trend()
        if not rows:
            return {}
        ok, why = convergence(rows)
        keep = ("version", "open", "resolved", "new", "persisting")
        return {"trend": [{k: r[k] for k in keep} for r in rows[-2:]], "converging": ok, "convergence": why}

    def _unblock_advice(self, node: NodeDef, edge: str | None, edges: list[str] | None = None) -> str:
        cmd = f"`vpg unblock {rel(self.project.root, Path.cwd())} {node.id} --actor <name>`"
        if edge and node.gate == "freeze_inputs":
            return (f"- The intake could not close its context gaps within the budget of loop {edge}. Add the missing sources "
                    f"(product evidence, brand authority, prior feedback) under `sources/`, then run {cmd}: unblocking resets the "
                    f"loop's budget and asks the intake again. If a source cannot be supplied, only a named human may waive that gap: "
                    f"{cmd[:-1]} --waive <gap id> --note \"<why the film can proceed>\"`.")
        if edge:
            named = list(edges or [edge])
            rt = self.rt(node.id)
            others = [e.id for e in self._loop_edges(node.id) if e.id not in named and self._loop_spent(rt, e)]
            text = (f"- A human resolves why {'loop ' + named[0] if len(named) == 1 else 'loops ' + ', '.join(named)} kept repeating, "
                    f"then runs {cmd}; unblocking resets the traversal budget of {'that loop' if len(named) == 1 else 'those loops'}.")
            if others:
                text += (f"\n- {', '.join(others)} {'has' if len(others) == 1 else 'have'} no traversal left either, so the next plan that "
                         f"needs {'it' if len(others) == 1 else 'them'} blocks again. `{cmd[1:-1]} --reset-loops` gives every loop of "
                         f"{node.id} a fresh budget in the same decision (the ledger records it).")
            return text
        if node.gate == "review_gate":
            return (f"- A human reviews the report above. Fix the cause, or grant a fresh rework budget, with {cmd[:-1]} --reset-budget`. "
                    "`--reset-budget` restarts the rework-cycle and repeated-defect counters, and the ledger records it.")
        return f"- A human resolves the cause (see Details), then runs {cmd}; unblocking resets the node's retry streak."

    def _escalate_run(self, what: str, report: str) -> None:
        self.state.runtime.status = RunStatus.BLOCKED
        reason = report if len(report) <= 240 else report[:237] + "…"
        self.state.runtime.escalations.append(Escalation(node=what, reason=reason, report=report, to="Wei", at=now()))
        self.ledger.append("escalated", node=what, reason=what, report=report, **self._trend_event())

    def invalidate(self, targets: list[str], reason: str, defects_by_target: dict[str, list[str]] | None = None,
                   recheck_depth: int = 0) -> list[str]:
        closure = set()
        for t in targets:
            closure |= {t} | self.index.descendants(t)
        changed = []
        for nid in self.index.topo:
            if nid not in closure:
                continue
            rt = self.rt(nid)
            if rt.status == S.PLANNED:
                continue
            if rt.status == S.BLOCKED:   # its escalation stays open: record what lifted the block, for the unblock's ledger
                for esc in self.state.runtime.escalations:
                    if esc.node == nid and not esc.resolved:
                        esc.reopened = f"while it was blocked it was sent to re-check ({reason})"
            if rt.preflight:   # its files stay registered, unjudged: the next dispatch re-runs instead of memo-passing on them
                self.ledger.append("preflight_dropped", node=nid, attempt=rt.preflight.get("attempt"), reason=reason)
                rt.force_rerun = True
            rt.status, rt.dispatch_token, rt.preflight = S.STALE, None, None
            rt.rechecks = recheck_depth   # 0 unless a control-document re-check sent it back (see _recheck_readers)
            if nid in targets:
                rt.rework_defects = sorted(set(rt.rework_defects) | set((defects_by_target or {}).get(nid, [])))
            changed.append(nid)
        freezer = next((n.id for n in self.wf.nodes if n.gate == "freeze_inputs"), None)
        if freezer in closure and self.state.inputs.frozen:
            self.state.inputs.frozen = False
            self.ledger.append("inputs_unfrozen", reason=reason)
        preserved = [nid for nid in self.index.topo if self.rt(nid).status == S.STEP_PASS]
        if self.state.runtime.status == RunStatus.RUN_PASS:
            self.state.runtime.status = RunStatus.RUNNING
        self.ledger.append("invalidated", targets=targets, closure=changed, preserved=preserved, reason=reason)
        return changed

    def _require_human(self, actor: str, what: str) -> None:
        if not is_human_name(actor, self.wf.policies.dry_run_actor):
            raise EngineError(f"{what} needs a named human actor (not an agent, not {self.wf.policies.dry_run_actor})")

    # ------------------------------------------------------------------ delegation grants (v1.7, #18)
    def _authorize(self, actor: str, action: str, what: str) -> Grant | None:
        """A named human acts in their own name. An agent may act only under a live grant for `action` from a named human;
        every event the action records then carries the grant and its grantor (Ledger.stamp), so the record shows who acted
        and on whose authority, never the human's name for the agent's act."""
        if is_human_name(actor, self.wf.policies.dry_run_actor):
            return None
        grant = self._live_grant(actor, action)
        if grant is None:
            held = [g.id for g in self.state.history.grants if self._actor_key(g.grantee) == self._actor_key(actor)]
            raise EngineError(f"{what} needs a named human actor (not an agent, not {self.wf.policies.dry_run_actor}), or an agent "
                              f"with a live grant for '{action}' (`vpg grant <project> --actor <human> --to agent:<id> --allow "
                              f"{action} --expires <date> --reason …`)" + (f"; {actor}'s grants {', '.join(held)} don't cover it "
                                                                         "or have ended" if held else ""))
        self.ledger.stamp = {"grant": grant.id, "grantor": grant.grantor}
        return grant

    def _live_grant(self, actor: str, action: str) -> Grant | None:
        key, today = self._actor_key(actor), now()[:10]
        if not key:
            return None
        for g in reversed(self.state.history.grants):
            if not g.revoked_at and g.expires >= today and self._actor_key(g.grantee) == key and action in g.actions:
                return g
        return None

    def _human_dims(self, node: NodeDef) -> list[str]:
        return [d["id"] for d in _dims(node, self.rubrics) if d["check"]["type"] == "human"]

    def _human_only_decision(self, node: NodeDef) -> str:
        """Why only a human may decide at this gate, or '' when a grant may cover it: its rubric has human-judged
        dimensions, or it decides on the reviewed film (the release, where listening and taste are judged)."""
        covers = self._human_dims(node)
        if covers:
            return f"a decision on {node.id} covers {', '.join(covers)}, which only a human judges"
        if node.id in self.index.descendants(self._gate_node()):
            return f"{node.id} decides on the reviewed film (the release), which only a human decides"
        return ""

    def _grantable(self, action: str) -> str:
        """Why `action` can't be delegated, or '' when it can."""
        if action in GRANTABLE:
            return ""
        if action.startswith("approve:"):
            nid = action.split(":", 1)[1]
            if nid not in self.index.ids:
                return f"{nid} is not a node of this workflow"
            node = self.node(nid)
            if not node.human_review.required:
                return f"{nid} has no human gate"
            return self._human_only_decision(node)
        return f"'{action}' is not a delegable action (delegable: {', '.join(GRANTABLE)}, approve:<node>)"

    @_human_action("grant")
    def grant(self, grantee: str, actions: list[str], expires: str, reason: str, actor: str) -> Grant:
        """`vpg grant`: a named human delegates named human-only actions to one agent until a date. Never delegable itself."""
        self._require_human(actor, "granting a delegation")
        g_raw, key = (grantee or "").strip(), self._actor_key(grantee)
        if not g_raw.lower().startswith("agent:") or not key or key == "cli" or key.startswith("dry-run"):
            raise EngineError("a grant names one agent: --to agent:<id>")
        acts = list(dict.fromkeys(a.strip() for a in actions or [] if a and a.strip()))
        if not acts:
            raise EngineError("a grant lists the actions it allows (--allow ACTION, repeatable)")
        bad = [f"{a}: {why}" for a in acts if (why := self._grantable(a))]
        if bad:
            raise EngineError("cannot delegate " + "; ".join(bad))
        try:
            end = datetime.strptime((expires or "").strip(), "%Y-%m-%d").date()
        except ValueError:
            raise EngineError("a grant needs an end date (--expires YYYY-MM-DD)") from None
        today = datetime.strptime(now()[:10], "%Y-%m-%d").date()
        if not today <= end <= today + timedelta(days=GRANT_MAX_DAYS):
            raise EngineError(f"a grant ends between today and {GRANT_MAX_DAYS} days from now (got {end.isoformat()})")
        if not (reason or "").strip():
            raise EngineError("a grant needs the human's reason (--reason)")
        g = Grant(id=f"G-{self._next_grant_number():03d}", grantor=actor.strip(), grantee=f"agent:{key}", actions=acts,
                  reason=reason.strip(), at=now(), expires=end.isoformat())
        self.state.history.grants.append(g)
        self.ledger.append("grant_issued", id=g.id, grantor=g.grantor, grantee=g.grantee, actions=acts, expires=g.expires, reason=g.reason)
        self._save("grant")
        return g

    @_human_action("grant")
    def revoke_grant(self, grant_id: str, actor: str, reason: str) -> Grant:
        self._require_human(actor, "revoking a grant")
        g = next((x for x in self.state.history.grants if x.id == grant_id), None)
        if g is None:
            raise EngineError(f"no grant {grant_id}; grants: {[x.id for x in self.state.history.grants] or 'none'}")
        if g.revoked_at:
            raise EngineError(f"{g.id} was already revoked by {g.revoked_by} ({g.revoked_at[:10]})")
        if not (reason or "").strip():
            raise EngineError("revoking a grant needs the human's reason (--reason)")
        g.revoked_at, g.revoked_by, g.revoke_reason = now(), actor.strip(), reason.strip()
        self.ledger.append("grant_revoked", id=g.id, by=g.revoked_by, reason=g.revoke_reason)
        self._save("grant")
        return g

    def _next_grant_number(self) -> int:
        """Grant ids never repeat, even after a rollback: the highest id in the state or the ledger, plus one."""
        ids = [g.id for g in self.state.history.grants] + [e["data"].get("id", "") for e in self.ledger.events("grant_issued")]
        nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"G-(\d+)", str(i)))]
        return max(nums, default=0) + 1

    def _live_grants(self) -> list[Grant]:
        today = now()[:10]
        return [g for g in self.state.history.grants if not g.revoked_at and g.expires >= today]

    @_human_action("invalidate")
    def invalidate_nodes(self, targets: list[str], reason: str, actor: str = "") -> list[str]:
        """`vpg invalidate`: force nodes and their dependents to re-check. Budgets and loop counters are untouched.

        #13: the request names its actor. Re-opening a human's decision needs a named human, or an agent granted
        'invalidate': when the closure holds a human gate that passed or waits for its human, or the inputs' freeze node
        while the inputs are frozen. An agent may re-run other work (e.g. a producer whose code changed)."""
        actor = (actor or "").strip()
        key = self._actor_key(actor)
        if not key or key == "cli" or key.startswith("dry-run"):
            raise EngineError("invalidating needs the requester's identity: --actor <name> or --actor agent:<id>")
        closure = set()
        for t in targets:
            closure |= {t} | self.index.descendants(t)
        freezer = next((n.id for n in self.wf.nodes if n.gate == "freeze_inputs"), None)
        gated = sorted(n for n in closure if self.node(n).human_review.required
                       and self.rt(n).status in (S.STEP_PASS, S.WAITING_HUMAN))
        if freezer in closure and self.state.inputs.frozen:
            gated.append(f"{freezer} (the frozen inputs)")
        if gated:
            self._authorize(actor, "invalidate", f"invalidating {', '.join(targets)} re-opens a human's decision "
                                                 f"({', '.join(gated)}), which")
        changed = self.invalidate(targets, reason=f"{reason} (requested by {actor})")
        for t in targets:
            self.rt(t).force_rerun = True   # an explicit request re-runs the target even when its inputs are unchanged
        self._update_status()
        self._save("invalidate")
        return changed

    # ------------------------------------------------------------------ human and agent interactions
    def submit(self, node_id: str, outputs: dict[str, Any], token: str, actor: str, review: dict[str, Any] | None = None,
               outcome: str = "pass") -> dict[str, Any]:
        node, rt = self.node(node_id), self.rt(node_id)
        if rt.status != S.WAITING_RUNTIME:
            raise EngineError(f"{node_id} is {rt.status.value}; no task is open (duplicate or late submission rejected)")
        if rt.preflight:
            raise EngineError(f"attempt {rt.preflight['attempt']} of {node_id} is finished and waits for its independent pre-flight "
                              f"verdict ({rt.task_packet}); record it with `vpg preflight`")
        if token != rt.dispatch_token:
            raise StaleSubmission(f"stale submission for {node_id}: token {token} ≠ current {rt.dispatch_token}")
        ctx = self._context(node, rt.attempts, rt.fingerprint or "", token)
        blocked_by = self._integrity_problems()
        if blocked_by:
            raise EngineError("the run has an integrity problem, so no submission is accepted: " + "; ".join(blocked_by))
        pre = rt.result_data.get("pre_outputs", {})
        self._check_submission(node, ctx, outputs, review, pre, actor)
        executor = self._agent_identity(actor)
        res = NodeResult(outcome=outcome, outputs={**pre, **outputs}, review=review, executor=executor)
        bound = f"agent:{node.agent}" if node.agent else None
        extra = {"bound_agent": node.agent} if bound and executor != bound else {}
        self.ledger.append("node_submitted", node=node_id, attempt=rt.attempts, actor=actor, outputs=sorted(outputs), **extra)
        cmd = self.project.command_for(node_id)
        rt.status, rt.executed = S.RUNNING, False
        if cmd:
            # the code this command starts with: an agent's edits since dispatch aren't the command's own writes (#105).
            # command_code stays the dispatch snapshot the fingerprint was computed from, so status still reports them.
            rt.command_code_at_run = dict(self.project.command_code(node_id))
            self._save("submit_command")   # a command that takes the process down is charged on resume, like any execution
            try:
                res = run_command(ctx, cmd, res, int(self.project.config.get("command_timeout_s", 900)))
            except Exception as exc:
                res = NodeResult(outcome="__error__", data={"error": str(exc)})
            self._executed(node_id)
        else:
            rt.executed = True   # nothing runs at submit: the agent's work had already finished
        self._handle(ctx, res)
        self._update_status()
        self._save("submit")
        self._drain_publish()
        return self.status()

    def _check_submission(self, node: NodeDef, ctx: NodeContext, outputs: dict[str, Any], review: dict[str, Any] | None,
                          pre: dict[str, Any], actor: str = "") -> None:
        """Argument errors are refused before an attempt is used: unknown keys, missing files, paths outside the
        project, missing required outputs, a review node submitted without its review, or (v1.7) a review of a build that
        isn't this version's, or one its project requires to be independent that isn't."""
        known = [o.key for o in node.outputs]
        kinds = {o.key: o.kind for o in node.outputs}
        problems = []
        unknown = sorted(set(outputs) - set(known))
        if unknown:
            problems.append(f"unknown output key(s) {unknown}; this node's outputs are {known}")
        tool_made = sorted(set(outputs) & set(pre))
        if tool_made:   # the deterministic check's evidence is the tool's file, never a copy the agent supplies
            problems.append(f"output(s) {tool_made} are written by this node's tool check; a submission cannot replace them")
        for key, value in outputs.items():
            values = list(value.values()) if isinstance(value, dict) else (value if isinstance(value, list) else [value])
            for v in values:
                path = ctx.output_path(key, v).resolve()
                if not path.is_relative_to(self.project.root):
                    problems.append(f"'{key}' points outside the project: {path}")
                elif not path.exists():
                    problems.append(f"'{key}' file not found: {path} (relative paths are resolved against {rel(ctx.workdir, self.project.root)})")
                elif not path.is_file():
                    problems.append(f"'{key}' is not a regular file (a directory, pipe or device): {path}")
                elif kinds.get(key) == "json" and path.is_file():
                    try:
                        json.loads(read_text(path))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        problems.append(f"'{key}' is not valid JSON ({path.name}: {exc})")
        if not self.project.command_for(node.id):
            missing = [o.key for o in node.outputs if o.required and o.key not in outputs and o.key not in pre
                       and not (o.per_item and self._items_complete(node, o.key))]
            if missing:
                problems.append(f"missing required output(s) {missing}")
        if node.kind == NodeKind.REVIEW and review is None and any(d["check"]["type"] == "review" for d in _dims(node, self.rubrics)):
            problems.append("this review node needs --review <review.json> scoring its review dimensions")
        if node.kind == NodeKind.REVIEW and isinstance(review, dict):
            problems += self._review_bind_problems(node, review.get("reviewed"), self._agent_identity(actor))
        if problems:
            raise EngineError(f"submission for {node.id} refused, no attempt used: " + "; ".join(problems))

    @_human_action("decision")
    def decide(self, node_id: str, decision: str, actor: str, reason: str = "", feedback: list[str] | None = None) -> dict[str, Any]:
        node, rt = self.node(node_id), self.rt(node_id)
        if rt.status != S.WAITING_HUMAN:
            raise EngineError(f"{node_id} is not waiting for a human decision ({rt.status.value})")
        if decision not in node.human_review.outcomes:
            raise EngineError(f"decision must be one of {node.human_review.outcomes}")
        only = self._human_only_decision(node)
        if only:   # listening, taste, the release: never delegated
            if not is_human_name(actor, self.wf.policies.dry_run_actor):
                raise EngineError(f"{only}: it needs a named human actor, never an agent, and no grant covers it")
            grant = None
        else:
            grant = self._authorize(actor, f"approve:{node_id}", "a human decision")
        locked = self._lock_on_approval(node, decision, actor)   # may refuse; nothing is ledgered before it succeeds
        self._record_approval(node, decision, actor, human=grant is None, reason=reason, locked=locked,
                              grant=grant.id if grant else "")
        for text in feedback or []:
            self.add_feedback(text, actor, reopen=False)
        self._pass(node, rt, decision, rt.result_data)
        self._update_status()
        self._save("decision")
        return self.status()

    def _record_approval(self, node: NodeDef, decision: str, actor: str, human: bool, reason: str,
                         locked: list[tuple[str, Any]] = (), grant: str = "") -> None:
        rt = self.rt(node.id)
        outputs = {k: sha[:16] for k, (_, sha) in sorted(self._attempt_outputs(node.id, rt.attempts).items())}
        self.state.history.approvals.append(Approval(node=node.id, decision=decision, actor=actor, human=human, at=now(), reason=reason,
                                                     outputs=outputs, grant=grant))
        self.state.history.decisions.append(Decision(id=f"DEC-{len(self.state.history.decisions) + 1:03d}", node=node.id,
                                                     text=f"{node.human_review.decision} → {decision}" + (f" ({reason})" if reason else ""),
                                                     by=actor, at=now()))
        covers = [d["id"] for d in _dims(node, self.rubrics) if d["check"]["type"] == "human"]
        self.ledger.append("decision", node=node.id, decision=decision, actor=actor, human=human, reason=reason, covers=covers,
                           outputs=self.state.history.approvals[-1].outputs)
        for doc, st in locked:
            self.ledger.append("control_locked", doc=doc, version=st.version, sha256=(st.approved_sha256 or "")[:16], by=actor)

    def _lock_on_approval(self, node: NodeDef, decision: str, actor: str) -> list[tuple[str, Any]]:
        """Lock the documents an approval locks. It is all or nothing, and it runs before the decision is recorded, so a
        refused lock leaves no phantom approval in the state or the ledger."""
        docs = list(node.controls.lock_on_approval) if decision == node.human_review.outcomes[0] else []
        bad = [d for d in docs if not self.controls.path(d).is_file()]
        if bad:
            here = rel(self.project.root, Path.cwd())
            raise EngineError(f"cannot lock {', '.join(f'controls/{d}.md' for d in bad)}: missing or not a regular file. Recreate "
                              f"the accepted version with " + "; ".join(f"`vpg restore-control {here} --doc {d} --actor <name>`" for d in bad)
                              + ", then record the decision again")
        drifted = [f"controls/{d}.md" for d in docs if sha256_file(self.controls.path(d)) != self.state.controls[d].sha256]
        if drifted:   # the human reviewed the accepted version; an edit since then must not be locked in by the approval
            here = rel(self.project.root, Path.cwd())
            cmds = "; ".join(f"`vpg restore-control {here} --doc {d} --actor <name>`" for d in docs if f"controls/{d}.md" in drifted)
            raise EngineError(f"cannot lock {', '.join(drifted)}: it changed since the workflow accepted it (an edit outside the "
                              f"workflow). Restore the accepted version with {cmds} (the edit is kept aside), then decide again. "
                              "To make the change, reject with feedback after restoring, so the writer makes it")
        before = {d: self.state.controls[d].model_copy(deep=True) for d in docs}
        try:
            return [(doc, self.controls.approve(doc, actor)) for doc in docs]
        except Exception:
            self.state.controls.update(before)   # all or nothing
            raise

    @_human_action("feedback")
    def add_feedback(self, text: str, actor: str, at: str = "", severity: str = "", reopen: bool = True) -> Feedback:
        """A named human's feedback re-opens a passed gate: N21 interprets it on the next run. An agent's note (#14) never
        re-opens anything by itself: it waits for a named human, or an agent granted 'feedback-triage', to accept or dismiss
        it, and until then no version can pass the gate and N23 refuses the release, so a real problem it names is never
        lost and never acted on without a human."""
        if self._actor_key(actor) in ("", "cli"):
            raise EngineError("feedback names its author: --actor <name> or --actor agent:<id>")
        human = is_human_name(actor, self.wf.policies.dry_run_actor)
        fb = Feedback(id=f"F{len(self.state.evaluation.feedback) + 1:03d}", text=text, actor=actor, at=at, severity=severity,
                      version=self.state.runtime.current_version, created_at=now())
        if not human:
            fb.status, fb.triage = "triage", "pending"
            fb.hold = f"{actor}'s note awaits a human's triage (`vpg feedback --triage {fb.id} --accept|--dismiss`)"
        self.state.evaluation.feedback.append(fb)
        self.ledger.append("feedback", id=fb.id, actor=actor, at=at, version=fb.version, **({"triage": "pending"} if not human else {}))
        if human and reopen:
            self._reopen_gate_for(fb.id, f"new human feedback {fb.id}")
        self._save("feedback")
        return fb

    def _reopen_gate_for(self, fb_id: str, reason: str) -> None:
        gate_node = self._gate_node()
        if self.rt(gate_node).status in (S.STEP_PASS, S.WAITING_HUMAN, S.BLOCKED) or self.state.runtime.status == RunStatus.RUN_PASS:
            self.invalidate([gate_node], reason=reason)

    @_human_action("feedback_triaged")
    def triage_feedback(self, fb_id: str, accept: bool, actor: str, reason: str) -> Feedback:
        """`vpg feedback --triage F --accept|--dismiss`: a named human (or an agent granted 'feedback-triage', never the note's
        own author) rules on an agent's note. Accepted, it becomes feedback like the human's own and re-opens a passed gate;
        dismissed, it is closed with the reason. Either way its hold on the gate is released."""
        fb = next((f for f in self.state.evaluation.feedback if f.id == fb_id), None)
        if fb is None:
            raise EngineError(f"no feedback {fb_id}")
        if fb.triage != "pending":
            raise EngineError(f"{fb.id} is not awaiting triage" + (f" (it was {fb.triage})" if fb.triage else " (it is a human's feedback)"))
        if not (reason or "").strip():
            raise EngineError("triage needs the reason (--reason)")
        if self._actor_key(actor) == self._actor_key(fb.actor):
            raise EngineError(f"{fb.id} is {fb.actor}'s own note; another party triages it")
        self._authorize(actor, "feedback-triage", "triaging an agent's note")
        fb.hold, fb.triaged_by = "", actor.strip()
        if accept:
            fb.triage, fb.status = "accepted", "open"
        else:
            fb.triage, fb.status, fb.no_action_reason = "dismissed", "dismissed", reason.strip()
        self.ledger.append("feedback_triaged", id=fb.id, outcome=fb.triage, by=actor, author=fb.actor, reason=reason.strip())
        if accept:
            self._reopen_gate_for(fb.id, f"{actor} accepted {fb.actor}'s note {fb.id}")
        self._update_status()
        self._save("feedback_triaged")
        return fb

    @_human_action("change_request")
    def change_request(self, doc: str, reason: str, by: str) -> ChangeRequest:
        if doc not in self.state.controls:
            raise EngineError(f"unknown control document {doc!r}; documents: {sorted(self.state.controls)}")
        cr = ChangeRequest(id=f"CR-{len(self.state.history.change_requests) + 1:03d}", doc=doc, reason=reason, requested_by=by, at=now())
        self.state.history.change_requests.append(cr)
        self.ledger.append("change_request", id=cr.id, doc=doc, by=by, reason=reason)
        self._save("change_request")
        return cr

    @_human_action("change_request")
    def approve_change_request(self, cr_id: str, by: str, writer_node: str | None = None, reason: str = "") -> ChangeRequest:
        cr = self._open_change_request(cr_id, "approved")
        self._authorize(by, "change-request", "approving a change request")
        self._not_own_request(cr, by)
        cr.status, cr.approved_by = "approved", by
        self.ledger.append("change_request_approved", id=cr.id, doc=cr.doc, by=by, reason=(reason or "").strip())
        writer = writer_node or next((n.id for n in self.wf.nodes if cr.doc in n.controls.write), None)
        if writer:
            self.invalidate([writer], reason=f"approved change request {cr.id} on {cr.doc}.md")
        self._save("change_request")
        return cr

    @_human_action("change_request")
    def reject_change_request(self, cr_id: str, by: str, reason: str) -> ChangeRequest:
        """A named human declines an open change request, with the reason. Nothing is invalidated: the document stays as
        approved, and the request stays in the history and the release summary as rejected."""
        cr = self._open_change_request(cr_id, "rejected")
        self._authorize(by, "change-request", "rejecting a change request")
        self._not_own_request(cr, by)
        if not (reason or "").strip():
            raise EngineError("rejecting a change request needs the human's reason (--reason)")
        cr.status = "rejected"
        self.ledger.append("change_request_rejected", id=cr.id, doc=cr.doc, by=by, reason=reason.strip())
        self._save("change_request")
        return cr

    @_human_action("change_request")
    def defer_change_request(self, cr_id: str, by: str, until: str, reason: str) -> ChangeRequest:
        """#102: park an open change request with the trigger that revisits it (e.g. "after the v1 release"). Nothing is
        invalidated; the document stays as approved. It can still be approved or rejected, and the release lists it."""
        cr = self._open_change_request(cr_id, "deferred")
        self._authorize(by, "change-request", "deferring a change request")
        self._not_own_request(cr, by)
        if not (until or "").strip():
            raise EngineError("deferring a change request needs its revisit trigger (--until \"<when or what brings it back>\")")
        if not (reason or "").strip():
            raise EngineError("deferring a change request needs the reason (--reason)")
        cr.status, cr.revisit = "deferred", until.strip()
        self.ledger.append("change_request_deferred", id=cr.id, doc=cr.doc, by=by, revisit=cr.revisit, reason=reason.strip())
        self._save("change_request")
        return cr

    def _not_own_request(self, cr: ChangeRequest, by: str) -> None:
        if not is_human_name(by, self.wf.policies.dry_run_actor) and self._actor_key(by) == self._actor_key(cr.requested_by):
            raise EngineError(f"{cr.id} was filed by {cr.requested_by}; an agent never decides its own change request")

    def change_request_impact(self, cr_id: str = "", doc: str = "") -> dict[str, Any]:
        """#80, read-only: what approving a change request on `doc` sets in motion. Its writer re-runs; every node that
        reads the document re-checks when it changes; and everything below them re-checks too."""
        if cr_id:
            cr = next((c for c in self.state.history.change_requests if c.id == cr_id), None)
            if cr is None:
                raise EngineError(f"no change request {cr_id}")
            doc = cr.doc
        if doc not in self.state.controls:
            raise EngineError(f"unknown control document {doc!r}; documents: {sorted(self.state.controls)}")
        writer = next((n.id for n in self.wf.nodes if doc in n.controls.write), None)
        readers = sorted(n.id for n in self.wf.nodes if n.id != writer
                         and (doc in control_reads(n) or f"control:{doc}" in n.inputs))
        reach: set[str] = set()
        for n in ([writer] if writer else []) + readers:
            reach |= {n} | self.index.descendants(n)
        order = [n for n in self.index.topo if n in reach]
        gates = [n for n in order if self.node(n).human_review.required and self.rt(n).status in (S.STEP_PASS, S.WAITING_HUMAN)]
        return {"cr": cr_id, "doc": doc, "writer": writer, "readers": readers, "recheck": order,
                "human_gates": gates, "commands": [n for n in order if self.project.command_for(n)]}

    def _open_change_request(self, cr_id: str, verb: str) -> ChangeRequest:
        cr = next((c for c in self.state.history.change_requests if c.id == cr_id), None)
        if cr is None:
            raise EngineError(f"no change request {cr_id}; open ones: "
                              f"{[c.id for c in self.state.history.change_requests if c.status in ('open', 'deferred')] or 'none'}")
        if cr.status not in ("open", "deferred"):
            raise EngineError(f"{cr.id} is {cr.status}; only an open or deferred change request can be {verb}")
        return cr

    @_human_action("defect_resolved")
    def resolve_defect(self, defect_id: str, actor: str, evidence: str) -> Defect:
        """`vpg resolve`: a named human closes an unresolved defect with the evidence that it is fixed or does not apply,
        when no review can show it (e.g. the reviewer no longer runs). The gate counts it as resolved from its next
        evaluation; its failure history is kept."""
        grant = self._authorize(actor, "resolve", "resolving a defect")
        d = next((x for x in self.state.evaluation.defects if x.id == defect_id), None)
        if d is None:
            raise EngineError(f"no defect {defect_id}")
        if grant is not None:
            why = self._needs_a_human(d)
            if why:
                raise EngineError(f"{d.id} {why}, so only a named human resolves it; a grant can't")
        if d.status not in ("open", "persisting", "in_rework"):
            raise EngineError(f"{d.id} is {d.status}; only an open, persisting or in-rework defect can be resolved")
        if not (evidence or "").strip():
            raise EngineError("resolving a defect needs the human's evidence (--evidence): what shows it is fixed or does not apply")
        was = d.status
        d.status, d.needs_human, d.human_hold = "resolved", False, ""
        version = self.state.runtime.current_version
        self.state.evaluation.comparisons.append(Comparison(
            rework_cycle=self.state.runtime.rework_cycles, defect_id=d.id, before_version=d.last_seen_version,
            after_version=version, resolved=True, before=d.description, after=f"resolved by {actor}: {evidence.strip()}"))
        self.ledger.append("defect_resolved", id=d.id, actor=actor, evidence=evidence.strip(), was=was, version=version)
        self._save("defect_resolved")
        return d

    @_human_action("unblock")
    def unblock(self, node_id: str, actor: str, note: str = "", reset_budget: bool = False, waive: list[str] | None = None,
                all_loops: bool = False) -> dict[str, Any]:
        """A named human resolves the open escalations of one node (or one run-level name). Nothing else changes state:
        a node without an open escalation is not reset (that would re-run it without re-checking its dependents), and
        the run stays BLOCKED while any other escalation is open. `waive` records intake-gap waivers before the freeze.
        `all_loops` also gives every loop of the node a fresh traversal budget, not only the loops that escalated."""
        self._authorize(actor, "unblock", "unblocking")
        if waive:   # a waived intake gap stands in for evidence the film never had: only a human waives it
            self._require_human(actor, "waiving an intake gap")
        is_node = node_id in self.state.runtime.nodes
        open_here = [e for e in self.state.runtime.escalations if e.node == node_id and not e.resolved]
        live = ({d.id for d in self.state.evaluation.defects if d.human_hold and d.status in ("open", "persisting", "in_rework")}
                | {f.id for f in self.state.evaluation.feedback if f.hold})
        held = sorted({h for e in open_here for h in e.holds} & live)   # a hold a human has since cleared no longer counts
        if held and not is_human_name(actor, self.wf.policies.dry_run_actor):
            raise EngineError(f"{node_id}'s escalation holds {', '.join(held)} for a named human; a grant can't release them "
                              "(a named human unblocks it, or triages the notes and resolves the defects first)")
        blocked = is_node and self.rt(node_id).status == S.BLOCKED
        if not (open_here or blocked or waive):
            state = f" (it is {self.rt(node_id).status.value})" if is_node else ""
            raise EngineError(f"{node_id} has no open escalation{state}, so there is nothing to unblock; open escalations: "
                              f"{sorted({e.node for e in self.state.runtime.escalations if not e.resolved}) or 'none'}")
        if all_loops and not any(e.edge for e in open_here):
            raise EngineError(f"--reset-loops needs an open loop-bound escalation on {node_id}; "
                              "a plain unblock resolves any other escalation")
        if waive:
            self._waive_gaps(node_id, waive, actor, note)
        reset_loops, released = [], []
        legacy = self._legacy_blocked_plan(node_id, open_here) if is_node and open_here else None
        if legacy is not None:
            self._revert_legacy_plan(node_id, legacy)
        if is_node and node_id == self._gate_node() and open_here:
            shown = {h for e in open_here for h in e.holds}   # only holds the gate reported to this human are released

            def cited(item_id: str) -> bool:
                return item_id in shown
            for d in self.state.evaluation.defects:   # the human saw them in the gate's report and lets rework route them
                if d.human_hold and d.status in ("open", "persisting", "in_rework") and cited(d.id):
                    d.human_hold = ""
                    released.append(d.id)
            owner = self.node(node_id).escalation.to.strip().lower()
            human = is_human_name(actor, self.wf.policies.dry_run_actor)
            for f in self.state.evaluation.feedback:   # only the feedback's author, or the gate's owner, accepts a dismissal
                author = f.triaged_by if f.triage == "accepted" else f.actor   # an accepted note is its triager's (#14)
                if (f.hold and f.triage != "pending" and cited(f.id)   # a note awaiting triage is released only by it
                        and (human or not self._humans_feedback(f))       # a grant never releases a human's feedback
                        and actor.strip().lower() in (author.strip().lower(), owner)):
                    f.hold = ""
                    released.append(f.id)
        for esc in open_here:
            esc.resolved = True
            for edge_id in dict.fromkeys(([esc.edge] if esc.edge else []) + esc.edges) if is_node else []:
                self.rt(node_id).traversals[edge_id] = 0   # the human grants each loop that ran out a fresh budget …
                if edge_id not in reset_loops:
                    reset_loops.append(edge_id)
                loop = next((e for e in self.wf.edges if e.id == edge_id), None)
                if loop is not None and loop.target in self.state.runtime.nodes:
                    self.rt(loop.target).force_rerun = True   # … and its target is asked again on the next traversal
        if all_loops:
            for e in self._loop_edges(node_id):
                if self.rt(node_id).traversals.get(e.id) and e.id not in reset_loops:
                    self.rt(node_id).traversals[e.id] = 0
                    reset_loops.append(e.id)
        for other in self.state.runtime.nodes.values():
            other.rechecks = 0   # a human restarts every control-document re-check chain
        if blocked:
            rt = self.rt(node_id)
            rt.status, rt.fail_streak, rt.failures, rt.interruptions, rt.preflight_revisions = S.READY, 0, [], 0, 0
            below = [d for d in self.index.topo if d in self.index.descendants(node_id) and self.rt(d).status == S.STEP_PASS]
            if below:   # it re-runs, so nothing built on its earlier output may stay passed
                self.invalidate(below, reason=f"{node_id} was unblocked and re-runs; its dependents re-check")
        elif is_node and open_here:
            # The escalation is open but the node is not BLOCKED: a rollback restored a checkpoint at which it was not, or an
            # invalidation (an accepted configuration change, an upstream edit) sent it to re-check while it was blocked. It
            # is never reset to READY; it and its dependents re-check (re-run, or memo-skip when unchanged).
            cause = next((e.reopened for e in open_here if e.reopened), "it was no longer blocked")
            self.invalidate([node_id], reason=f"{node_id} was unblocked; {cause}; it and its dependents re-check")
        granted = {e: self.state.runtime.loop_extra.pop(e) for e in reset_loops if e in self.state.runtime.loop_extra}
        for e in reset_loops:
            self.state.runtime.grant_versions.pop(e, None)
        if reset_budget:
            self.state.runtime.rework_cycles = 0
            self.state.runtime.defect_failures = {}
            if self.state.runtime.rework_extra:
                granted["rework_cycles"] = self.state.runtime.rework_extra
            self.state.runtime.rework_extra = 0
            self.state.runtime.grant_versions.pop("rework", None)
        still_open = sorted({e.node for e in self.state.runtime.escalations if not e.resolved})
        if still_open:
            self.state.runtime.status = RunStatus.BLOCKED    # each escalation needs its own human decision
        elif self.state.runtime.status == RunStatus.BLOCKED:
            self.state.runtime.status = RunStatus.RUNNING
        self.ledger.append("unblocked", node=node_id, actor=actor, note=note, reset_loops=reset_loops, reset_budget=reset_budget,
                           released_holds=released,
                           waived=list(waive or []), still_open=still_open,
                           **({"cleared_grants": granted} if granted else {}))   # a fresh budget is the base again
        self._update_status()
        self._save("unblock")
        return self.status()

    def _legacy_blocked_plan(self, node_id: str, open_here: list[Escalation]) -> dict[str, Any] | None:
        """The plan an engine before v1.5.1 applied on a pass that a loop bound then blocked, or None. That engine started
        the rework (a rework cycle, the plan's defects in rework, a traversal on every loop the plan named) before it
        checked the bounds, and wrote loop escalations without `edges`. Only the exact ledger sequence it wrote proves one:
        `regeneration_plan`, `node_passed`, then one `escalated` per exceeded loop of this node, with no rollback since."""
        legacy = [e for e in open_here if e.edge and not e.edges]
        if not legacy or len(legacy) != len(open_here) or self.node(node_id).gate != "regeneration_plan":
            return None
        evs = list(self.ledger.events())
        last = next((k for k in range(len(evs) - 1, -1, -1)
                     if evs[k]["type"] == "escalated" and evs[k]["data"].get("node") == node_id), None)
        if last is None or any(e["type"] == "rollback" for e in evs[last + 1:]):
            return None
        first = last
        while first > 0 and evs[first - 1]["type"] == "escalated" and evs[first - 1]["data"].get("node") == node_id:
            first -= 1
        if first < 2 or {evs[k]["data"].get("reason") for k in range(first, last + 1)} != {e.reason for e in legacy}:
            return None
        passed, plan = evs[first - 1], evs[first - 2]
        if passed["type"] != "node_passed" or passed["data"].get("node") != node_id or plan["type"] != "regeneration_plan":
            return None
        checkpoint = next((e["data"].get("seq") for e in reversed(evs[:first - 2]) if e["type"] == "checkpoint"), None)
        return {"plan": plan["data"], "checkpoint": checkpoint, "spent": [e.edge for e in legacy]}

    def _legacy_revert(self, node_id: str, legacy: dict[str, Any]) -> dict[str, Any]:
        """What undoing that plan changes, since none of it ran, computed without changing anything (`vpg status` shows
        it before the unblock): its rework cycle (while it is still the latest), each of its defects still in rework back
        to its status in the checkpoint before the plan, and the traversal it charged each loop that was not spent (the
        unblock resets a spent loop itself)."""
        plan, rt, st = legacy["plan"], self.rt(node_id), self.state.runtime
        cycle = plan.get("cycle") if isinstance(plan.get("cycle"), int) and plan.get("cycle") == st.rework_cycles else None
        before: dict[str, str] = {}
        ref = next((c for c in self.state.history.checkpoints if c.seq == legacy["checkpoint"]), None)
        if ref is not None:
            try:
                before = {d.id: d.status for d in ProjectState.model_validate(read_json(self.project.root / ref.path)).evaluation.defects}
            except (OSError, ValueError):
                before = {}
        defects = {d.id: (before[d.id] if before.get(d.id) in ("open", "persisting") else ("persisting" if d.rework_count else "open"))
                   for d in self.state.evaluation.defects if d.id in (plan.get("defects") or []) and d.status == "in_rework"}
        named = {"targets": plan.get("targets") or []}
        loops = {e.id: rt.traversals[e.id] - 1 for e in self._loop_edges(node_id)
                 if e.id not in legacy["spent"] and outcome_matches(e.when, rt.outcome or "pass", named) and rt.traversals.get(e.id, 0) > 0}
        return {"cycle": cycle, "defects": defects, "loops": loops}

    def _revert_legacy_plan(self, node_id: str, legacy: dict[str, Any]) -> None:
        change, rt = self._legacy_revert(node_id, legacy), self.rt(node_id)
        if change["cycle"] is not None:
            self.state.runtime.rework_cycles -= 1
        for d in self.state.evaluation.defects:
            if d.id in change["defects"]:
                d.status = change["defects"][d.id]
        rt.traversals.update(change["loops"])
        self.ledger.append("legacy_plan_reverted", node=node_id, **change,
                           reason="an engine before v1.5.1 applied this plan although a loop bound blocked it, so none of it ran")

    def _pending_reverts(self) -> list[dict[str, Any]]:
        """The blocks an older engine wrote whose unblock will revert the plan it applied, with what it will change."""
        open_by_node: dict[str, list[Escalation]] = {}
        for e in self.state.runtime.escalations:
            if not e.resolved and e.node in self.state.runtime.nodes:
                open_by_node.setdefault(e.node, []).append(e)
        out = []
        for nid, escs in open_by_node.items():
            try:
                legacy = self._legacy_blocked_plan(nid, escs) if any(e.edge and not e.edges for e in escs) else None
            except LedgerCorrupt as exc:   # a status report never fails on the preview
                out.append({"node": nid, "unavailable": str(exc)})
                continue
            if legacy is not None:
                out.append({"node": nid, **self._legacy_revert(nid, legacy)})
        return out

    def _waive_gaps(self, node_id: str, gaps: list[str], actor: str, reason: str) -> None:
        """A named human proceeds without resolving intake gaps. Only resolvable gaps in the latest gap report can be
        waived, and only before the inputs freeze; the waiver is part of the frozen inputs and of the ledger."""
        freezer = next((n for n in self.wf.nodes if n.gate == "freeze_inputs"), None)
        if freezer is None or node_id != freezer.id:
            raise EngineError(f"gaps are waived when unblocking {freezer.id if freezer else 'the source-validation node'}")
        if self.state.inputs.frozen:
            raise EngineError("the inputs are frozen; a waiver can only be recorded while the intake gaps are open")
        if not reason.strip():
            raise EngineError("a waiver needs the human's reason (--note)")
        latest = next(iter(reversed(self.current("gaps"))), None)
        report = {g["id"]: g for g in (read_json(self.project.root / latest.path).get("gaps", []) if latest else [])}
        resolvable = sorted(k for k, v in report.items() if v.get("severity") == "resolvable")
        bad = [gid for gid in gaps if gid not in resolvable]
        if bad:   # check every id before recording any, so a refused waiver leaves no trace in state or ledger
            raise EngineError(f"{', '.join(bad)}: not a resolvable gap in the latest gap report (resolvable: {resolvable or 'none'}); "
                              "blocking gaps are answered, not waived")
        for gid in dict.fromkeys(gaps):
            if any(w.gap == gid for w in self.state.inputs.waivers):
                continue
            self.state.inputs.waivers.append(GapWaiver(gap=gid, by=actor, reason=reason.strip(), at=now()))
            self.ledger.append("gap_waived", gap=gid, actor=actor, reason=reason.strip())

    @_human_action("restore_control")
    def restore_control(self, doc: str, actor: str) -> dict[str, Any]:
        """A named human puts a control document back to the version the workflow accepted, after an edit outside the
        workflow. The discarded text is kept as `.vpg/controls/<doc>.discarded[-n].md`."""
        self._authorize(actor, "restore-control", "restoring a control document")
        if doc not in CONTROL_DOCS:
            raise EngineError(f"unknown control document '{doc}' (known: {', '.join(CONTROL_DOCS)})")
        writing = [n for n in self._inflight() if doc in self.node(n).controls.write]
        if writing:
            raise EngineError(f"{', '.join(writing)} may be writing {doc}.md now; let it finish, or invalidate it, then restore")
        restored, keep = self.controls.restore_one(doc, tag="discarded",
                                                   before_change=lambda k: self._undo.append(lambda: self.controls.put_back(doc, k)))
        if not restored:
            raise EngineError(f"controls/{doc}.md already matches the version the workflow accepted; nothing to restore")
        self.ledger.append("control_restored", doc=doc, actor=actor,
                           kept=rel(keep, self.project.root) if keep else "none (the document was missing or not a regular file)")
        self._update_status()
        return self.status()

    def _pass_checkpoint(self, nid: str, fingerprint: str) -> tuple[ProjectState, CheckpointRef] | None:
        """The state saved when the node's recorded pass happened: the newest checkpoint of one of its passes that
        carries this last-pass fingerprint."""
        for ref in reversed(self.state.history.checkpoints):
            if ref.event != "node_passed" or ref.node != nid:
                continue
            try:
                st = ProjectState.model_validate(read_json(self.project.root / ref.path))
            except Exception:   # a missing or unreadable checkpoint proves nothing; an older one may still
                continue
            r = st.runtime.nodes.get(nid)
            if r is not None and r.last_pass_fingerprint == fingerprint:
                return st, ref
        return None

    def _carry_over_plan(self, adopt: set[str]) -> dict[str, Any]:
        """Bring each recorded pass up to the current fingerprint scheme where that is provable, so a node whose inputs
        did not change still memo-skips after an engine upgrade instead of re-running once.

        Left alone: a pass recorded under the current scheme, a review (accepting a change re-runs every review), and a
        gate or router (they never memo-skip). A pass whose fingerprint means the same under every scheme (no bound
        command and no `defects:rework` input), or that the current scheme reproduces as it is, is marked current. Any
        other pass recorded before v1.5 is re-derived from the checkpoint written when it passed, with the rework defects
        its node_dispatched event names: the scheme it was recorded under must reproduce its fingerprint exactly, and
        the current scheme is then computed from that same state. A pass from before code fingerprints is carried over
        only for a node a human names in `adopt`, and only if none of its code files was modified or re-created in or
        after the second that pass began.

        Returns carry {node: its fingerprint under the current scheme}, current [nodes already meaning the same],
        kept {node: why it re-runs once at its next re-check}, refused {named node: why} and code {node: the code an
        adopted pass, or one whose code was only narrowed, is carried with, hashed once}. Every named node is either
        carried or refused."""
        carry: dict[str, str] = {}
        current: list[str] = []
        codes: dict[str, list[tuple[str, str]]] = {}
        kept: dict[str, str] = {}
        refused: dict[str, str] = {}
        dispatched = {(e["data"].get("node"), e["data"].get("attempt")): list(e["data"].get("rework") or [])
                      for e in self.ledger.events("node_dispatched")}
        passed_attempt = {e["data"].get("node"): e["data"].get("attempt") for e in self.ledger.events("node_passed")}
        for nid in self.index.topo:
            node, rt, named = self.node(nid), self.rt(nid), nid in adopt
            lp, has_cmd = rt.last_pass_fingerprint, bool(self.project.command_for(nid))
            router = bool(node.gate) or any(e.kind != EdgeKind.FLOW for e in self.index.outgoing(nid))
            skip = ("it has no recorded pass to keep" if not lp or rt.status not in (S.STEP_PASS, S.STALE) else
                    "it is a review, which re-runs after every accepted change" if node.kind == NodeKind.REVIEW else
                    "it always re-runs (a gate or router)" if router else
                    "it runs no bound command" if named and not has_cmd else
                    "its last pass already records its code" if named and rt.last_pass_scheme is not None else
                    "" if rt.last_pass_scheme is not None and rt.last_pass_scheme >= FINGERPRINT_SCHEME else None)
            if skip is not None:
                if named:
                    refused[nid] = skip
                continue
            # a pass made to fix defects recorded them (before v1.5), so it is scheme-dependent whatever the node reads
            reworked = passed_attempt.get(nid) is None or bool(dispatched.get((nid, passed_attempt[nid])))
            if not named and ((not (has_cmd or "defects:rework" in node.inputs) and not reworked) or lp == self.fingerprint(node)):
                current.append(nid)
                continue
            found = self._pass_checkpoint(nid, lp)
            if found is None:
                (refused if named else kept)[nid] = "no checkpoint records its last pass, so it re-runs at its next re-check"
                continue
            cp, ref = found
            cp_rt = cp.runtime.nodes[nid]
            # a pass recorded since v1.5 is settled (recorded without its rework defects); an older one hashed them
            rework = [] if rt.last_pass_scheme is not None else dispatched.get((nid, cp_rt.attempts), [])
            recorded = sorted(cp_rt.command_code.items())
            if rt.last_pass_scheme is not None:   # recorded since v1.5: its scheme is known
                candidates = [(rt.last_pass_scheme, recorded)]
            else:   # before v1.5: 1 when it recorded its code; with no code, 0 or a v1.1+ command whose line names no script
                candidates = [(1, recorded)] if recorded or not has_cmd else [(0, []), (1, [])]
            scheme, code = next(((s, c) for s, c in candidates
                                 if self.fingerprint(node, state=cp, scheme=s, code=c, rework=rework) == lp), (_NO_MATCH, None))
            if scheme is _NO_MATCH:
                (refused if named else kept)[nid] = (
                    f"checkpoint {ref.seq} does not reproduce its last pass: something it reads changed between its dispatch and "
                    "that checkpoint (a node batched with it), or since (its definition, prompt, a control document, rubric, "
                    "project file or command line), so it re-runs at its next re-check")
                continue
            if has_cmd and scheme == 0:
                if not named:
                    kept[nid] = ("its last pass predates command-code fingerprints, so it re-runs at its next re-check; a human "
                                 f"who knows its code is unchanged since keeps it with `vpg accept-config --adopt-code {nid}`")
                    continue
                if not cp_rt.started_at:
                    refused[nid] = "its last pass records no start time, so an edit to its code cannot be ruled out"
                    continue
                code = self.project.command_code(nid)   # hashed first: an edit after this shows in the file times below
                newer = self._changed_since(nid, cp_rt.started_at)
                if newer:
                    refused[nid] = (f"{', '.join(newer[:5])}{', …' if len(newer) > 5 else ''} changed after its last pass began at "
                                    f"{cp_rt.started_at}: re-run it, or list its real code files in command_deps")
                    continue
                codes[nid] = code   # the human vouches that this code made the pass
            elif named:
                refused[nid] = "its last pass already records its code"
                continue
            elif has_cmd:
                code, narrowed = self._narrowed_code(nid, code)
                if narrowed:
                    codes[nid] = code
            carry[nid] = self.fingerprint(node, state=cp, code=code, rework=[])   # recorded as settled, like a v1.5 pass
        return {"carry": carry, "current": current, "kept": kept, "refused": refused, "code": codes}

    def _carry_over(self, plan: dict[str, Any], actor: str, reason: str, adopt: list[str]) -> None:
        """Record a carry-over plan: carried passes get their current-scheme fingerprint, and every pass the plan proved
        gets the current scheme. An adopted pass (the code the human vouched for) and a narrowed one (the part of its
        recorded code it now covers) record that code, so `vpg status` does not report a change that re-runs nothing."""
        for nid, fp in plan["carry"].items():
            self.rt(nid).last_pass_fingerprint = fp
        for nid in [*plan["carry"], *plan["current"]]:
            self.rt(nid).last_pass_scheme = FINGERPRINT_SCHEME
        for nid, code in (plan.get("code") or {}).items():   # the same hashes the carried fingerprint was computed from
            self.rt(nid).command_code = dict(code)
        if plan["carry"] or plan["kept"]:
            self.ledger.append("passes_carried_over", actor=actor, reason=reason, carried=sorted(plan["carry"]), adopted=adopt,
                               kept=plan["kept"])

    def _narrowed_code(self, nid: str, recorded: list) -> tuple[list, bool]:
        """(the current code, True) if every file the node's code now covers is one its pass recorded, byte for byte: a
        narrower command_deps or a new command_code_exclude (accepted with this change) then keeps the pass instead of
        re-running it, since the code it now depends on is exactly the code that made it. The code is hashed once here, so
        the carried fingerprint and the recorded code map are the bytes this check saw. Otherwise (the recorded code, False)."""
        recorded_by_path = {p: h for p, h in (tuple(c) for c in recorded)}
        current = self.project.command_code(nid)
        if current and all(recorded_by_path.get(p) == h for p, h in current):
            return current, True
        return recorded, False

    def _changed_since(self, nid: str, when: str) -> list[str]:
        """The node's current code files modified or re-created in or after the second `when` names. The change time
        counts, so restoring an older modification time (cp -p, rsync -a, touch -r) does not hide an edit, and recorded
        times are floored to the second, so an edit in that second may have come after it."""
        limit = datetime.fromisoformat(when).timestamp()
        out = []
        for path, _ in self.project.command_code(nid):
            try:
                st = (self.project.root / path).stat()
                if int(max(st.st_mtime, st.st_ctime)) >= limit:
                    out.append(path)
            except OSError:
                out.append(path)
        return out

    @_human_action("accept_config")
    def accept_config(self, actor: str, reason: str, adopt_code: list[str] | None = None) -> dict[str, Any]:
        """A named human accepts a mid-run change to the workflow, rules, rubrics, thresholds, project config or engine.
        The reviews and the gate re-run under it: a review's fingerprint includes the accepted configuration, so no earlier
        verdict survives a change in how work is judged. Recorded passes are carried over to the current fingerprint
        scheme where that is provable (_carry_over_plan). `adopt_code` names command nodes whose last pass predates code
        fingerprints and whose code the human vouches for."""
        # never delegable: an accepted configuration can rewrite the workflow, rubrics, rules or engine (this boundary too)
        self._require_human(actor, "accepting a configuration change")
        adopt = list(dict.fromkeys(adopt_code or []))
        unknown = [n for n in adopt if n not in self.index.ids]
        if unknown:
            raise EngineError(f"unknown node(s) to adopt: {', '.join(unknown)}")
        plan = self._carry_over_plan(set(adopt))
        if plan["refused"]:
            raise EngineError("cannot keep the last pass of " + "; ".join(f"{n}: {w}" for n, w in plan["refused"].items()))
        self._carry_over(plan, actor, reason, adopt)
        old, new = self.state.runtime.config_sha256, self._config_fingerprint()
        self.state.runtime.config_sha256, self.state.runtime.config_parts = new, self._config_parts()
        kb = summarize(self.rules_path)
        k = self.state.knowledge
        k.rules_path, k.rules_version, k.rules_sha256, k.enforced, k.advisory = (str(self.rules_path), kb["version"], kb["sha256"],
                                                                                 kb["enforced"], kb["advisory"])
        for esc in self.state.runtime.escalations:
            if esc.node == "integrity" and not esc.resolved:
                esc.resolved = True
        judged = [n.id for n in self.wf.nodes if (n.kind == NodeKind.REVIEW or n.gate == "review_gate") and self.rt(n.id).status != S.PLANNED]
        if judged:
            self.invalidate(judged, reason=f"configuration change accepted by {actor}")
        rerun = self._code_changed_nodes()   # #99: the accepted code reaches the film now, not at a re-check nobody schedules
        if rerun:
            self.invalidate(rerun, reason=f"their code changed since they last ran; accepting the configuration ({actor}) runs the new code")
        self.ledger.append("config_accepted", actor=actor, reason=reason, old=old[:16], new=new[:16], rechecked=judged, rerun=rerun)
        if not any(not e.resolved for e in self.state.runtime.escalations):
            self.state.runtime.status = RunStatus.RUNNING
        self._update_status()
        self._save("accept_config")
        return self.status()

    # ------------------------------------------------------------------ status, persistence, lineage
    def _end_reached(self) -> bool:
        return all(self._edge_satisfied(e) for e in self.index.incoming(self.wf.end, EdgeKind.FLOW))

    def _update_status(self) -> None:
        nodes = self.state.runtime.nodes.values()
        if any(rt.status == S.BLOCKED for rt in nodes) or (self.state.runtime.status == RunStatus.BLOCKED
                                                            and any(not e.resolved for e in self.state.runtime.escalations)):
            self.state.runtime.status = RunStatus.BLOCKED
        elif self._end_reached():
            if self.state.runtime.status != RunStatus.RUN_PASS:
                self.ledger.append("run_pass", run=self.state.runtime.run_id, version=self.state.runtime.current_version)
            self.state.runtime.status = RunStatus.RUN_PASS
        elif any(rt.status == S.WAITING_HUMAN for rt in nodes):
            self.state.runtime.status = RunStatus.WAITING_HUMAN
        elif any(rt.status == S.WAITING_RUNTIME for rt in nodes):
            self.state.runtime.status = RunStatus.WAITING_RUNTIME
        elif not self.ready_nodes() and not any(rt.status in (S.RUNNING, S.VALIDATING) for rt in nodes):
            self._escalate_stall()
        else:
            self.state.runtime.status = RunStatus.RUNNING

    def _escalate_stall(self) -> None:
        """No node can run and END is not reached: a join waits for a branch that never ran. Stop and say which."""
        def label(src: str) -> str:
            return "START" if src == "START" else f"{src} [{self.rt(src).status.value}, outcome {self.rt(src).outcome}]"
        waiting = []
        for target in self.index.topo + [self.wf.end]:
            if target != self.wf.end and self.rt(target).status == S.STEP_PASS:
                continue
            unmet = [f"{label(e.source)} needs '{e.when}'" for e in self.index.incoming(target, EdgeKind.FLOW) if not self._edge_satisfied(e)]
            met = [e for e in self.index.incoming(target, EdgeKind.FLOW) if self._edge_satisfied(e)]
            if unmet and met:
                waiting.append(f"{target} waits for {'; '.join(unmet)}")
        unhandled = [f"{nid} passed with outcome '{rt.outcome}', which no edge handles" for nid, rt in self.state.runtime.nodes.items()
                     if rt.status == S.STEP_PASS and not any(outcome_matches(e.when, rt.outcome, rt.result_data) for e in self.index.outgoing(nid))]
        report = ["no node can run and END is not reached"] + unhandled + waiting
        if len(report) == 1:
            report.append("no join has partial input and every outcome was handled; inspect `vpg status --json`")
        n = len(self.state.runtime.escalations) + 1
        path = self.project.vpg / "escalations" / f"run-no-progress-{n}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 🔴 Blocked — no progress possible\n\n" + "\n".join(f"- {x}" for x in report)
                        + "\n\n## What unblocks it\n- The graph lets a branch strand a join. Fix it so `vpg validate` passes, or roll back "
                        + f"to a checkpoint before the branch (`vpg rollback`), then run `vpg unblock {rel(self.project.root, Path.cwd())} "
                        + "no-progress --actor <name>`.\n")
        self._escalate_run("no-progress", "; ".join(report))

    def status(self) -> dict[str, Any]:
        st = self.state
        trend = self._trend()
        return {
            "run": st.runtime.run_id, "status": st.runtime.status.value, "version": st.runtime.current_version,
            "rework_cycles": st.runtime.rework_cycles, "max_rework_cycles": self._max_rework_cycles(), "resumes": st.runtime.resumes,
            "loops": [{"edge": e.id, "from": e.source, "to": e.target, "used": st.runtime.nodes[e.source].traversals.get(e.id, 0),
                       "max": self._bound(e)} for e in self.wf.edges if e.kind == EdgeKind.LOOP and e.source in st.runtime.nodes
                      and st.runtime.nodes[e.source].traversals.get(e.id, 0)],
            "budget": self._budget_status(),
            "trend": trend[-8:],
            "convergence": dict(zip(("converging", "why"), convergence(trend))) if len(trend) >= 2 else None,
            "pending_reverts": self._pending_reverts(),
            "nodes": {nid: {"num": self.node(nid).num, "status": rt.status.value, "attempts": rt.attempts, "executions": rt.executions,
                            "memo_hits": rt.memo_hits, "outcome": rt.outcome} for nid, rt in st.runtime.nodes.items()},
            "waiting_runtime": {nid: rt.task_packet for nid, rt in st.runtime.nodes.items() if rt.status == S.WAITING_RUNTIME},
            "waiting_human": [nid for nid, rt in st.runtime.nodes.items() if rt.status == S.WAITING_HUMAN],
            "escalations": [e.model_dump() for e in st.runtime.escalations if not e.resolved],
            "open_defects": [f"{d.id} [{d.severity}] {d.dimension} → {d.traced_node}" for d in st.evaluation.defects if d.status in ("open", "persisting")],
            "gates": [(g.version, g.outcome) for g in st.evaluation.gate_results],
            "holds": [f"{d.id}: {d.human_hold}" for d in st.evaluation.defects if d.human_hold and d.status in ("open", "persisting", "in_rework")]
                     + [f"{f.id}: {f.hold}" for f in st.evaluation.feedback if f.hold],
            "warnings": self._code_warnings() + self.project.reuse_advisories(set(self.index.ids)) + self._publish_warnings()
                        + self._source_warnings() + self._threshold_warnings(),
            "grants": [f"{g.id} {g.grantee} may {', '.join(g.actions)} for {g.grantor} until {g.expires}" for g in self._live_grants()],
            "triage": [f"{f.id} by {f.actor} (v{f.version}): {f.text[:120]}" for f in st.evaluation.feedback if f.triage == "pending"],
            "deferred_change_requests": [f"{c.id} on {c.doc}.md until: {c.revisit}" for c in st.history.change_requests
                                         if c.status == "deferred"],
            "notes": self._status_notes(),
        }

    def _threshold_warnings(self) -> list[str]:
        """#101: a malformed evaluation.md threshold is named now, not first as a failed N18 attempt."""
        try:
            from .tools.legibility import scene_limits
            th = (self.controls.front_matter("evaluation") or {}).get("thresholds") or {}
            scene_limits(th.get("density") or {} if isinstance(th, dict) else {})
        except ValueError as exc:
            return [f"controls/evaluation.md: {exc}; N18 fails every attempt until it is fixed (a change request on evaluation)"]
        except Exception as exc:   # a status report never fails on a control document
            return [f"controls/evaluation.md: its thresholds could not be read ({type(exc).__name__})"]
        return []

    def _status_notes(self) -> list[str]:
        """#33: when open human feedback takes effect."""
        waiting = [f.id for f in self.state.evaluation.feedback if f.status == "open"]
        if not waiting:
            return []
        return [f"feedback {', '.join(waiting)} is interpreted at the next N21 ({self._gate_node()}): after the reviews of the "
                "version in progress, not at once"]

    def _budget_status(self) -> dict[str, Any] | None:
        """The project's loop budget as it applies now; None when the project declares none and nothing was granted."""
        b, rt = self.budget, self.state.runtime
        if not b.declared and not rt.loop_extra and not rt.rework_extra:
            return None
        return {"profile": b.profile, "on_exhaust": b.on_exhaust, "rework_loop_traversals": b.max_traversals,
                "rework_cycles": self._max_rework_cycles(), "extend_by": b.extend_by, "max_hours": b.max_hours,
                "granted": {"loops": dict(rt.loop_extra), "rework_cycles": rt.rework_extra}}

    def _publish_warnings(self) -> list[str]:
        if "publish" not in self.project.config:
            return []
        try:
            from .publish import status_warnings
            return status_warnings(self)
        except Exception as exc:
            return [f"publish: status could not be read ({type(exc).__name__})"]

    def _queue_publish(self, version: int) -> None:
        """Note a version to publish once the state that cut it is saved (`_drain_publish`), so a slow privacy scan never
        sits between a pass and its save, and a crash never costs the pass."""
        cfg = self.project.config.get("publish")
        if isinstance(cfg, dict) and cfg.get("auto") and version not in self._publish_queue:
            self._publish_queue.append(version)

    def _drain_publish(self) -> None:
        """Publish the queued versions (only those), after a save. A failure is ledgered and shown by `vpg status`; it never
        changes a node or the run."""
        queue, self._publish_queue = self._publish_queue, []
        if not queue or self.read_only:
            return
        from .publish import publish
        for version in queue:
            try:
                publish(self, version=version)
            except Exception as exc:
                self.ledger.append("publish_failed", version=version, error=type(exc).__name__)
        self._save("publish")

    def _code_warnings(self) -> list[str]:
        """What an operator should know about bound commands' code: none of it is fingerprinted, a last pass predates
        code fingerprints (it re-runs once), or the code changed since the node last ran (it re-runs at its next
        re-check; a generated file belongs in command_code_exclude)."""
        bad = self.project.config_problems(set(self.index.ids)) + self._preflight_node_problems()
        if bad:   # a read-only engine opens despite them; name them instead of failing on one
            return [f"vpg.project.yaml: {b}" for b in bad] + ["the run will not start until vpg.project.yaml is fixed"]
        out = []
        for nid in self.index.topo:
            if nid not in self.state.runtime.nodes or not self.project.command_for(nid):
                continue
            rt = self.rt(nid)
            try:
                code = self.project.command_code(nid)
            except Exception as exc:   # a status report never fails on one node's code
                out.append(f"{nid}: its command's code could not be read ({type(exc).__name__}: {exc})")
                continue
            if not code:
                out.append(f"{nid}: its command names no script in the project, so editing its code does not re-run it; "
                           "list its files in command_deps")
            elif rt.last_pass_fingerprint and rt.last_pass_scheme is None and not rt.command_code:
                out.append(f"{nid}: its last pass predates command-code fingerprints, so it re-runs once at its next re-check; "
                           f"if its code is unchanged since, a human keeps it with `vpg accept-config --adopt-code {nid}`")
            elif rt.status in (S.STEP_PASS, S.STALE) and rt.command_code:
                now_code = dict(code)
                own = sorted(p for p, h in rt.self_written_code.items() if now_code.get(p) == h)
                if own:   # #105
                    out.append(f"{nid}: its command rewrote {len(own)} file(s) it counts as code while it ran ({', '.join(own[:5])}"
                               f"{', …' if len(own) > 5 else ''}); they count as its output until an edit changes them. List "
                               "them in command_code_exclude")
                changed = sorted(p for p in now_code if rt.command_code.get(p) != now_code[p] and p not in own)
                dropped = sorted(p for p in rt.command_code if p not in now_code)
                if changed:
                    runs = ("it is human-gated, so it re-runs only when a human invalidates it" if self._human_gated(nid) else
                            "the next accepted configuration or rework cycle runs the new code "
                            f"(`vpg invalidate <project> {nid} --actor <name> --reason …` runs it now)")
                    out.append(f"{nid}: its code changed since it last ran ({', '.join(changed[:5])}"
                               f"{', …' if len(changed) > 5 else ''}); {runs}. A file a command generates belongs in "
                               "command_code_exclude"
                               + (f". {len(dropped)} file(s) it recorded are no longer counted (command_deps or "
                                  "command_code_exclude)" if dropped else ""))
                elif dropped:   # #97: a narrowed code set is not a change; an accepted configuration carries the pass
                    out.append(f"{nid}: command_deps or command_code_exclude now leave out {len(dropped)} file(s) its last "
                               f"pass recorded ({', '.join(dropped[:5])}{', …' if len(dropped) > 5 else ''}). Every file it "
                               "still counts is unchanged, so accepting the configuration keeps its pass")
        return out

    def _writable(self) -> None:
        if self.read_only:
            raise EngineError("this engine was opened read-only; changing the run needs the project lock")

    def _save(self, event: str) -> None:
        if self._in_action:
            return   # the human action commits once, when it completes
        self._writable()
        self._write_state()

    def _write_state(self) -> None:
        """Save state.json only if it is exactly as this engine last read or wrote it (see util.ConcurrentChange)."""
        if file_identity(self.project.state_path) != self._state_identity:
            raise ConcurrentChange(f"{self.project.state_path} changed since this command opened the run (another vpg "
                                   "process); nothing was written. Retry the command")
        self.state.runtime.sequence += 1
        self.state.runtime.ledger_tail = list(self.ledger._last)   # a ledger that later lacks this event lost events
        write_json_atomic(self.project.state_path, self.state)
        self._state_identity = file_identity(self.project.state_path)

    @contextmanager
    def _action(self, event: str) -> Iterator[None]:
        """One human action is one durable unit. Its state changes stay in memory and its ledger events are buffered;
        the state is saved once at the end, and only then are the events written. If anything fails, nothing is
        written and the in-memory state is put back, so a retry never records the same decision twice."""
        if self._in_action:
            yield
            return
        snapshot = self.state.model_copy(deep=True)
        self._in_action, self._undo = True, []
        try:
            with self.ledger.deferred() as lines:
                yield
                self._in_action = False
                self.state.runtime.pending_ledger = list(lines)   # write-ahead: the events commit with the state
                self._save(event)
        except BaseException:
            self.state = snapshot
            self.controls = ControlStore(self.project.root, self.state, self.project.vpg)
            for undo in reversed(self._undo):   # e.g. a rollback's restored documents go back; each one runs regardless
                try:
                    undo()
                except Exception as exc:   # an undo that can't run (e.g. the same read-only file) must not stop the others
                    self._undo_failures.append(f"{type(exc).__name__}: {exc}")
            if self._undo_failures:
                import sys as _sys
                _exc = _sys.exc_info()[1]
                if _exc is not None:
                    _exc.add_note("could not undo part of this action (check these files by hand): " + "; ".join(self._undo_failures))
            raise
        finally:
            self._in_action, self._undo = False, []
            self.ledger.stamp = {}   # a grant covers only the action it authorized
        committed = self.state.runtime.pending_ledger
        try:
            self.ledger.write_lines(committed)
        except Exception as exc:
            raise LedgerBehind(f"the {event} was saved, but its {len(committed)} ledger event(s) could not be written ({exc}). "
                               "They are kept in the state and written by the next command that changes the run (for example "
                               "`vpg run`); don't repeat the action") from exc
        self.state.runtime.pending_ledger = []   # persisted with the next save

    def _checkpoint(self, event: str, node_id: str | None) -> None:
        self._writable()
        seq = len(self.state.history.checkpoints) + 1
        path = self.project.vpg / "checkpoints" / f"{seq:05d}-{event}-{node_id or 'run'}.json"
        self.state.history.checkpoints.append(CheckpointRef(seq=seq, path=rel(path, self.project.root), event=event, node=node_id, at=now()))
        write_json_atomic(path, self.state)
        if not self._in_action:   # inside a human action the state is saved once, at its commit
            self._write_state()
        self.ledger.append("checkpoint", seq=seq, node=node_id, event=event)

    @_human_action("rollback")
    def rollback(self, seq: int, actor: str) -> dict[str, Any]:
        """Restore a checkpoint's content state. Budgets never go backwards: loop traversals, rework cycles and
        repeated-defect counts carry forward, and open escalations stay open until a human unblocks them."""
        self._require_human(actor, "a rollback")
        ref = next((c for c in self.state.history.checkpoints if c.seq == seq), None)
        if ref is None:
            raise EngineError(f"no checkpoint {seq}; `vpg status --json` lists the run, and .vpg/checkpoints/ holds them")
        cur = self.state
        restored = ProjectState.model_validate(read_json(self.project.root / ref.path))
        restored.history.checkpoints = cur.history.checkpoints   # keep the full checkpoint list
        restored.history.grants = cur.history.grants   # authority never goes backwards: a revoked grant stays revoked
        rt_new, rt_cur = restored.runtime, cur.runtime
        rt_new.rework_cycles = max(rt_new.rework_cycles, rt_cur.rework_cycles)
        rt_new.rework_extra = max(rt_new.rework_extra, rt_cur.rework_extra)
        for eid, extra in rt_cur.loop_extra.items():
            rt_new.loop_extra[eid] = max(rt_new.loop_extra.get(eid, 0), extra)
        for sig, count in rt_cur.defect_failures.items():
            rt_new.defect_failures[sig] = max(rt_new.defect_failures.get(sig, 0), count)
        for nid, node_rt in rt_cur.nodes.items():
            if nid in rt_new.nodes:
                for eid, count in node_rt.traversals.items():
                    rt_new.nodes[nid].traversals[eid] = max(rt_new.nodes[nid].traversals.get(eid, 0), count)
        rt_new.escalations, rt_new.resumes, rt_new.sequence = rt_cur.escalations, rt_cur.resumes, rt_cur.sequence
        judged_under_other = rt_new.config_sha256 != rt_cur.config_sha256
        rt_new.config_sha256, rt_new.config_parts = rt_cur.config_sha256, rt_cur.config_parts
        if judged_under_other:   # the rule base in force is the accepted one, not the checkpoint's
            restored.knowledge = cur.knowledge
        if any(not e.resolved for e in rt_new.escalations):
            rt_new.status = RunStatus.BLOCKED
        for esc in rt_new.escalations:   # an escalation carried over: does its node still block at the restored checkpoint?
            if not esc.resolved and esc.node in rt_new.nodes:
                esc.reopened = ("" if rt_new.nodes[esc.node].status == S.BLOCKED
                                else "a rollback had restored a checkpoint at which it was not blocked")
        self.state = restored
        self.controls = ControlStore(self.project.root, self.state, self.project.vpg)
        restored = self.controls.restore_to_state(   # each restored document is undoable at once, even if a later one fails
            tag=f"before-rollback-{seq}-r{self.state.runtime.resumes}",
            on_restored=lambda d, k: self._undo.append(lambda d=d, k=k: self.controls.put_back(d, k)))
        restored_docs = [doc for doc, _ in restored]
        rechecked = []
        if judged_under_other:
            # restored passes may predate the engine in force: carry over what is provable (no adoption), and its verdicts
            # were reached under another configuration: judge them again, as accept_config does
            self._carry_over(self._carry_over_plan(set()), actor, f"rollback to checkpoint {seq}", [])
            judged = [n.id for n in self.wf.nodes if (n.kind == NodeKind.REVIEW or n.gate == "review_gate")
                      and self.rt(n.id).status != S.PLANNED]
            if judged:
                rechecked = self.invalidate(judged, reason=f"rollback to checkpoint {seq}, whose verdicts were reached under "
                                                           "another configuration")
        self.ledger.append("rollback", to=seq, actor=actor, rework_cycles=rt_new.rework_cycles,
                           open_escalations=sum(not e.resolved for e in rt_new.escalations), restored_controls=restored_docs,
                           rechecked=rechecked)
        self._update_status()
        self._save("rollback")
        return self.status()

    def lineage(self, artifact_id: str) -> list[dict[str, Any]]:
        out, stack, seen = [], [artifact_id], set()
        while stack:
            aid = stack.pop()
            if aid in seen or aid not in self.state.artifacts:
                continue
            seen.add(aid)
            a = self.state.artifacts[aid]
            out.append({"id": a.id, "node": a.node, "attempt": a.attempt, "sha256": a.sha256[:16], "status": a.status, "inputs": a.inputs})
            stack.extend(a.inputs)
        return out


def _dims(node: NodeDef, rubrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for rid in node.rubric for d in rubrics.get(rid, {}).get("dimensions", [])]


def _all_dim_ids(rubrics: dict[str, Any]) -> set[str]:
    return {d["id"] for r in rubrics.values() for d in r["dimensions"]}
