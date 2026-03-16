"""Tests for work distribution and search space generation."""

from tor_pipeline.workers import distribute_work, generate_search_space


def test_distribute_even():
    items = list(range(10))
    batches = distribute_work(items, 5)
    assert len(batches) == 5
    assert all(len(b) == 2 for b in batches)
    # all items present
    assert sorted(x for b in batches for x in b) == items


def test_distribute_uneven():
    items = list(range(7))
    batches = distribute_work(items, 3)
    assert len(batches) == 3
    sizes = [len(b) for b in batches]
    assert sizes == [3, 2, 2]
    assert sorted(x for b in batches for x in b) == items


def test_distribute_more_workers_than_items():
    items = [1, 2, 3]
    batches = distribute_work(items, 5)
    assert len(batches) == 5
    non_empty = [b for b in batches if b]
    assert len(non_empty) == 3


def test_distribute_single_worker():
    items = list(range(100))
    batches = distribute_work(items, 1)
    assert len(batches) == 1
    assert batches[0] == items


def test_generate_search_space_depth3():
    ranges = {1: ("A", "A")}
    terms = generate_search_space(ranges, 1, depth=3)
    # A followed by 26*26 = 676 combinations
    assert len(terms) == 676
    assert terms[0] == "AAA"
    assert terms[-1] == "AZZ"


def test_generate_search_space_depth2():
    ranges = {1: ("A", "B")}
    terms = generate_search_space(ranges, 1, depth=2)
    # A* = 26, B* = 26 -> 52
    assert len(terms) == 52
    assert terms[0] == "AA"
    assert terms[-1] == "BZ"


def test_generate_search_space_depth1():
    ranges = {1: ("X", "Z")}
    terms = generate_search_space(ranges, 1, depth=1)
    assert terms == ["X", "Y", "Z"]


def test_generate_search_space_no_overlap():
    ranges = {1: ("A", "B"), 2: ("C", "D")}
    t1 = generate_search_space(ranges, 1, depth=1)
    t2 = generate_search_space(ranges, 2, depth=1)
    assert set(t1) & set(t2) == set()
