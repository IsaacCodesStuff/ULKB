"""
download.py — HTTP(S) downloads with a progress bar, timeouts, retries and resume.

Used for toolchain archives and kernel tarballs. A download that stalls fails
after `timeout` seconds of silence and is retried from where it stopped, so a
flaky connection no longer looks like a frozen program.
"""

import http.client
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

CHUNK = 64 * 1024

# Errors worth retrying: the network dropped, stalled or cut the transfer short.
_RETRYABLE = (
    urllib.error.URLError,
    socket.timeout,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,   # includes IncompleteRead (connection closed early)
)


class DownloadError(RuntimeError):
    """The download failed for good (bad URL, or out of retries)."""


def download_file(
    url: str,
    dest,
    console: Console,
    *,
    label: str = None,
    timeout: float = 30.0,
    retries: int = 5,
) -> Path:
    """Download `url` to `dest`, showing progress. Resumes partial downloads.

    The data is written to `<dest>.part` and renamed only when complete, so an
    interrupted run never leaves something that looks like a finished file.

    Args:
        timeout: Seconds without receiving any data before an attempt is abandoned.
        retries: Total attempts before giving up.

    Raises:
        DownloadError: On a permanent error (404 and friends) or when retries run out.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    label = label or dest.name

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Downloading {label}", total=None)

        for attempt in range(1, retries + 1):
            try:
                _attempt(url, part, progress, task, timeout)
                part.replace(dest)
                return dest
            except urllib.error.HTTPError as e:
                if e.code == 416:            # our partial file is unusable: start over
                    part.unlink(missing_ok=True)
                elif 400 <= e.code < 500:
                    raise DownloadError(f"{url}\n  server answered HTTP {e.code} {e.reason}") from e
                last = f"HTTP {e.code}"
            except _RETRYABLE as e:
                last = str(e) or type(e).__name__
            except OSError as e:             # local disk problems are not worth retrying
                raise DownloadError(f"could not write {part}: {e}") from e

            if attempt == retries:
                raise DownloadError(
                    f"{url}\n  gave up after {retries} attempts (last error: {last})"
                )
            wait = min(2 ** attempt, 15)
            have = part.stat().st_size if part.exists() else 0
            progress.console.print(
                f"  [yellow]⚠[/yellow] {escape(last)} — retrying in {wait}s "
                f"([dim]attempt {attempt + 1}/{retries}, resuming at {have:,} bytes[/dim])"
            )
            time.sleep(wait)

    raise DownloadError("unreachable")       # pragma: no cover


def _attempt(url: str, part: Path, progress: Progress, task, timeout: float):
    have = part.stat().st_size if part.exists() else 0

    request = urllib.request.Request(url, headers={"User-Agent": "ulkb"})
    if have:
        request.add_header("Range", f"bytes={have}-")

    # `timeout` applies to every socket read, so a stalled connection raises
    # instead of hanging forever.
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        resuming = have > 0 and getattr(resp, "status", None) == 206
        if not resuming:
            have = 0                          # server ignored Range: start from byte 0

        length = resp.headers.get("Content-Length")
        total = (have + int(length)) if length is not None else None
        progress.update(task, total=total, completed=have)

        with open(part, "ab" if resuming else "wb") as f:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                progress.update(task, advance=len(chunk))

    if total is not None and part.stat().st_size != total:
        raise ConnectionError("connection closed before the download finished")
