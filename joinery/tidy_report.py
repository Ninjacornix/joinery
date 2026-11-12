"""The tidy report: every finding of `joinery.tidy`, grouped for the card."""

from __future__ import annotations

from pathlib import Path

from joinery import tidy, tidy_caches, tidy_nodes, tidy_remove
from joinery.tidy import KINDS, Graph, Group, Report, human_size


def check(project: Path, graph: Graph | None = None) -> Report:
    """What the game never uses, with the evidence. Writes nothing."""
    project = project.resolve()
    graph = graph or tidy.build(project)
    keep = tidy.keep_list(project)
    final = tidy.final_list(project)
    prov = tidy_remove.Provenance.read(project)
    corpus = tidy_nodes.Corpus(graph)
    findings, held = tidy_remove.file_findings(graph, prov, keep, final)
    autoloads = set(tidy_nodes.autoload_targets(graph).values())
    for found, kept in (
        tidy_nodes.input_findings(graph, corpus, keep, final),
        tidy_nodes.autoload_findings(graph, corpus, keep, final),
        tidy_nodes.node_findings(graph, corpus, keep, final),
        tidy_nodes.script_findings(graph, keep, final, autoloads),
    ):
        findings += found
        held += kept
    order = list(KINDS)
    findings.sort(
        key=lambda f: (
            f.confidence != "unused",
            order.index(f.kind),
            -f.size,
            f.path,
            f.subject,
        )
    )
    report = Report(
        project=str(project),
        summary="",
        count=sum(1 for f in findings if f.confidence == "unused"),
        size=0,
        maybe=sum(1 for f in findings if f.confidence == "maybe"),
        findings=findings,
        kept=sorted(held),
        used_by_tests=sorted(r for r in graph.by_tests if tidy.candidate(graph, r)),
        read={
            "files": len(graph.files),
            "scenes": sum(1 for r in graph.files if tidy.suffix(r) == ".tscn"),
            "scripts": sum(1 for r in graph.files if tidy.suffix(r) in tidy.CODE),
            "reached": len(graph.used),
        },
    )
    for kind, label in KINDS.items():
        mine = [f for f in findings if f.kind == kind]
        if mine:
            sure = [f for f in mine if f.confidence == "unused"]
            report.groups.append(
                Group(
                    kind=kind,
                    label=label,
                    count=len(sure),
                    size=sum(f.size for f in sure),
                    maybe=len(mine) - len(sure),
                )
            )
    if report.count:
        report.size = tidy_remove.plan(graph, report).size
    report.caches = tidy_caches.caches(project)
    report.cache_size = sum(c.size for c in report.caches)
    report.summary = headline(report)
    return report


def headline(report: Report) -> str:
    """ "14 unused things · 38 MB", "Nothing unused", "3 maybe unused"."""
    if report.count:
        words = f"{report.count} unused thing{'s' if report.count != 1 else ''}"
        return words + (f" · {human_size(report.size)}" if report.size else "")
    if report.maybe:
        return f"Nothing surely unused · {report.maybe} maybe"
    return "Nothing unused"
