"""Per-install API token (defence in depth for a server on 127.0.0.1).

Any program on the computer can reach 127.0.0.1, and a web page can try to. The Host/Origin checks stop
browsers on other sites; the token additionally stops anything that has not read it from disk or been given it
by the app. The browser UI receives it as an HttpOnly, SameSite=Strict cookie when it loads the page; scripts
send it as the `X-Clipper-Token` header, read from `CLIPPER_DATA_DIR/api_token`.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path

FILE = "api_token"
HEADER = "x-clipper-token"
COOKIE = "clipper_token"


def load_or_create(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / FILE
    if p.exists():
        tok = p.read_text(encoding="ascii").strip()
        if len(tok) >= 32:
            return tok
    tok = secrets.token_urlsafe(32)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(tok)
    os.replace(tmp, p)
    with contextlib.suppress(OSError):
        os.chmod(p, 0o600)  # no-op on Windows; the file sits in the user's own data folder
    return tok
