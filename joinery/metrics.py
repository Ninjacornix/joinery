"""What each change cost: wall time by span and tokens per agent turn (ADR 0054).

One plain JSONL file per change under ``<project>/.joinery/metrics/``, named by
its transaction id (or the turn's id when nothing was staged). Each line is one
span; spans are named and attributed after the OpenTelemetry GenAI semantic
conventions (``gen_ai.operation.name``, ``gen_ai.usage.input_tokens``, …) so an
OTel viewer's importer reads them as they are. Joinery's own facts use the
``joinery.`` prefix. Fixed labels, durations and counts only: never prompts,
replies, payloads or tool arguments. Nothing is exported; nothing leaves the
machine.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: OpenTelemetry GenAI semantic-convention attribute names.
OPERATION = "gen_ai.operation.name"
PROVIDER = "gen_ai.provider.name"
AGENT_NAME = "gen_ai.agent.name"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
TOOL_NAME = "gen_ai.tool.name"
#: Input tokens include the cached ones, as the conventions define it.
INPUT_TOKENS = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
CACHED_TOKENS = "gen_ai.usage.cache_read.input_tokens"
ERROR_TYPE = "error.type"

#: Joinery's own attributes.
KIND = "joinery.span.kind"
TRANSACTION = "joinery.transaction.id"
ROUND = "joinery.round"
OUTCOME = "joinery.outcome"
DECISION = "joinery.decision"
AGENT = "joinery.agent"
TASK = "joinery.task.id"
WORKER_START_MS = "joinery.worker.start_ms"
WORKER_WORK_MS = "joinery.worker.work_ms"
SENT_BACK_BY = "joinery.send_back.by"
SUBMITS = "joinery.submit.attempts"
SEND_BACKS = "joinery.review.send_backs"
CONTINUATIONS = "joinery.continuations"
USAGE_REPORTED = "joinery.usage.reported"
PRECHECKS = "joinery.prechecks"
#: Model routing (ADR 0061): which model ran a turn ("light" or "main"), and how a
#: review was routed ("skip", "light" or "main") for a change of which risk.
MODEL_TIER = "joinery.model.tier"
REVIEW_RISK = "joinery.review.risk"
REVIEW_ROUTE = "joinery.review.route"
REVIEW_ESCALATED = "joinery.review.escalated"
#: A findings span: who found them, whether the change was dropped, and the rule
#: ids (``joinery.reasons``), comma-separated, one per item.
FOUND_BY = "joinery.findings.by"
FINAL = "joinery.findings.final"
RULES = "joinery.findings.rules"

#: Span kinds, in the order the breakdown lists them.
CHANGE = "change"
AGENT_TURN = "agent_turn"
TOOL = "tool"
WORKER_START = "worker_start"
CHECKS = "checks"
REVIEWER = "reviewer"
REVIEW_RENDER = "review_render"
SEND_BACK = "send_back"
PRECHECK = "precheck"
FINDINGS = "findings"
DECIDED = "decision"
KINDS = (
    AGENT_TURN,
    TOOL,
    WORKER_START,
    PRECHECK,
    CHECKS,
    REVIEWER,
    REVIEW_RENDER,
    SEND_BACK,
)

#: The latency phases' names (joinery.timing) as span kinds.
PHASE_KINDS = {
    "tools": TOOL,
    "tool_connection": WORKER_START,
    "submit": CHECKS,
    "precheck": PRECHECK,
    "review_media": REVIEW_RENDER,
}

#: Agent id → the conventions' well-known provider name, where one provider is sure.
PROVIDERS = {"claude": "anthropic", "codex": "openai", "gemini": "gcp.gemini"}

#: What a worker may put in its tool result's ``_meta`` to split its time.
META_WORKER_START = "joinery/worker_start_ms"
META_WORKER_WORK = "joinery/work_ms"

#: Changes kept on disk; the oldest go first.
KEEP = 500
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _span_id() -> str:
    return uuid.uuid4().hex[:16]


class Clock:
    """A wall-clock start with a monotonic duration, so a clock change can't skew it."""

    def __init__(self) -> None:
        self.wall = time.time_ns()
        self._start = time.monotonic_ns()

    def end(self) -> int:
        return self.wall + time.monotonic_ns() - self._start


class Trace:
    """One change's spans while it runs; :meth:`save` writes them.

    A span that ends after the save (a review render still running when the
    turn ends) is appended to the same file.
    """

    def __init__(self, trace_id: str = "") -> None:
        self.trace_id = trace_id or uuid.uuid4().hex
        self.root_id = _span_id()
        self.clock = Clock()
        self.spans: list[dict[str, Any]] = []
        #: The change's own attributes (the root span's).
        self.attributes: dict[str, Any] = {}
        self.path: Path | None = None

    @contextmanager
    def span(
        self, kind: str, name: str = "", attributes: Mapping[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Time the block as one span; the yielded dict takes more attributes."""
        attrs = dict(attributes or {})
        clock = Clock()
        failed = False
        try:
            yield attrs
        except BaseException as exc:
            failed = True
            attrs.setdefault(ERROR_TYPE, type(exc).__name__)
            raise
        finally:
            self.add(kind, name, clock.wall, clock.end(), attrs, failed)

    def add(
        self,
        kind: str,
        name: str,
        start_ns: int,
        end_ns: int,
        attributes: Mapping[str, Any] | None = None,
        failed: bool = False,
    ) -> dict[str, Any]:
        record = {
            "trace_id": self.trace_id,
            "span_id": _span_id(),
            "parent_span_id": self.root_id,
            "name": name or kind,
            "start_time_unix_nano": start_ns,
            "end_time_unix_nano": max(end_ns, start_ns),
            "attributes": {KIND: kind, **_plain(attributes or {})},
            "status": {"code": "ERROR" if failed else "OK"},
        }
        if self.path is None:
            self.spans.append(record)
        else:
            try:
                _append(self.path, [record])
            except (OSError, ValueError):
                pass  # Evidence never fails the work it measures.
        return record

    def root(self, end_ns: int) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.root_id,
            "parent_span_id": "",
            "name": CHANGE,
            "start_time_unix_nano": self.clock.wall,
            "end_time_unix_nano": end_ns,
            "attributes": {KIND: CHANGE, **_plain(self.attributes)},
            "status": {"code": "OK"},
        }

    def save(self, project: Path) -> Path:
        """Write the change's spans; later spans append to the same file."""
        identifier = str(self.attributes.get(TRANSACTION) or self.trace_id)
        target = _file(project, identifier)
        _append(target, [self.root(self.clock.end()), *self.spans])
        self.spans = []
        self.path = target
        prune(project)
        return target


def _plain(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Attribute values OTel allows: strings, numbers and booleans."""
    return {
        str(k): v
        for k, v in attributes.items()
        if isinstance(v, (str, int, float, bool)) and v != ""
    }


def metrics_dir(project: Path) -> Path:
    return project / ".joinery/metrics"


def _file(project: Path, identifier: str) -> Path:
    if not _ID.match(identifier):
        raise ValueError("A change id is letters, digits, - and _ only.")
    return metrics_dir(project) / (identifier + ".jsonl")


def _append(target: Path, records: list[dict[str, Any]]) -> None:
    project = target.parent.parent.parent
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("Change metrics must stay inside this project.")
    if not target.parent.resolve().is_relative_to(project.resolve()):
        raise ValueError("Change metrics must stay inside this project.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record, separators=(",", ":")) + "\n")


def prune(project: Path, keep: int = KEEP) -> None:
    files = sorted(
        metrics_dir(project).glob("*.jsonl"), key=lambda p: p.stat().st_mtime
    )
    for old in files[: max(len(files) - keep, 0)]:
        old.unlink(missing_ok=True)


def record_decision(project: Path, transaction_id: str, accepted: bool) -> None:
    """The user's Accept or Reject, added to the change's file when there is one."""
    try:
        target = _file(project, transaction_id)
        if not target.is_file():
            return
        now = time.time_ns()
        _append(
            target,
            [
                {
                    "trace_id": "",
                    "span_id": _span_id(),
                    "parent_span_id": "",
                    "name": DECIDED,
                    "start_time_unix_nano": now,
                    "end_time_unix_nano": now,
                    "attributes": {
                        KIND: DECIDED,
                        TRANSACTION: transaction_id,
                        DECISION: "accepted" if accepted else "rejected",
                    },
                    "status": {"code": "OK"},
                }
            ],
        )
    except (OSError, ValueError):
        pass


# --- Tokens ------------------------------------------------------------------------


Totals = tuple[int, int, int]


def usage_delta(before: Totals, now: Totals) -> Totals:
    """One turn's tokens from a session's running totals (input, output, cached).

    Providers report session totals. A total that went down means a new
    session started, so its totals are all this turn's."""
    if any(n < b for n, b in zip(now, before, strict=True)):
        return now
    return (now[0] - before[0], now[1] - before[1], now[2] - before[2])


def token_attributes(tokens: Totals | None) -> dict[str, Any]:
    if tokens is None:
        return {USAGE_REPORTED: False}
    return {
        USAGE_REPORTED: True,
        INPUT_TOKENS: tokens[0],
        OUTPUT_TOKENS: tokens[1],
        CACHED_TOKENS: tokens[2],
    }


def agent_attributes(
    agent: str, role: str, model: str = "", tier: str = ""
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        OPERATION: "invoke_agent",
        AGENT_NAME: role,
        AGENT: agent,
        RESPONSE_MODEL: model,
        MODEL_TIER: tier,
    }
    if agent in PROVIDERS:
        attrs[PROVIDER] = PROVIDERS[agent]
    return attrs


def worker_split(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """A tool result's own start/work split, when its worker reports one."""
    attrs: dict[str, Any] = {}
    for key, attr in (
        (META_WORKER_START, WORKER_START_MS),
        (META_WORKER_WORK, WORKER_WORK_MS),
    ):
        value = (meta or {}).get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            attrs[attr] = round(float(value))
    return attrs


# --- Reading -----------------------------------------------------------------------


def _ms(span: Mapping[str, Any]) -> float:
    return (int(span["end_time_unix_nano"]) - int(span["start_time_unix_nano"])) / 1e6


def read(path: Path) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            span = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(span, dict)
            and isinstance(span.get("attributes"), dict)
            and isinstance(span.get("start_time_unix_nano"), int)
            and isinstance(span.get("end_time_unix_nano"), int)
        ):
            spans.append(span)
    return spans


def summarise(identifier: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    """One change's breakdown: total time, time by span kind, tokens and rounds.

    Spans nest (an agent turn holds its tool calls), so the kinds' times
    overlap and don't add up to the total."""
    roots = [s for s in spans if s["attributes"].get(KIND) == CHANGE]
    by_kind: dict[str, float] = {}
    counts: dict[str, int] = {}
    tokens = {"input": 0, "output": 0, "cached": 0}
    reported = False
    turns = 0
    decision = ""
    #: Which model ran which role's turns: (role, model, tier) -> turns.
    models: dict[tuple[str, str, str], int] = {}
    routes: dict[str, int] = {}
    escalations = 0
    for span in spans:
        attrs = span["attributes"]
        kind = str(attrs.get(KIND, ""))
        if kind == DECIDED:
            decision = str(attrs.get(DECISION, ""))
            continue
        if kind in {CHANGE, FINDINGS}:
            continue
        by_kind[kind] = by_kind.get(kind, 0.0) + _ms(span)
        counts[kind] = counts.get(kind, 0) + 1
        if kind == REVIEWER:
            route = str(attrs.get(REVIEW_ROUTE, ""))
            if route:
                routes[route] = routes.get(route, 0) + 1
            if attrs.get(REVIEW_ESCALATED) is True:
                escalations += 1
        if kind == AGENT_TURN:
            key = (
                str(attrs.get(AGENT_NAME, "")),
                str(attrs.get(RESPONSE_MODEL, "") or attrs.get(REQUEST_MODEL, "")),
                str(attrs.get(MODEL_TIER, "") or "main"),
            )
            models[key] = models.get(key, 0) + 1
            turns += 1
            if attrs.get(USAGE_REPORTED):
                reported = True
                tokens["input"] += int(attrs.get(INPUT_TOKENS, 0) or 0)
                tokens["output"] += int(attrs.get(OUTPUT_TOKENS, 0) or 0)
                tokens["cached"] += int(attrs.get(CACHED_TOKENS, 0) or 0)
    first = min((int(s["start_time_unix_nano"]) for s in roots), default=0)
    total = sum(_ms(s) for s in roots)
    root_attrs: dict[str, Any] = {}
    for root in roots:
        root_attrs.update(root["attributes"])
    send_backs = sum(int(r["attributes"].get(SEND_BACKS, 0) or 0) for r in roots)
    return {
        "id": identifier,
        "transaction_id": str(root_attrs.get(TRANSACTION, "")),
        "started_unix_nano": first,
        "requests": len(roots),
        "outcome": str(root_attrs.get(OUTCOME, "")),
        "decision": decision,
        "agent": str(root_attrs.get(AGENT, "")),
        "model": str(root_attrs.get(RESPONSE_MODEL, "")),
        "total_ms": round(total),
        "by_kind_ms": {k: round(v) for k, v in sorted(by_kind.items())},
        "counts": dict(sorted(counts.items())),
        "tokens": tokens if reported else None,
        "models": [
            {"role": role, "model": model, "tier": tier, "turns": n}
            for (role, model, tier), n in models.items()
        ],
        "review_routes": dict(sorted(routes.items())),
        "escalations": escalations,
        "rounds": {
            "agent_turns": turns,
            "submits": sum(int(r["attributes"].get(SUBMITS, 0) or 0) for r in roots),
            "send_backs": send_backs,
            "continuations": sum(
                int(r["attributes"].get(CONTINUATIONS, 0) or 0) for r in roots
            ),
            "prechecks": sum(
                int(r["attributes"].get(PRECHECKS, 0) or 0) for r in roots
            ),
        },
    }


def rules(project: Path, last: int = 0) -> list[dict[str, Any]]:
    """How often each check rule turned a change back, most often first.

    Per rule: ``sent_back`` (items in failed submits the agent could fix),
    ``final`` (items in failures that dropped the change), ``prechecked`` (items
    a precheck found before any submit) and ``changes`` (changes it showed up in).
    Only the newest ``last`` changes count when ``last`` is above 0.
    """
    files = []
    for path in metrics_dir(project).glob("*.jsonl"):
        if path.is_symlink():
            continue
        spans = read(path)
        start = min((int(s["start_time_unix_nano"]) for s in spans), default=0)
        files.append((start, path.stem, spans))
    files.sort(key=lambda f: (f[0], f[1]))
    if last > 0:
        files = files[-last:]
    found: dict[str, dict[str, Any]] = {}
    for _start, _name, spans in files:
        seen: set[str] = set()
        for span in spans:
            attrs = span["attributes"]
            if attrs.get(KIND) != FINDINGS:
                continue
            by = str(attrs.get(FOUND_BY, ""))
            column = (
                "prechecked"
                if by == PRECHECK
                else "final"
                if attrs.get(FINAL) is True
                else "sent_back"
            )
            for rule in str(attrs.get(RULES, "")).split(","):
                rule = rule.strip()
                if not rule:
                    continue
                row = found.setdefault(
                    rule,
                    {
                        "rule": rule,
                        "sent_back": 0,
                        "final": 0,
                        "prechecked": 0,
                        "changes": 0,
                    },
                )
                row[column] += 1
                if rule not in seen:
                    seen.add(rule)
                    row["changes"] += 1
    return sorted(
        found.values(),
        key=lambda r: (-(r["sent_back"] + r["final"]), -r["prechecked"], r["rule"]),
    )


def render_rules(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No check has turned a change back in this project yet."
    width = max(len("rule"), *(len(r["rule"]) for r in rows))
    lines = [f"{'rule':<{width}}  sent back  dropped  prechecked  changes"]
    for row in rows:
        lines.append(
            f"{row['rule']:<{width}}  {row['sent_back']:>9}  {row['final']:>7}"
            f"  {row['prechecked']:>10}  {row['changes']:>7}"
        )
    lines.append("Counts are items: one finding of a rule in one check run.")
    return "\n".join(lines)


def changes(project: Path, last: int = 0) -> list[dict[str, Any]]:
    """Every recorded change's breakdown, oldest first (only the newest ``last``)."""
    found = []
    for path in metrics_dir(project).glob("*.jsonl"):
        if path.is_symlink():
            continue
        spans = read(path)
        if any(s["attributes"].get(KIND) == CHANGE for s in spans):
            found.append(summarise(path.stem, spans))
    found.sort(key=lambda c: (c["started_unix_nano"], c["id"]))
    return found[-last:] if last > 0 else found


def cost(project: Path, transaction_id: str) -> dict[str, Any] | None:
    """One transaction's total time and tokens, for its row in a history listing."""
    try:
        path = _file(project, transaction_id)
    except ValueError:
        return None
    if not path.is_file() or path.is_symlink():
        return None
    summary = summarise(transaction_id, read(path))
    if not summary["requests"]:
        return None
    tokens = summary["tokens"]
    return {
        "total_ms": summary["total_ms"],
        "tokens": None if tokens is None else tokens["input"] + tokens["output"],
        "rounds": summary["rounds"]["agent_turns"],
    }


# --- CLI ---------------------------------------------------------------------------


def _seconds(ms: float) -> str:
    return f"{ms / 1000:.1f}s"


def _tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def render(summaries: list[dict[str, Any]]) -> str:
    if not summaries:
        return "No change has been measured in this project yet."
    lines: list[str] = []
    for change in summaries:
        head = f"{change['id']}  {_seconds(change['total_ms'])}"
        state = change["decision"] or change["outcome"]
        if state:
            head += f"  {state}"
        if change["agent"]:
            head += f"  {change['agent']}"
            if change["model"]:
                head += f" ({change['model']})"
        lines.append(head)
        times = change["by_kind_ms"]
        ordered = [k for k in KINDS if k in times] + [
            k for k in times if k not in KINDS
        ]
        if ordered:
            lines.append(
                "  time:   "
                + ", ".join(
                    f"{k} {_seconds(times[k])}"
                    + (
                        f" ×{change['counts'][k]}"
                        if change["counts"].get(k, 0) > 1
                        else ""
                    )
                    for k in ordered
                )
            )
        tokens = change["tokens"]
        lines.append(
            "  tokens: "
            + (
                "not reported by this agent"
                if tokens is None
                else f"in {_tokens(tokens['input'])} (cached {_tokens(tokens['cached'])}),"
                f" out {_tokens(tokens['output'])}"
            )
        )
        light = [m for m in change.get("models", []) if m["tier"] == "light"]
        if light or change.get("review_routes"):
            parts = [
                f"{m['role']} {m['model'] or '?'} (light) ×{m['turns']}" for m in light
            ]
            parts += [f"review {k} ×{v}" for k, v in change["review_routes"].items()]
            if change.get("escalations"):
                parts.append(f"escalated ×{change['escalations']}")
            lines.append("  models: " + ", ".join(parts))
        rounds = change["rounds"]
        lines.append(
            f"  rounds: {rounds['agent_turns']} agent turns, {rounds['submits']} submits,"
            f" {rounds['send_backs']} send-backs"
        )
    lines.append("Span times overlap (an agent turn holds its tool calls).")
    return "\n".join(lines)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``metrics --project P [--last N] [--json]``."""
    parser = subparsers.add_parser(
        "metrics", help="show what each change cost: time by span, tokens, rounds"
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        help="newest N changes (0: all; default 10, all with --rules)",
    )
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument(
        "--rules",
        action="store_true",
        help="which check rules send changes back most often",
    )


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not project.is_dir():
        message = f"No project at {project}."
        print(json.dumps({"error": message}) if args.json else message)
        return 1
    if getattr(args, "rules", False):
        rows = rules(project, max(int(args.last or 0), 0))
        print(json.dumps({"rules": rows}) if args.json else render_rules(rows))
        return 0
    last = 10 if args.last is None else int(args.last)
    summaries = changes(project, max(last, 0))
    print(json.dumps({"changes": summaries}) if args.json else render(summaries))
    return 0
