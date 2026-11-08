import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest

from joinery import api_profiles

_REAL_VERIFY = api_profiles._verify_model


@pytest.fixture(autouse=True)
def private_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "JOINERY_CONNECTIONS_FILE", str(tmp_path / "private/connections.json")
    )
    monkeypatch.setattr(api_profiles.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(api_profiles, "_verify_model", lambda *_: None)


def settings(**overrides: str) -> dict[str, str]:
    return {
        "driver": "codex",
        "endpoint": "https://example.invalid/v1",
        "model": "test-model",
        "key": "PRIVATE_TEST_KEY",
        **overrides,
    }


def test_configuration_round_trip_keeps_key_out_of_public_data() -> None:
    result = api_profiles.configure(settings())
    assert result["connected"] and result["profile"]
    assert "PRIVATE_TEST_KEY" not in json.dumps(result)
    assert "PRIVATE_TEST_KEY" not in json.dumps(api_profiles.list_public())
    assert api_profiles.list_public() == [result]
    path = api_profiles.store_path()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_private_storage_failure_preserves_previous_profile_and_writes_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_profiles.configure(settings())
    path = api_profiles.store_path()
    previous = path.read_bytes()

    def unavailable(temporary: Path) -> None:
        assert temporary.read_bytes() == b""
        raise ValueError("Fixture private storage unavailable")

    monkeypatch.setattr(api_profiles, "private_file", unavailable)
    with pytest.raises(ValueError, match="private storage unavailable"):
        api_profiles.configure(settings(model="another-model"))
    assert path.read_bytes() == previous
    assert list(path.parent.glob(".connections-*")) == []


def test_runtime_uses_existing_gated_driver_and_a_separate_secret_environment() -> None:
    profile = api_profiles.configure(settings())
    driver, args, env = api_profiles.runtime(profile["id"])
    assert driver == "codex"
    assert env["JOINERY_MODEL_API_KEY"] == "PRIVATE_TEST_KEY"
    assert "PRIVATE_TEST_KEY" not in " ".join(args)
    config = tomllib.loads(
        "\n".join(args[index + 1] for index, arg in enumerate(args) if arg == "-c")
    )
    assert config["model"] == "test-model"
    provider = config["model_providers"]["joinery"]
    assert provider["base_url"] == "https://example.invalid/v1"
    assert provider["env_key"] == "JOINERY_MODEL_API_KEY"
    assert provider["wire_api"] == "responses"


def test_opencode_model_configuration_references_secret_without_embedding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    profile = api_profiles.configure(settings(driver="opencode"))
    driver, args, env = api_profiles.runtime(profile["id"])
    assert driver == "opencode" and args == []
    assert "PRIVATE_TEST_KEY" not in env["OPENCODE_CONFIG_CONTENT"]
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert (
        config["provider"]["joinery"]["options"]["apiKey"]
        == "{env:JOINERY_MODEL_API_KEY}"
    )


def test_saved_key_can_be_kept_for_the_same_endpoint() -> None:
    old = api_profiles.configure(settings())
    new = api_profiles.configure(settings(id=old["id"], key="", model="another-model"))
    assert len(api_profiles.list_public()) == 1
    assert (
        api_profiles.runtime(new["id"])[2]["JOINERY_MODEL_API_KEY"]
        == "PRIVATE_TEST_KEY"
    )


def test_endpoint_change_does_not_reuse_a_saved_key() -> None:
    profile = api_profiles.configure(settings())
    before = api_profiles.store_path().read_bytes()
    with pytest.raises(ValueError, match="Changing the endpoint"):
        api_profiles.configure(
            settings(id=profile["id"], endpoint="https://other.invalid/v1", key="")
        )
    assert api_profiles.store_path().read_bytes() == before


@pytest.mark.parametrize(
    "endpoint",
    [
        "bad",
        "file:///tmp/x",
        "https://key@example.com",
        "https://example.com?key=PRIVATE",
        "http://[bad",
    ],
)
def test_bad_endpoints_do_not_create_a_profile(endpoint: str) -> None:
    with pytest.raises(ValueError):
        api_profiles.configure(settings(endpoint=endpoint))
    assert not api_profiles.store_path().exists()


def test_a_missing_driver_cannot_become_a_ready_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_profiles.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Install"):
        api_profiles.configure(settings())


def test_existing_corrupt_profiles_are_not_overwritten() -> None:
    path = api_profiles.store_path()
    path.parent.mkdir()
    path.write_text("bad data")
    with pytest.raises(ValueError, match="read saved"):
        api_profiles.configure(settings())
    assert path.read_text() == "bad data"


def test_local_provider_icons_are_imported_unchanged() -> None:
    folder = Path(__file__).parents[1] / "godot_addon/joinery/icons/providers"
    manifest = json.loads((folder / "sources.json").read_text())
    assert len(manifest) == 4
    for entry in manifest:
        assert (
            hashlib.sha256((folder / entry["file"]).read_bytes()).hexdigest()
            == entry["sha256"]
        )


def test_cli_configuration_reads_secrets_from_stdin_not_arguments(
    tmp_path: Path,
) -> None:
    # The helper needs only an installed driver for configuration; no model call occurs.
    executable = tmp_path / ("codex.py" if os.name == "nt" else "codex")
    executable.write_text("#!/usr/bin/env python3\npass\n")
    executable.chmod(0o700)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"])
    if os.name == "nt":
        env["PATHEXT"] = env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD") + ";.PY"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from joinery import api_profiles; api_profiles._verify_model=lambda *a:None; from joinery.cli import main; raise SystemExit(main())",
            "connect",
            "configure",
        ],
        input=json.dumps(settings()) + "\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PRIVATE_TEST_KEY" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["profile"]["model"] == "test-model"


def test_rejected_key_keeps_the_profile_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rejected(*args: str) -> None:
        raise ValueError("The provider rejected the API key.")

    monkeypatch.setattr(api_profiles, "_verify_model", rejected)
    with pytest.raises(ValueError, match="rejected"):
        api_profiles.configure(settings())
    assert not api_profiles.store_path().exists()


def test_model_verification_does_not_expose_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = io.BytesIO(b'{"data":[{"id":"test-model"}]}')
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(api_profiles, "build_opener", lambda *args: opener)
    _REAL_VERIFY("https://example.invalid/v1", "test-model", "PRIVATE")
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://example.invalid/v1/models"
    assert request.get_header("Authorization") == "Bearer PRIVATE"


def test_an_unavailable_model_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"data":[]}')
    monkeypatch.setattr(api_profiles, "build_opener", lambda *args: opener)
    with pytest.raises(ValueError, match="not available"):
        _REAL_VERIFY("https://example.invalid/v1", "missing", "PRIVATE")


def test_redirects_cannot_forward_the_authorization_header() -> None:
    assert (
        api_profiles._NoRedirect().redirect_request(
            None, None, 302, "", None, "https://other.invalid"
        )
        is None
    )


def test_windows_saved_opencode_profile_stays_authenticated_but_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(sys, "platform", "win32")
    profile = api_profiles.Profile(
        id="api_saved",
        driver="opencode",
        endpoint="https://example.invalid/v1",
        model="model",
        key=SecretStr("PRIVATE"),
        verified=True,
    )
    monkeypatch.setattr(api_profiles, "_read", lambda: [profile])
    result = api_profiles.list_public()[0]
    assert result["installed"] and result["authenticated"]
    assert result["supported"] is False and result["connected"] is False
    assert "Windows read-only host boundary" in result["reason"]
    assert "PRIVATE" not in json.dumps(result)
    with pytest.raises(ValueError, match="Windows read-only host boundary"):
        api_profiles.runtime(profile.id)


def test_windows_unsupported_profile_configuration_fails_before_io_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    for target, name in (
        (api_profiles, "_read"),
        (api_profiles, "_verify_model"),
        (api_profiles.shutil, "which"),
    ):
        monkeypatch.setattr(target, name, Mock(side_effect=AssertionError("No unsupported I/O")))
    with pytest.raises(ValueError, match="Windows read-only host boundary"):
        api_profiles.configure(settings(driver="opencode"))
    assert not api_profiles.store_path().exists()
