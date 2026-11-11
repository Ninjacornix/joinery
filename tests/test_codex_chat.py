"""Protocol integration without network, credentials, or model inference."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from bridge.codex_session import CodexSession, require_gate
from bridge.protocol import Done, MessageDelta, Prompt
from joinery.catalog import ROOT
from joinery.wiring import _command_str


@pytest.fixture
def game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    game = tmp_path / "game"
    (game / ".codex").mkdir(parents=True)
    (game / "project.godot").write_text('[application]\nconfig/name="Test"\n')
    (game / "joinery.toml").write_text('[tools]\nenabled=["godot"]\n')
    command = _command_str(str(ROOT), "codex")
    (game / ".codex/hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "mcp__.*|Bash", "hooks": [{"command": command}]}
                    ]
                }
            }
        )
    )
    executable = tmp_path / ("codex.py" if os.name == "nt" else "codex")
    monkeypatch.setenv("PYTHONUTF8", "1")
    if os.name == "nt":
        monkeypatch.setenv("PATHEXT", os.environ.get("PATHEXT", ".EXE;.CMD") + ";.PY")
    executable.write_text("""#!/usr/bin/env python3
import json,sys,os
from pathlib import Path
command = next(a for a in sys.argv if a.startswith('hooks.PreToolUse='))
# Only the command's quoted value, whose path can contain spaces.
import re
command = json.loads(re.search(r'command=("(?:[^"\\\\]|\\\\.)*")',command)[1])
trusted = Path('hook_trusted').exists()
Path('argv.json').write_text(json.dumps(sys.argv))
turn = 0
for line in sys.stdin:
 r=json.loads(line)
 with open('requests.jsonl','a') as f:f.write(json.dumps(r)+'\\n')
 if r.get('id')=='edit-call' and 'result' in r:
  print(json.dumps({'method':'item/agentMessage/delta','params':{'threadId':'thread1','turnId':str(turn),'itemId':'edit-result','delta':'Box preview ready' if r['result']['success'] else 'Edit refused'}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'thread1','turn':{'id':str(turn),'status':'completed'}}}),flush=True)
  continue
 method=r.get('method')
 if 'id' not in r or 'error' in r:continue
 result={}
 if method=='hooks/list':
  hooks=[{'eventName':'preToolUse','matcher':'mcp__.*|Bash','command':command,
   'enabled':True,'key':'test-gate','currentHash':'sha256:known','trustStatus':'trusted' if trusted else 'untrusted'}]
  if Path('extra_hook').exists():hooks.append({'enabled':True,'command':'unknown','trustStatus':'untrusted'})
  result={'data':[{'hooks':hooks,'errors':[]}]}
 elif method=='config/value/write':Path('hook_trusted').touch()
 elif method=='account/read':result={'account':None if Path('signed_out').exists() else {'type':'chatgpt'}}
 elif method=='thread/start':result={'thread':{'id':'thread1'}}
 elif method=='thread/resume':
  if Path('resume_fails').exists():
   print(json.dumps({'id':r['id'],'error':{'message':'no rollout found for thread id'}}),flush=True)
   continue
  result={'thread':{'id':r['params']['threadId']}}
 elif method=='turn/start':
  turn+=1
  result={'turn':{'id':str(turn)}}
 elif method=='turn/interrupt':result={}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
 if method=='turn/start':
  text=r['params']['input'][0]['text']
  if text.startswith('edit'):
   print(json.dumps({'id':'edit-call','method':'item/tool/call','params':{'tool':'joinery_edit_scene','threadId':'thread1','turnId':str(turn),'callId':'box-call','arguments':{'summary':'Create a one metre box','operations':[{'operation':'create_box','name':'Box','size':[1,1,1]}]}}}),flush=True)
   continue
  if text.startswith('wait'):continue
  if text.startswith('crash'):sys.exit(2)
  p={'threadId':'thread1','turnId':str(turn),'itemId':'message'+str(turn)}
  print(json.dumps({'id':'approval','method':'item/commandExecution/requestApproval','params':{}}),flush=True)
  if text.startswith('fallback'):
   print(json.dumps({'method':'item/completed','params':dict(p,item={'type':'agentMessage','id':p['itemId'],'text':'Fallback'})}),flush=True)
  else:
   for delta in ['Hello ','世界']:
    print(json.dumps({'method':'item/agentMessage/delta','params':dict(p,delta=delta)},ensure_ascii=False),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'thread1','turn':{'id':str(turn),'status':'completed'}}}),flush=True)
""")
    executable.chmod(0o755)
    monkeypatch.setattr("bridge.codex_session.shutil.which", lambda _: str(executable))
    return game


def test_submit_for_review_runs_without_an_approval_prompt(game: Path) -> None:
    async def run() -> None:
        session = CodexSession()
        session.gateway_socket = game / "gateway.sock"
        try:
            await session.connect(game)
        finally:
            await session.close()

    asyncio.run(run())
    argv = json.loads((game / "argv.json").read_text())
    assert (
        'mcp_servers.joinery-registry.tools.submit_for_review.approval_mode="approve"'
        in argv
    )
    # The reviewer's and debugger's reports are approved too; the hook limits who calls them.
    for tool in (
        "report_review",
        "report_diagnosis",
        "reproduce_error",
        "report_design",
    ):
        assert (
            f'mcp_servers.joinery-registry.tools.{tool}.approval_mode="approve"' in argv
        )


def test_missing_gate_refuses_launch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing"):
        require_gate(tmp_path)


def test_persistent_stream_and_selection(game: Path) -> None:
    async def run() -> None:
        session = CodexSession()
        try:
            await session.connect(game)
            for text, expected in [("hello", "Hello 世界"), ("fallback", "Fallback")]:
                events = [
                    e
                    async for e in session.send(
                        Prompt(
                            text=text,
                            project_dir=str(game),
                            targets=("res://Door",),
                            target_context=({"id": "res://Door", "label": "Door"},),
                        )
                    )
                ]
                assert (
                    "".join(e.text for e in events if isinstance(e, MessageDelta))
                    == expected
                )
                assert events[-1] == Done(ok=True)
                await asyncio.sleep(0.02)
            requests = [
                json.loads(line)
                for line in (game / "requests.jsonl").read_text().splitlines()
            ]
            assert len([r for r in requests if r.get("method") == "thread/start"]) == 1
            turns = [r for r in requests if r.get("method") == "turn/start"]
            assert all(r["params"]["threadId"] == "thread1" for r in turns)
            assert "Door" in turns[0]["params"]["input"][0]["text"]
            assert any(r.get("id") == "approval" and "error" in r for r in requests)
            process = session.process
        finally:
            await session.close()
        assert process is not None and process.returncode is not None

    asyncio.run(run())


def test_instructions_read_the_game_brief(game: Path) -> None:
    async def run() -> None:
        session = CodexSession()
        try:
            await session.connect(game)
        finally:
            await session.close()
        requests = [
            json.loads(line)
            for line in (game / "requests.jsonl").read_text().splitlines()
        ]
        start = next(r for r in requests if r.get("method") == "thread/start")
        text = start["params"]["developerInstructions"]
        assert "Read game_brief and game_setup with art_direction" in text
        assert "game_setup.extension_points" in text
        assert "suggest /feature add, never apply it" in text
        assert "Approve these rules" in text
        assert "Reject" not in text

    asyncio.run(run())


def test_unreviewed_extra_hook_is_not_trusted(game: Path) -> None:
    (game / "extra_hook").touch()

    async def run() -> None:
        session = CodexSession()
        with pytest.raises(ValueError, match="Review the other"):
            await session.connect(game)
        assert session.process is None
        requests = (game / "requests.jsonl").read_text()
        assert "thread/start" not in requests

    asyncio.run(run())


def test_signed_out_cannot_claim_connected(game: Path) -> None:
    (game / "signed_out").touch()

    async def run() -> None:
        session = CodexSession()
        with pytest.raises(ValueError, match="not signed in"):
            await session.connect(game)
        assert session.process is None

    asyncio.run(run())


def test_cancel_and_disconnect(game: Path) -> None:
    async def run() -> None:
        session = CodexSession()
        await session.connect(game)

        async def waiting() -> None:
            async for _ in session.send(Prompt(text="wait", project_dir=str(game))):
                pass

        task = asyncio.create_task(waiting())
        for _ in range(100):
            if session.turn_id:
                break
            await asyncio.sleep(0.01)
        await session.cancel()
        assert "turn/interrupt" in (game / "requests.jsonl").read_text()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(ConnectionError):
            _ = [
                e
                async for e in session.send(Prompt(text="crash", project_dir=str(game)))
            ]
        await session.close()

    asyncio.run(run())


def test_api_errors_redact_credentials() -> None:
    session = CodexSession()
    session._secrets = ["custom-secret"]
    assert session.safe_error("custom-secret sk-fakeToken") == "[redacted] [redacted]"


def test_cli_stdio_conversation_and_eof_cleanup(
    game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import sys

    monkeypatch.setenv("PATH", str(game.parent) + os.pathsep + os.environ["PATH"])

    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from joinery.cli import main; raise SystemExit(main())",
            "chat",
            "--addon-protocol",
            "2",
            "--project",
            str(game),
            "--agent",
            "codex",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stdin is not None

        async def read() -> dict:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=20)
            except TimeoutError:
                process.kill()
                err = await process.stderr.read() if process.stderr else b""
                raise AssertionError(
                    "the chat host stopped answering: "
                    + err.decode(errors="replace")[-2000:]
                ) from None
            if not line:
                err = await process.stderr.read() if process.stderr else b""
                # The exit code tells a crash (e.g. 0xC0000005) from a clean exit.
                code = await asyncio.wait_for(process.wait(), timeout=5)
                raise AssertionError(
                    f"the chat host exited with {code:#x}: "
                    + err.decode(errors="replace")[-2000:]
                )
            return json.loads(line)

        try:
            assert (await read())["state"] == "connected"
            # Then the brief's feature offers (none: this project has no brief).
            assert await read() == {"kind": "suggestions", "items": []}
            process.stdin.write(b"{invalid json}\n")
            await process.stdin.drain()
            assert (await read())["kind"] == "message"
            process.stdin.write(
                json.dumps(
                    {
                        "kind": "prompt",
                        "text": "hello",
                        "targets": [{"id": "res://Door"}],
                    }
                ).encode()
                + b"\n"
            )
            await process.stdin.drain()
            events = []
            while True:
                event = await read()
                events.append(event)
                if event["kind"] == "done":
                    break
            assert (
                "".join(e["text"] for e in events if e["kind"] == "message_delta")
                == "Hello 世界"
            )
            process.stdin.write(b'{"kind":"prompt","text":"wait"}\n')
            await process.stdin.drain()
            # Ensure this reaches turn/start rather than cancelling the previous completed turn.
            for _ in range(100):
                if (game / "requests.jsonl").read_text().count(
                    '"method": "turn/start"'
                ) == 2:
                    break
                await asyncio.sleep(0.01)
            process.stdin.write(b'{"kind":"cancel"}\n')
            await process.stdin.drain()
            cancelled = await read()
            while cancelled["kind"] in {
                "progress",
                "timing",
                "execution",
                "turn_state",
            }:
                cancelled = await read()
            assert cancelled == {"kind": "done", "ok": False}
            process.stdin.close()
            assert await asyncio.wait_for(process.wait(), timeout=5) == 0
            assert not (await process.stderr.read())
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(run())


def test_editor_tool_round_trip(game: Path) -> None:
    from bridge.protocol import EditorAction

    async def run() -> None:
        session = CodexSession()
        await session.connect(game)
        try:
            events = []
            async for event in session.send(
                Prompt(
                    text="edit",
                    project_dir=str(game),
                    editor_context={
                        "scene_instance": "42",
                        "scene_path": "res://main.tscn",
                    },
                )
            ):
                events.append(event)
                if isinstance(event, EditorAction):
                    assert event.payload["operations"][0]["size"] == [1.0, 1.0, 1.0]
                    assert event.context["scene_instance"] == "42"
                    session.editor_result(
                        event.request_id, {"success": True, "status": "preview"}
                    )
            assert any(isinstance(e, EditorAction) for e in events)
            assert any(
                isinstance(e, MessageDelta) and "preview ready" in e.text
                for e in events
            )
        finally:
            await session.close()

    asyncio.run(run())


def test_editor_tool_missing_policy_returns_error(game: Path) -> None:
    from bridge.protocol import EditorAction

    (game / "joinery.toml").unlink()

    async def run() -> None:
        session = CodexSession()
        await session.connect(game)
        try:
            events = [
                e
                async for e in session.send(
                    Prompt(
                        text="edit",
                        project_dir=str(game),
                        editor_context={"scene_instance": "42"},
                    )
                )
            ]
            assert not any(isinstance(e, EditorAction) for e in events)
            assert any(
                isinstance(e, MessageDelta) and e.text == "Edit refused" for e in events
            )
        finally:
            await session.close()

    asyncio.run(asyncio.wait_for(run(), timeout=5))


def test_attached_images_go_in_as_local_images(game: Path) -> None:
    folder = game / ".joinery/attachments"
    folder.mkdir(parents=True)
    image = folder / "view.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    async def run() -> None:
        session = CodexSession()
        await session.connect(game)
        try:
            prompt = Prompt(
                text="hello", project_dir=str(game), attachments=(str(image),)
            )
            _ = [e async for e in session.send(prompt)]
        finally:
            await session.close()
        requests = [
            json.loads(line)
            for line in (game / "requests.jsonl").read_text().splitlines()
        ]
        turn = next(r for r in requests if r.get("method") == "turn/start")
        assert turn["params"]["input"][1:] == [
            {"type": "localImage", "path": str(image)}
        ]

    asyncio.run(run())


def test_a_saved_thread_resumes_and_a_new_chat_starts_a_thread(game: Path) -> None:
    async def run() -> None:
        session = CodexSession()
        try:
            await session.connect(game)
            assert session.handle == "thread1"
            assert await session.resume("thread-saved")
            assert session.handle == "thread-saved"
            prompt = Prompt(text="hello", project_dir=str(game))
            events = [e async for e in session.send(prompt)]
            assert events[-1] == Done(ok=True)
            (game / "resume_fails").touch()
            assert not await session.resume("thread-gone")
            assert session.handle == "thread-saved"
            await session.new_session()
            assert session.handle == "thread1"
            requests = [
                json.loads(line)
                for line in (game / "requests.jsonl").read_text().splitlines()
            ]
            resumes = [r for r in requests if r.get("method") == "thread/resume"]
            assert resumes[0]["params"]["threadId"] == "thread-saved"
            # The resumed thread runs exactly like a new one: same folder, sandbox
            # and instructions.
            start = next(r for r in requests if r.get("method") == "thread/start")
            for key in ("cwd", "sandbox", "approvalPolicy", "developerInstructions"):
                assert resumes[0]["params"][key] == start["params"][key]
            assert start["params"]["dynamicTools"][0]["name"] == "joinery_edit_scene"
            turn = next(r for r in requests if r.get("method") == "turn/start")
            assert turn["params"]["threadId"] == "thread-saved"
            assert len([r for r in requests if r.get("method") == "thread/start"]) == 2
        finally:
            await session.close()

    asyncio.run(run())
