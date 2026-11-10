"""Krita: native .kra paintings (pixel art included) driven headless in the worker."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from joinery.tools.base import (
    CREATIVE_WORKER,
    ImageCheck,
    McpProbe,
    Run,
    ToolModule,
    WorkerExec,
)


class Krita(ToolModule):
    id = "krita"
    name = "Krita"
    family = "joinery-paint"
    suffix = ".kra"
    script_ext = ".py"
    authoring = True
    binary = "krita"
    #: The headless launcher every operation and replay runs.
    requires = ("joinery-krita",)
    desktop = "krita"
    ready_summary = "{name} {version} ready (runs offline in the creative worker)"
    needs_image = "Krita: the creative worker isn't built yet. Rebuild the worker."
    broken = "Krita: last check failed"
    checks = (
        ImageCheck(CREATIVE_WORKER),
        WorkerExec(
            CREATIVE_WORKER,
            (
                "sh",
                "-c",
                "command -v krita && command -v joinery-krita && dpkg-query -W -f='${Version}\\n' krita",
            ),
            expect=r"joinery-krita",
            version=r"(?:\d+:)?(\d+\.\d+\.\d+)",
            on_failure="needs_image",
            failure="Krita: the creative worker image is out of date; rebuild the worker",
        ),
        McpProbe(
            "joinery-paint",
            "document_capabilities",
            capability="krita",
            image=CREATIVE_WORKER,
            failure="Krita: the creative worker image is out of date; rebuild the worker",
        ),
    )

    def script(self, source: str, base: str, code: str, exports: list[str]) -> str:
        return "\n".join(
            [
                "from krita import Krita, InfoObject",
                "import os",
                "os.chdir('/work')",
                "app = Krita.instance()",
                f"document = app.openDocument(os.path.abspath({base!r}))"
                if base
                else "document = None",
                code,
                "assert document is not None, 'Create or open a native Krita document'",
                "document.setBatchmode(True)",
                "document.waitForDone()",
                f"assert document.saveAs(os.path.abspath({source!r}))",
                *[
                    f"assert document.exportImage(os.path.abspath({path!r}), InfoObject())"
                    for path in exports
                ],
                "document.setModified(False)",
                "document.close()",
                "",
            ]
        )

    def replay_argv(self, script: str) -> list[str]:
        return ["joinery-krita", script]

    async def inspect(
        self, project: Path, path: Path, source: str, run: Run
    ) -> dict[str, Any] | None:
        with zipfile.ZipFile(path) as archive:
            document = ElementTree.fromstring(archive.read("maindoc.xml"))
        return {
            "layers": [
                dict(node.attrib)
                for node in document.iter()
                if node.tag.split("}")[-1].lower() == "layer"
            ]
        }


MODULE = Krita()
