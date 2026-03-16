"""
Tor circuit management: connection verification, identity renewal, and
health monitoring over the SOCKS5 + control port interface.

Requires a running Tor daemon with ControlPort enabled (default 9051).
Circuit renewal uses the Stem library to signal NEWNYM.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

_IP_SERVICES = [
    "https://api.ipify.org?format=json",
    "https://checkip.amazonaws.com",
    "https://api.my-ip.io/v1/ip",
    "https://ifconfig.me/ip",
]

TOR_CHECK_URL = "https://check.torproject.org/api/ip"


@dataclass
class TorConfig:
    socks_port: int = 9050
    control_port: int = 9051
    control_password: str | None = field(
        default_factory=lambda: os.environ.get("TOR_CONTROL_PASSWORD")
    )
    renewal_interval: int = 10
    renewal_cooldown: float = 5.0
    connect_timeout: float = 15.0
    max_retries: int = 3


class TorManager:
    """Manages a Tor SOCKS5 proxy connection with circuit renewal.

    Typical usage::

        tor = TorManager()
        tor.verify()                    # confirm Tor is reachable
        proxy_url = tor.proxy_url      # "socks5h://127.0.0.1:9050"

        for i, item in enumerate(work):
            if i > 0 and i % tor.config.renewal_interval == 0:
                tor.renew_circuit()
            do_work(item, proxy=proxy_url)
    """

    def __init__(self, config: TorConfig | None = None) -> None:
        self.config = config or TorConfig()
        self._operations = 0
        self._last_renewal = 0.0

    @property
    def proxy_url(self) -> str:
        """SOCKS5h URL (DNS resolved through Tor)."""
        return f"socks5h://127.0.0.1:{self.config.socks_port}"

    @property
    def proxy_dict(self) -> dict[str, str]:
        """Proxy dict for ``requests`` library."""
        url = self.proxy_url
        return {"http": url, "https": url}

    def verify(self) -> bool:
        """Confirm traffic is routing through Tor.

        Returns True if the Tor check API confirms we're on the Tor network.
        Retries up to ``config.max_retries`` times with backoff.
        """
        for attempt in range(self.config.max_retries):
            try:
                resp = requests.get(
                    TOR_CHECK_URL,
                    proxies=self.proxy_dict,
                    timeout=self.config.connect_timeout,
                )
                data = resp.json()
                if data.get("IsTor"):
                    log.info("tor connection verified (IP: %s)", data.get("IP", "?"))
                    return True
                log.warning("connected but not routing through Tor")
                return False
            except Exception as exc:
                if attempt < self.config.max_retries - 1:
                    wait = (attempt + 1) * 3
                    log.warning(
                        "tor check failed (attempt %d/%d): %s - retrying in %ds",
                        attempt + 1,
                        self.config.max_retries,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    log.error(
                        "tor unreachable after %d attempts: %s",
                        self.config.max_retries,
                        exc,
                    )
        return False

    def renew_circuit(self) -> bool:
        """Request a new Tor circuit via the control port.

        Sends the NEWNYM signal through Stem. Respects a cooldown period
        (default 5s) to allow the new circuit to establish.
        """
        elapsed = time.monotonic() - self._last_renewal
        if elapsed < self.config.renewal_cooldown:
            remaining = self.config.renewal_cooldown - elapsed
            log.debug("circuit renewal cooldown: %.1fs remaining", remaining)
            time.sleep(remaining)

        try:
            from stem import Signal
            from stem.control import Controller

            with Controller.from_port(port=self.config.control_port) as ctl:
                if self.config.control_password:
                    ctl.authenticate(password=self.config.control_password)
                else:
                    ctl.authenticate()
                ctl.signal(Signal.NEWNYM)

            self._last_renewal = time.monotonic()
            time.sleep(self.config.renewal_cooldown)
            log.info("tor circuit renewed")
            return True

        except ImportError:
            log.error("stem package required for circuit renewal: pip install stem")
            return False
        except Exception as exc:
            log.error("circuit renewal failed: %s", exc)
            return False

    def get_ip(self) -> str:
        """Fetch current exit IP through the Tor proxy.

        Tries multiple IP-check services in case one is rate-limited or down.
        """
        for service_url in _IP_SERVICES:
            try:
                resp = requests.get(
                    service_url,
                    proxies=self.proxy_dict,
                    timeout=10,
                )
                content_type = resp.headers.get("content-type", "")
                if "json" in content_type:
                    data = resp.json()
                    ip = data.get("ip", "")
                    if "error" in data:
                        continue
                else:
                    ip = resp.text.strip()

                if ip and len(ip) < 50:
                    return ip
            except Exception:
                continue

        return "unknown"

    def tick(self) -> None:
        """Increment the operation counter; renew circuit when interval is hit."""
        self._operations += 1
        if self._operations > 0 and self._operations % self.config.renewal_interval == 0:
            log.info("auto-renewing circuit (every %d ops)", self.config.renewal_interval)
            self.renew_circuit()
