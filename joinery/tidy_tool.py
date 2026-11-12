"""The agents' ``tidy_check`` and ``tidy_remove`` (gateway RPC, every provider alike).

``tidy_check`` reads: what the game never uses, as the turn's staged copy has it when
there is one. Any role may call it. ``tidy_remove`` stages the removal of the chosen
findings in the turn's change, like any write: the checks, the reviewer and the
Review tab see it, and nothing in the project moves before the user accepts. The
read-only roles can't call it (``joinery.roles``); the gateway refuses it to them too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joinery import tidy, tidy_remove, tidy_report
from joinery.gateway import ProjectGateway
from joinery.tidy import human_size

#: Findings listed to the agent at most; the rest are counted.
LISTED = 40
NEXT = (
    "The removal is staged in your change. Call submit_for_review with one short "
    "line naming it; the user reviews what goes and the space it frees."
)


def _root(gateway: ProjectGateway) -> Path:
    tx = gateway.transaction
    if tx is not None and tx.state == "staging":
        return tx.stage
    return gateway.project


def summary(report: tidy.Report) -> dict[str, Any]:
    return {
        "summary": report.summary,
        "sure": report.count,
        "maybe": report.maybe,
        "frees": human_size(report.size),
        "findings": [
            {
                "id": f.id,
                "kind": f.kind,
                "title": f.title,
                "path": f.path,
                "subject": f.subject,
                "confidence": f.confidence,
                "size": human_size(f.size) if f.size else "",
                "why": f.why,
                "goes_with": [c.path for c in f.goes_with][:8],
            }
            for f in report.findings[:LISTED]
        ],
        "more": max(0, len(report.findings) - LISTED),
        "used_by_tests": report.used_by_tests[:20],
        "note": (
            "Read-only. 'maybe' findings are named by a path a script builds; never "
            "remove one without asking the user. Remove with tidy_remove(ids) only "
            "when the user asked for it."
        ),
    }


async def check(gateway: ProjectGateway, params: dict[str, Any]) -> dict[str, Any]:
    del params
    return summary(tidy_report.check(_root(gateway)))


async def remove(gateway: ProjectGateway, params: dict[str, Any]) -> dict[str, Any]:
    ids = params.get("ids") or []
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 200
        or any(not isinstance(i, str) or not 1 <= len(i) <= 40 for i in ids)
    ):
        raise ValueError("ids is a list of finding ids from tidy_check (1 to 200).")
    if gateway.turn is not None:
        gateway.turn.admit_mutation()
    async with gateway.call_lock:
        tx = gateway.staging()
        graph = tidy.build(tx.stage)
        report = tidy_report.check(tx.stage, graph)
        removal = tidy_remove.plan(graph, report, ids)
        if removal.problems:
            return {"status": "problems", "problems": removal.problems}
        tx.note_tool("joinery")
        tidy_remove.apply(tx.stage, removal)
        if gateway.claims.contended():
            gateway.claim_stage(strict=True)
    return {
        "status": "staged",
        "removed": [c.path for c in removal.delete],
        "edited": sorted(removal.edits),
        "frees": human_size(removal.size),
        "facts": [removal.summary()] + removal.lines[:3],
        "next": NEXT,
    }
