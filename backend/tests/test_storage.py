from __future__ import annotations

from pathlib import Path

import pytest

from clipper.core.ids import is_valid_id, new_id
from clipper.storage import LocalStorage, UnsafePathError


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../x",
        "a/../../x",
        "a/./b",
        "a//b",
        "a\\b",
        "C:/x",
        "a/.hidden",
        "con",
        "projects/NUL.txt",
        "a/" + "x" * 200,
        "a/b c",
        "a/\x00",
    ],
)
def test_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(UnsafePathError):
        LocalStorage(tmp_path).path(key)


def test_atomic_write_and_read(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path)
    st.write_bytes("projects/p1/a.json", b"{}")
    assert st.read_bytes("projects/p1/a.json") == b"{}"
    assert not list((tmp_path / "projects" / "p1").glob(".part-*"))


def test_failed_write_leaves_nothing(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path)
    with pytest.raises(RuntimeError), st.open_write("projects/p1/b.bin") as fh:
        fh.write(b"partial")
        raise RuntimeError("crash mid-write")
    assert not st.exists("projects/p1/b.bin")
    assert not list((tmp_path / "projects" / "p1").glob(".part-*"))


def test_delete_prefix(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path)
    st.write_bytes("projects/p1/x/y.bin", b"1")
    st.write_bytes("projects/p2/y.bin", b"2")
    st.delete_prefix("projects/p1")
    assert not st.exists("projects/p1") and st.exists("projects/p2/y.bin")


def test_symlink_escape_blocked(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    st = LocalStorage(tmp_path / "root")
    try:
        (st.root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this OS")
    with pytest.raises(UnsafePathError):
        st.path("link/file")


def test_ids_sortable_and_valid() -> None:
    ids = [new_id() for _ in range(200)]
    assert all(is_valid_id(i) for i in ids)
    assert len(set(ids)) == 200
    assert not is_valid_id("../../etc") and not is_valid_id("A" * 32)
