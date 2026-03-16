"""Tests for Checkpoint: persistence, resume, atomic writes, disk merge."""

import json

import pytest

from tor_pipeline.checkpoint import Checkpoint


@pytest.fixture
def cp_path(tmp_path):
    return tmp_path / "progress.json"


def test_empty_load(cp_path):
    cp = Checkpoint(cp_path)
    cp.load()
    assert cp.processed_count == 0
    assert cp.failed_count == 0


def test_mark_done_and_save(cp_path):
    cp = Checkpoint(cp_path)
    cp.mark_done("item_1")
    cp.mark_done("item_2")
    cp.save()

    cp2 = Checkpoint(cp_path)
    cp2.load()
    assert cp2.is_done("item_1")
    assert cp2.is_done("item_2")
    assert not cp2.is_done("item_3")
    assert cp2.processed_count == 2


def test_mark_failed_with_reason(cp_path):
    cp = Checkpoint(cp_path)
    cp.mark_failed("bad_1", "timeout")
    cp.mark_failed("bad_2", "http_403")
    cp.save()

    cp2 = Checkpoint(cp_path)
    cp2.load()
    assert cp2.is_failed("bad_1")
    assert cp2.is_done("bad_1")  # is_done covers both processed and failed
    assert cp2.failed_count == 2
    assert cp2.failed_by_reason() == {"timeout": 1, "http_403": 1}


def test_get_remaining(cp_path):
    cp = Checkpoint(cp_path)
    cp.mark_done("a")
    cp.mark_failed("b", "error")

    remaining = cp.get_remaining(["a", "b", "c", "d"])
    assert remaining == ["c", "d"]


def test_merge_from_disk(cp_path, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "file_001.json").write_text("{}")
    (output_dir / "file_002.json").write_text("{}")
    (output_dir / "readme.txt").write_text("")  # should be ignored

    cp = Checkpoint(cp_path)
    discovered = cp.merge_from_disk(output_dir)
    assert discovered == 2
    assert cp.is_processed("file_001")
    assert cp.is_processed("file_002")
    assert not cp.is_processed("readme")


def test_summary(cp_path):
    cp = Checkpoint(cp_path)
    cp.mark_done("ok_1")
    cp.mark_done("ok_2")
    cp.mark_failed("fail_1", "timeout")
    cp.mark_failed("fail_2", "timeout")
    cp.mark_failed("fail_3", "not_found")

    summary = cp.summary()
    assert "processed: 2" in summary
    assert "failed:    3" in summary
    assert "timeout: 2" in summary
    assert "not_found: 1" in summary


def test_atomic_write_creates_parent_dirs(tmp_path):
    deep_path = tmp_path / "a" / "b" / "c" / "progress.json"
    cp = Checkpoint(deep_path)
    cp.mark_done("item")
    cp.save()  # should create parent dirs
    assert deep_path.exists()


def test_corrupt_checkpoint_starts_fresh(cp_path):
    cp_path.write_text("not valid json{{{")
    cp = Checkpoint(cp_path)
    cp.load()  # should not raise
    assert cp.processed_count == 0


def test_sorted_output(cp_path):
    cp = Checkpoint(cp_path)
    cp.mark_done("z")
    cp.mark_done("a")
    cp.mark_done("m")
    cp.save()

    with open(cp_path) as f:
        data = json.load(f)
    assert data["processed"] == ["a", "m", "z"]
