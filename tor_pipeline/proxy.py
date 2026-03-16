"""
Proxy chain: rotation, health tracking, and failover across multiple
upstream proxies (Tor, SOCKS4/5, HTTP/HTTPS).

ProxyChain maintains per-proxy failure counters and automatically skips
degraded proxies until the rest of the pool is exhausted, at which point
counters reset and the full pool is retried.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from selenium.webdriver.chrome.options import Options

log = logging.getLogger(__name__)


@dataclass
class Proxy:
    url: str
    failures: int = 0
    max_failures: int = 3

    @property
    def healthy(self) -> bool:
        return self.failures < self.max_failures

    def record_failure(self) -> None:
        self.failures += 1
        log.warning("proxy failure #%d: %s", self.failures, self.url)

    def record_success(self) -> None:
        self.failures = 0


class ProxyChain:
    """Rotating proxy pool with health-aware selection.

    Supports any combination of SOCKS4, SOCKS5, SOCKS5h (DNS-through-proxy),
    HTTP, and HTTPS upstream proxies.

    Usage::

        chain = ProxyChain([
            "socks5h://127.0.0.1:9050",   # Tor
            "http://proxy2.example:8080",
            "socks5://proxy3.example:1080",
        ])

        proxy_url = chain.current
        # ... use proxy_url, then report outcome:
        chain.record_success()
        chain.rotate()
    """

    def __init__(self, proxy_urls: list[str], max_failures: int = 3) -> None:
        if not proxy_urls:
            raise ValueError("at least one proxy URL is required")
        self._proxies = [Proxy(url=u, max_failures=max_failures) for u in proxy_urls]
        self._index = 0

    @property
    def current(self) -> str:
        """URL of the currently selected proxy."""
        return self._proxies[self._index].url

    @property
    def current_proxy(self) -> Proxy:
        return self._proxies[self._index]

    @property
    def pool_size(self) -> int:
        return len(self._proxies)

    @property
    def healthy_count(self) -> int:
        return sum(1 for p in self._proxies if p.healthy)

    def rotate(self) -> str:
        """Advance to the next healthy proxy. Returns its URL.

        If every proxy has hit its failure limit the counters are reset and
        rotation starts from the beginning of the pool.
        """
        for _ in range(len(self._proxies)):
            self._index = (self._index + 1) % len(self._proxies)
            if self._proxies[self._index].healthy:
                log.debug(
                    "rotated to proxy %d/%d: %s",
                    self._index + 1,
                    len(self._proxies),
                    self.current,
                )
                return self.current

        # All exhausted - reset and start over
        log.warning("all proxies degraded - resetting failure counters")
        for p in self._proxies:
            p.failures = 0
        self._index = 0
        return self.current

    def record_success(self) -> None:
        self.current_proxy.record_success()

    def record_failure(self) -> None:
        self.current_proxy.record_failure()

    def proxy_dict(self, url: str | None = None) -> dict[str, str]:
        """Return a ``requests``-compatible proxy dict."""
        u = url or self.current
        return {"http": u, "https": u}

    def configure_chrome(self, options: Options) -> Options:
        """Add the current proxy to Selenium Chrome options.

        Handles the SOCKS5h → SOCKS5 translation that Chrome requires
        (Chrome resolves DNS through SOCKS5 by default, so ``socks5h`` is
        not a recognized scheme in the ``--proxy-server`` flag).
        """
        url = self.current
        if url.startswith("socks5h://"):
            chrome_url = "socks5://" + url[len("socks5h://") :]
        elif url.startswith(("socks5://", "socks4://", "http://", "https://")):
            chrome_url = url
        else:
            chrome_url = f"http://{url}"

        options.add_argument(f"--proxy-server={chrome_url}")
        log.info("chrome proxy: %s", chrome_url)
        return options
