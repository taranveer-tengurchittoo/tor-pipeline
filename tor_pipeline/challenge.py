"""
Cloudflare Turnstile (and similar JS-challenge) handler.

Strategy: load the target page in Selenium, wait for the challenge script
to complete, then extract session cookies. The cookies carry the solved
challenge token and can be reused for direct HTTP requests until they
expire (typically 15-30 minutes depending on the provider).

Session refresh is handled by periodically re-solving the challenge.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

log = logging.getLogger(__name__)


@dataclass
class ChallengeConfig:
    challenge_wait: float = 12.0
    refresh_interval: int = 30
    cookie_banner_id: str | None = "accept-btn"


class ChallengeHandler:
    """Detects and waits through Cloudflare Turnstile challenges.

    Turnstile embeds an invisible iframe that runs a proof-of-work
    JavaScript challenge. There's no CAPTCHA to solve; the browser just
    needs to execute the script and wait. The result is a ``cf_clearance``
    cookie (or similar) attached to the session.

    Usage::

        handler = ChallengeHandler(target_url="https://example.com")
        handler.solve(driver)
        cookies = handler.get_cookies(driver)
        # use cookies with requests library
        # ...
        # periodically re-solve:
        if handler.needs_refresh():
            handler.solve(driver)
    """

    def __init__(
        self,
        target_url: str,
        config: ChallengeConfig | None = None,
    ) -> None:
        self.target_url = target_url
        self.config = config or ChallengeConfig()
        self._ops_since_refresh = 0

    def solve(self, driver: webdriver.Chrome) -> None:
        """Navigate to the target and wait for the JS challenge to clear."""
        log.info("solving challenge at %s", self.target_url)
        driver.get(self.target_url)
        time.sleep(self.config.challenge_wait)

        # Dismiss cookie consent banner if present
        if self.config.cookie_banner_id:
            try:
                driver.find_element(By.ID, self.config.cookie_banner_id).click()
                time.sleep(0.5)
            except NoSuchElementException:
                pass  # banner may not exist or may have already been dismissed

        self._ops_since_refresh = 0
        log.info("challenge solved")

    def tick(self, driver: webdriver.Chrome) -> None:
        """Increment op counter; re-solve when the refresh interval is hit."""
        self._ops_since_refresh += 1
        if self._ops_since_refresh >= self.config.refresh_interval:
            log.info(
                "session refresh triggered (every %d ops)",
                self.config.refresh_interval,
            )
            self.solve(driver)

    def needs_refresh(self) -> bool:
        return self._ops_since_refresh >= self.config.refresh_interval

    @staticmethod
    def get_cookies(driver: webdriver.Chrome) -> dict[str, str]:
        """Extract session cookies after challenge completion."""
        return {c["name"]: c["value"] for c in driver.get_cookies()}

    @staticmethod
    def is_challenge_pending(driver: webdriver.Chrome) -> bool:
        """Check whether a Turnstile iframe is still visible (unsolved)."""
        return driver.execute_script(
            """
            var t = document.querySelector('iframe[src*="turnstile"]')
                 || document.querySelector('iframe[src*="challenges.cloudflare"]');
            return !!(t && t.offsetParent !== null);
            """
        )
