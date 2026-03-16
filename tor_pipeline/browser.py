"""
Stealth browser driver: Selenium WebDriver with anti-detection hardening.

Removes common automation fingerprints that WAFs and bot-detection systems
look for (navigator.webdriver, Chrome automation flags, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

if TYPE_CHECKING:
    from tor_pipeline.proxy import ProxyChain

log = logging.getLogger(__name__)


@dataclass
class BrowserConfig:
    headless: bool = False
    window_width: int = 1920
    window_height: int = 1080
    user_data_dir: str | None = None
    binary_location: str | None = None
    chromedriver_path: str | None = None


class StealthBrowser:
    """Chrome WebDriver with automation fingerprint removal.

    Applies the following anti-detection measures:

    1. Excludes the ``enable-automation`` switch that Chrome sets
    2. Disables ``AutomationControlled`` Blink feature flag
    3. Overrides ``navigator.webdriver`` to return ``undefined``
    4. Disables the ``useAutomationExtension`` flag

    Usage::

        browser = StealthBrowser()
        driver = browser.create()
        driver.get("https://example.com")
        cookies = browser.extract_cookies(driver)
        ua = browser.extract_user_agent(driver)
    """

    def __init__(
        self,
        config: BrowserConfig | None = None,
        proxy_chain: ProxyChain | None = None,
    ) -> None:
        self.config = config or BrowserConfig()
        self.proxy_chain = proxy_chain

    def _build_options(self) -> Options:
        options = Options()

        # Core anti-detection flags
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        if self.config.headless:
            options.add_argument("--headless=new")

        if self.config.user_data_dir:
            options.add_argument(f"--user-data-dir={self.config.user_data_dir}")

        if self.config.binary_location:
            options.binary_location = self.config.binary_location

        # Apply proxy if configured
        if self.proxy_chain:
            self.proxy_chain.configure_chrome(options)

        return options

    def create(self) -> webdriver.Chrome:
        """Create and return a hardened Chrome WebDriver instance."""
        options = self._build_options()

        service_kwargs = {}
        if self.config.chromedriver_path:
            service_kwargs["executable_path"] = self.config.chromedriver_path

        driver = webdriver.Chrome(
            options=options,
            service=Service(**service_kwargs) if service_kwargs else Service(),
        )

        # Remove navigator.webdriver fingerprint.
        # addScriptToEvaluateOnNewDocument persists the override across navigations,
        # unlike a one-shot execute_script which resets on every page load.
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )

        driver.set_window_size(self.config.window_width, self.config.window_height)
        log.info("stealth browser created")
        return driver

    @staticmethod
    def extract_cookies(driver: webdriver.Chrome) -> dict[str, str]:
        """Extract all cookies from the current browser session."""
        return {c["name"]: c["value"] for c in driver.get_cookies()}

    @staticmethod
    def extract_user_agent(driver: webdriver.Chrome) -> str:
        """Read the browser's User-Agent string."""
        return driver.execute_script("return navigator.userAgent")

    @staticmethod
    def build_headers(
        driver: webdriver.Chrome,
        origin: str | None = None,
        referer: str | None = None,
    ) -> dict[str, str]:
        """Build request headers that match the browser session.

        Copies the User-Agent from the live browser so that subsequent
        ``requests`` calls look identical to the Selenium session.
        """
        ua = StealthBrowser.extract_user_agent(driver)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": ua,
        }
        if origin:
            headers["Origin"] = origin
        if referer:
            headers["Referer"] = referer
        return headers
