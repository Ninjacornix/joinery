"""Match native Linux bind-mount ownership without granting workers host root."""

from __future__ import annotations

import os
import sys


def docker_owner_args() -> list[str]:
    """Docker Desktop maps ownership; native Linux needs the calling user ID.

    Home/config/cache stay inside each disposable container, never on the host.
    The entrypoint copies only the image's prepared tool settings into this home.
    """
    if sys.platform != "linux":
        return []
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise ValueError("Run Joinery workers as a non-root Linux user.")
    home = "/tmp/joinery-home"
    arguments = ["--user", f"{uid}:{gid}"]
    for setting in (
        f"HOME={home}",
        f"XDG_CONFIG_HOME={home}/.config",
        f"XDG_CACHE_HOME={home}/.cache",
        f"XDG_DATA_HOME={home}/.local/share",
        f"XDG_RUNTIME_DIR={home}/runtime",
        "JOINERY_EPHEMERAL_HOME=1",
    ):
        arguments += ["--env", setting]
    return arguments
