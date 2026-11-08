"""Real model -> MCP proxy -> staged Godot files -> validation -> rejection."""

import asyncio
import tempfile
from pathlib import Path

from bridge.codex_session import CodexSession
from bridge.protocol import Done, Message, MessageDelta, Prompt
from joinery.gateway import ProjectGateway
from joinery.gateway_rpc import GatewayRPC
from joinery.wiring import write_agent_wiring


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="joinery-agent-gateway-") as directory:
        project = Path(directory)
        (project / "project.godot").write_text(
            'config_version=5\n[application]\nconfig/name="Gateway fixture"\n'
        )
        (project / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
        write_agent_wiring(project)
        gateway = ProjectGateway(project)
        gateway.request_summary = "Create a saved scene fixture"
        rpc = GatewayRPC(gateway)
        await rpc.start()
        session = CodexSession()
        session.gateway_socket = rpc.path
        try:
            await session.connect(project)
            done = False
            prompt = Prompt(
                project_dir=str(project),
                text="Use the connected joinery-registry MCP gateway. Discover the saved-file Godot tools and their schemas, then create gateway_fixture.tscn with a Node3D root and a Marker3D child named Marker. No live editor is attached in this test, so use the saved-file MCP tools. Leave all changes staged for review; do not give manual instructions.",
            )
            async for event in session.send(prompt):
                if isinstance(event, (Message, MessageDelta)):
                    print(event.text, end="", flush=True)
                if isinstance(event, Done):
                    done = event.ok
            assert done
            assert not (project / "gateway_fixture.tscn").exists()
            preview = await gateway.finish()
            assert preview, "The model must actually operate the gateway"
            assert (
                gateway.transaction
                and (gateway.transaction.stage / "gateway_fixture.tscn").is_file()
            )
            text = (gateway.transaction.stage / "gateway_fixture.tscn").read_text()
            assert 'name="Marker"' in text and 'type="Marker3D"' in text
            await gateway.decide(preview["transaction_id"], False)
            assert not (project / "gateway_fixture.tscn").exists()
            print("\nActual Codex -> MCP -> staged Godot -> validate -> reject: PASS")
        finally:
            await session.close()
            await rpc.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 180))
