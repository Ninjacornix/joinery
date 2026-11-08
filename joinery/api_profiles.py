"""Explicit API connections, stored privately outside projects and subprocess argv."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from joinery.platform_support import private_file
from joinery.provider_support import require_supported, usability


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    driver: Literal["codex", "opencode"]
    endpoint: str
    model: str
    key: SecretStr
    verified: bool = False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _verify_model(endpoint: str, model: str, key: str) -> None:
    request = Request(
        endpoint + "/models",
        headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
        },
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=10) as response:
            data = json.loads(response.read(2_000_000))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError(
                "The provider rejected the API key or its permissions."
            ) from None
        raise ValueError(
            "The endpoint must expose its model list at /models without redirects."
        ) from None
    except (OSError, ValueError, URLError):
        raise ValueError("Could not verify the model at this endpoint.") from None
    models = data.get("data", []) if isinstance(data, dict) else []
    if not isinstance(models, list) or not any(
        isinstance(entry, dict) and entry.get("id") == model for entry in models
    ):
        raise ValueError("The selected model is not available for this API key.")


def store_path() -> Path:
    override = os.environ.get("JOINERY_CONNECTIONS_FILE")
    return (
        Path(override) if override else Path.home() / ".config/joinery/connections.json"
    )


def _read() -> list[Profile]:
    path = store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            raise TypeError
        return [Profile.model_validate(entry) for entry in data]
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("Could not read saved API connections.") from exc


def public(profile: Profile) -> dict[str, Any]:
    support = usability(profile.driver)
    installed = shutil.which(profile.driver) is not None
    authenticated = profile.verified and bool(profile.key.get_secret_value())
    return {
        "id": profile.id,
        "name": profile.model,
        "driver": profile.driver,
        "endpoint": profile.endpoint,
        "model": profile.model,
        "profile": True,
        "installed": installed,
        "connected": installed and authenticated and support["supported"],
        "authenticated": authenticated,
        **support,
        "method": "API: " + profile.model,
        "requires_key_confirmation": False,
    }


def list_public() -> list[dict[str, Any]]:
    return [public(profile) for profile in _read()]


def configure(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TypeError("Enter an endpoint, model and API key.")
    if any(
        not isinstance(data.get(name, ""), str)
        for name in ("driver", "endpoint", "model", "key")
    ):
        raise TypeError("Connection settings must be text fields.")
    driver = data.get("driver", "codex")
    endpoint = str(data.get("endpoint", "")).strip().rstrip("/")
    model = str(data.get("model", "")).strip()
    key = str(data.get("key", "")).strip()
    if driver not in ("codex", "opencode"):
        raise ValueError("Choose Codex or opencode to run this connection.")
    require_supported(driver)
    if shutil.which(driver) is None:
        raise ValueError("Install the selected agent before connecting this model.")
    try:
        url = urlsplit(endpoint)
        valid = url.scheme in ("https", "http") and bool(url.hostname)
        valid = valid and not (
            url.username or url.password or url.query or url.fragment
        )
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Enter an HTTP(S) API base URL without embedded credentials or query parameters."
        )
    if not model or any(ord(char) < 32 for char in model):
        raise ValueError("Enter the provider's model ID.")
    profiles = _read()
    existing = next((p for p in profiles if p.id == data.get("id")), None)
    if (
        not key
        and existing
        and existing.endpoint == endpoint
        and existing.driver == driver
    ):
        key = existing.key.get_secret_value()
    if not key or any(ord(char) < 32 for char in key):
        raise ValueError(
            "Enter an API key. Changing the endpoint requires entering its key again."
        )
    _verify_model(endpoint, model, key)
    identity = (
        "api_"
        + hashlib.sha256(f"{driver}\n{endpoint}\n{model}".encode()).hexdigest()[:16]
    )
    try:
        profile = Profile(
            id=identity,
            driver=driver,
            endpoint=endpoint,
            model=model,
            key=SecretStr(key),
            verified=True,
        )
    except ValidationError:
        raise ValueError("Could not save these connection settings.") from None
    updated = [
        p for p in profiles if p.id not in {profile.id, existing.id if existing else ""}
    ]
    updated.append(profile)
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, filename = tempfile.mkstemp(prefix=".connections-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            private_file(Path(filename))
            json.dump(
                [
                    {**p.model_dump(exclude={"key"}), "key": p.key.get_secret_value()}
                    for p in updated
                ],
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(filename, 0o600)
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)
    return public(profile)


def runtime(profile_id: str) -> tuple[str, list[str], dict[str, str]]:
    """Launch settings for the existing gated agent; no new ungated agent type."""
    profile = next((p for p in _read() if p.id == profile_id), None)
    if profile is None:
        raise ValueError("API connection not found.")
    require_supported(profile.driver)
    if not profile.verified:
        raise ValueError("Verify this API connection before using it.")
    env = {"JOINERY_MODEL_API_KEY": profile.key.get_secret_value()}
    if profile.driver == "codex":
        provider = (
            '{name="Joinery API",base_url='
            + json.dumps(profile.endpoint)
            + ',env_key="JOINERY_MODEL_API_KEY",wire_api="responses",requires_openai_auth=false}'
        )
        args = [
            "-c",
            'model_provider="joinery"',
            "-c",
            "model=" + json.dumps(profile.model),
            "-c",
            "model_providers.joinery=" + provider,
        ]
    else:
        config = {
            "model": "joinery/" + profile.model,
            "provider": {
                "joinery": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Joinery API",
                    "options": {
                        "baseURL": profile.endpoint,
                        "apiKey": "{env:JOINERY_MODEL_API_KEY}",
                    },
                    "models": {profile.model: {"name": profile.model}},
                }
            },
        }
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        args = []
    return profile.driver, args, env
