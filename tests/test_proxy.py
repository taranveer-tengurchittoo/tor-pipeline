"""Tests for ProxyChain: rotation, health tracking, failover."""

import pytest

from tor_pipeline.proxy import ProxyChain


def test_single_proxy():
    chain = ProxyChain(["socks5h://127.0.0.1:9050"])
    assert chain.current == "socks5h://127.0.0.1:9050"
    assert chain.pool_size == 1


def test_rotation():
    chain = ProxyChain(["http://a:80", "http://b:80", "http://c:80"])
    assert chain.current == "http://a:80"
    chain.rotate()
    assert chain.current == "http://b:80"
    chain.rotate()
    assert chain.current == "http://c:80"
    chain.rotate()
    assert chain.current == "http://a:80"  # wraps around


def test_failure_tracking():
    chain = ProxyChain(["http://a:80", "http://b:80"], max_failures=2)
    # fail proxy a twice
    chain.record_failure()
    chain.record_failure()
    # a is now degraded, rotation should skip it
    chain.rotate()
    assert chain.current == "http://b:80"


def test_all_degraded_resets():
    chain = ProxyChain(["http://a:80", "http://b:80"], max_failures=1)
    chain.record_failure()  # a degraded
    chain.rotate()
    chain.record_failure()  # b degraded
    # all degraded - rotate should reset and return a
    chain.rotate()
    assert chain.healthy_count == 2  # counters reset


def test_success_resets_failure():
    chain = ProxyChain(["http://a:80"], max_failures=3)
    chain.record_failure()
    chain.record_failure()
    assert chain.current_proxy.failures == 2
    chain.record_success()
    assert chain.current_proxy.failures == 0


def test_proxy_dict():
    chain = ProxyChain(["socks5h://127.0.0.1:9050"])
    pd = chain.proxy_dict()
    assert pd["http"] == "socks5h://127.0.0.1:9050"
    assert pd["https"] == "socks5h://127.0.0.1:9050"


def test_empty_raises():
    with pytest.raises(ValueError):
        ProxyChain([])


def test_healthy_count():
    chain = ProxyChain(["http://a:80", "http://b:80", "http://c:80"], max_failures=1)
    assert chain.healthy_count == 3
    chain.record_failure()  # a goes down
    assert chain.healthy_count == 2
