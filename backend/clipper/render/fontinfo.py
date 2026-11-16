"""Bundled caption fonts (SIL OFL 1.1, licences next to the files) and the few metrics needed to make the
browser preview match libass.

libass sizes a font so that winAscent + winDescent (OS/2 table) equals the ASS `Fontsize` in pixels
(FreeType "real dimension" request). Browsers size by the em square. So the CSS font-size that draws the same
glyphs is  ass_size * unitsPerEm / (winAscent + winDescent). tests/test_render.py checks this against
real renders.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import cache
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"


@dataclass(frozen=True)
class FontInfo:
    file: str
    family: str  # name ID 1 (legacy family) - what ASS `Fontname` must use
    units_per_em: int
    win_ascent: int
    win_descent: int
    cap_height: int
    typo_ascent: int  # OS/2 sTypo*, what browsers use for these fonts (USE_TYPO_METRICS; equal to hhea)
    typo_descent: int  # positive
    bold: bool  # the file is the Bold face of its family (ASS needs Bold=-1 to pick it)

    @property
    def css_scale(self) -> float:
        """CSS font-size = ASS Fontsize * css_scale."""
        return self.units_per_em / (self.win_ascent + self.win_descent)


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    (num,) = struct.unpack_from(">H", data, 4)
    out = {}
    for i in range(num):
        tag, _cs, off, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
        out[tag] = (off, length)
    return out


def _name(data: bytes, off: int, name_id: int) -> str | None:
    _fmt, count, str_off = struct.unpack_from(">HHH", data, off)
    for i in range(count):
        pid, eid, _lang, nid, length, o = struct.unpack_from(">HHHHHH", data, off + 6 + 12 * i)
        if nid == name_id and pid == 3 and eid in (0, 1):  # Windows, UTF-16BE
            start = off + str_off + o
            return data[start : start + length].decode("utf-16-be")
    return None


def _cmap(data: bytes, off: int) -> dict[int, int]:
    """Unicode -> glyph id from a format 4 (BMP) or 12 (full) subtable."""
    (_v, n) = struct.unpack_from(">HH", data, off)
    subs = {}
    for i in range(n):
        pid, eid, o = struct.unpack_from(">HHI", data, off + 4 + 8 * i)
        subs[(pid, eid)] = off + o
    out: dict[int, int] = {}
    for key in ((3, 10), (0, 4), (3, 1), (0, 3)):
        if key not in subs:
            continue
        so = subs[key]
        (fmt,) = struct.unpack_from(">H", data, so)
        if fmt == 12:
            (ngroups,) = struct.unpack_from(">I", data, so + 12)
            for g in range(ngroups):
                a, b, gid = struct.unpack_from(">III", data, so + 16 + 12 * g)
                for c in range(a, b + 1):
                    out[c] = gid + c - a
            return out
        if fmt == 4:
            (segx2,) = struct.unpack_from(">H", data, so + 6)
            seg = segx2 // 2
            ends = struct.unpack_from(f">{seg}H", data, so + 14)
            starts = struct.unpack_from(f">{seg}H", data, so + 16 + segx2)
            deltas = struct.unpack_from(f">{seg}h", data, so + 16 + 2 * segx2)
            ro_off = so + 16 + 3 * segx2
            ranges = struct.unpack_from(f">{seg}H", data, ro_off)
            for k in range(seg):
                for c in range(starts[k], ends[k] + 1):
                    if c == 0xFFFF:
                        continue
                    if ranges[k] == 0:
                        gid = (c + deltas[k]) & 0xFFFF
                    else:
                        p = ro_off + 2 * k + ranges[k] + 2 * (c - starts[k])
                        (gid,) = struct.unpack_from(">H", data, p)
                        if gid:
                            gid = (gid + deltas[k]) & 0xFFFF
                    if gid:
                        out[c] = gid
            return out
    return out


@dataclass(frozen=True)
class Metrics:
    """Horizontal advances, for line fitting. Ignores kerning (a few % at most for caption text)."""

    units_per_em: int
    advances: dict[int, int]  # codepoint -> advance in font units
    fallback: int  # advance used for characters the font lacks (another font will draw them)

    def width(self, text: str, em_px: float) -> float:
        return sum(self.advances.get(ord(c), self.fallback) for c in text) * em_px / self.units_per_em


@cache
def metrics(file: str) -> Metrics:
    data = (FONTS_DIR / file).read_bytes()
    t = _tables(data)
    (upem,) = struct.unpack_from(">H", data, t[b"head"][0] + 18)
    (n_hm,) = struct.unpack_from(">H", data, t[b"hhea"][0] + 34)
    hm = t[b"hmtx"][0]
    adv = [struct.unpack_from(">H", data, hm + 4 * i)[0] for i in range(n_hm)]
    cmap = _cmap(data, t[b"cmap"][0])
    advances = {c: adv[min(g, n_hm - 1)] for c, g in cmap.items()}
    fallback = advances.get(ord("M"), upem)  # generous: unknown glyphs (e.g. CJK) are usually ~1 em wide
    return Metrics(upem, advances, max(fallback, upem))


@cache
def read(file: str) -> FontInfo:
    data = (FONTS_DIR / file).read_bytes()
    t = _tables(data)
    head_off = t[b"head"][0]
    (upem,) = struct.unpack_from(">H", data, head_off + 18)
    os2 = t[b"OS/2"][0]
    (version,) = struct.unpack_from(">H", data, os2)
    win_asc, win_desc = struct.unpack_from(">HH", data, os2 + 74)
    typo_asc, typo_desc = struct.unpack_from(">hh", data, os2 + 68)
    cap = struct.unpack_from(">h", data, os2 + 88)[0] if version >= 2 else int(upem * 0.7)
    family = _name(data, t[b"name"][0], 1)
    if not family:
        raise ValueError(f"{file}: no family name")
    sub = _name(data, t[b"name"][0], 2) or ""
    return FontInfo(file, family, upem, win_asc, win_desc, cap, typo_asc, abs(typo_desc), "Bold" in sub)
