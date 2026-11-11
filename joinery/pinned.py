"""Pinned third-party downloads: https only, denylist-checked, size-capped, hash-verified.

The shared mechanics behind the export templates and butler (the GodotSteam
model, docs/adr/0027-godotsteam.md). A caller downloads only after the user's
explicit action; these helpers make sure that what lands on disk is exactly
the pinned file, or nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import IO
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from joinery.policy import load_denylist, url_violation

#: `urlopen`-shaped: takes a URL and a timeout, returns a readable response.
Opener = Callable[..., IO[bytes]]
CHUNK = 1 << 20
TIMEOUT = 60


class PinnedDownloadError(Exception):
    """A plain-language reason a pinned download was refused or failed."""


def digest(path: Path, algorithm: str = "sha256") -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_url(url: str, what: str) -> None:
    if urlparse(url).scheme != "https":
        raise PinnedDownloadError(f"{what} is only downloaded over https.")
    verdict = url_violation(url, load_denylist())
    if verdict is not None:
        raise PinnedDownloadError(f"{what} was not downloaded: {verdict}.")


def checked_opener(what: str) -> Opener:
    """`urlopen`, but every redirect is held to the same https and denylist rules."""

    class Redirects(HTTPRedirectHandler):
        def redirect_request(
            self,
            req: Request,
            fp: IO[bytes],
            code: int,
            msg: str,
            headers: object,
            newurl: str,
        ) -> Request | None:
            check_url(newurl, what)
            return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]

    def open_url(url: str, timeout: float) -> IO[bytes]:
        response: IO[bytes] = build_opener(Redirects()).open(url, timeout=timeout)
        return response

    return open_url


def download(
    url: str,
    destination: Path,
    *,
    what: str,
    size: int,
    expected: str,
    algorithm: str = "sha256",
    opener: Opener | None = None,
) -> Path:
    """Download `url` to `destination`, keeping it only if its hash is `expected`.

    No more than `size` bytes are read (a larger file is not the pinned one);
    a partial or mismatched file is deleted and nothing is left behind.
    """
    check_url(url, what)
    open_url = opener or checked_opener(what)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        with open_url(url, timeout=TIMEOUT) as response, partial.open("wb") as out:
            copied = 0
            while chunk := response.read(CHUNK):
                copied += len(chunk)
                if copied > size:
                    raise PinnedDownloadError(
                        f"The {what} download was larger than the pinned release, so it "
                        "was deleted."
                    )
                out.write(chunk)
    except PinnedDownloadError:
        partial.unlink(missing_ok=True)
        raise
    except (URLError, OSError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        raise PinnedDownloadError(
            f"Couldn't download {what}. Check the connection and try again."
        ) from exc
    if digest(partial, algorithm) != expected:
        partial.unlink()
        raise PinnedDownloadError(
            f"The {what} download did not match its recorded hash, so it was deleted."
        )
    partial.replace(destination)
    return destination
