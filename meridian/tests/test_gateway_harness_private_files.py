"""Harness output is written owner-only, to a unique staging name, synced, then renamed."""

import os
import stat

import pytest

from scripts.gateway_harness import private_files


@pytest.fixture(autouse=True)
def open_umask():
    previous = os.umask(0)
    yield
    os.umask(previous)


def test_a_file_is_written_owner_only_whatever_the_umask(tmp_path):
    target = tmp_path / "ledger.json"
    private_files.write_private(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_replacing_a_file_keeps_it_owner_only_and_leaves_no_staging_file(tmp_path):
    target = tmp_path / "ledger.json"
    target.write_text("old")
    target.chmod(0o644)
    private_files.write_private(target, "new")
    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [path.name for path in tmp_path.iterdir()] == ["ledger.json"]


def test_directories_are_created_owner_only_including_missing_parents(tmp_path):
    folder = tmp_path / "out" / "run" / "recorded"
    private_files.private_dir(folder)
    for path in (folder, folder.parent, folder.parent.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_the_contents_are_synced_to_disk_before_the_rename(tmp_path, monkeypatch):
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: order.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(os, "replace", lambda a, b: order.append("replace") or real_replace(a, b))
    private_files.write_private(tmp_path / "verdicts.json", "[]")
    assert order == ["fsync", "replace"]


def test_each_write_draws_its_own_staging_name(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: seen.append(a.name) or real_replace(a, b))
    private_files.write_private(tmp_path / "ledger.json", "one")
    private_files.write_private(tmp_path / "ledger.json", "two")
    assert len(set(seen)) == 2 and all(name.endswith(".tmp") for name in seen)


def test_an_existing_staging_file_is_never_overwritten_or_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(private_files.secrets, "token_hex", lambda n: "aaaa")
    other = tmp_path / "ledger.json.aaaa.tmp"
    other.write_text("someone else's staging file")
    with pytest.raises(FileExistsError):
        private_files.write_private(tmp_path / "ledger.json", "mine")
    assert other.read_text() == "someone else's staging file"
    assert not (tmp_path / "ledger.json").exists()


def test_a_failed_write_removes_its_staging_file_and_keeps_the_old_content(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    target.write_text("old")

    def broken(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(OSError, match="disk full"):
        private_files.write_private(target, "new")
    assert target.read_text() == "old"
    assert [path.name for path in tmp_path.iterdir()] == ["ledger.json"]
