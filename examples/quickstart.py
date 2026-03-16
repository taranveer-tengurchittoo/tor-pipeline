#!/usr/bin/env python3
"""
Quickstart: demonstrates the core workflow without Tor.

Runs against httpbin.org to show how the stealth browser, network
interceptor, and checkpoint system work together. No Tor daemon or
proxy required. Just Chrome.

Run:
    python examples/quickstart.py
"""

import logging
import os

import requests

from tor_pipeline import Checkpoint, NetworkInterceptor, StealthBrowser
from tor_pipeline.browser import BrowserConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("quickstart")


def main():
    # -- Browser setup (no proxy needed for this demo) --
    browser = StealthBrowser(config=BrowserConfig(headless=True))
    driver = browser.create()

    try:
        # -- Verify stealth --
        driver.get("https://httpbin.org/html")
        webdriver_val = driver.execute_script("return navigator.webdriver")
        ua = browser.extract_user_agent(driver)
        log.info("navigator.webdriver = %s (should be None)", webdriver_val)
        log.info("user-agent: %s", ua)

        # -- Interceptor: capture a fetch() response --
        interceptor = NetworkInterceptor(driver)
        interceptor.watch("httpbin.org/get", key="demo")

        # Trigger a fetch from within the page
        driver.execute_script("fetch('https://httpbin.org/get?source=tor-pipeline')")
        data = interceptor.wait_for("demo", timeout=10, poll_interval=0.5)

        if data:
            log.info("intercepted fetch response:")
            log.info("  origin: %s", data.get("origin"))
            log.info("  args:   %s", data.get("args"))
        else:
            log.error("interceptor did not capture the response")
            return

        # -- Cookie/header extraction for direct API calls --
        headers = browser.build_headers(driver, origin="https://httpbin.org")

        resp = requests.get(
            "https://httpbin.org/get",
            params={"method": "direct"},
            headers=headers,
            timeout=10,
        )
        log.info("direct API call: status=%d", resp.status_code)

        # -- Checkpoint: track progress --
        os.makedirs("data", exist_ok=True)
        cp = Checkpoint("data/quickstart_progress.json")
        cp.load()

        items = ["alpha", "bravo", "charlie", "delta", "echo"]
        for item in items:
            if cp.is_done(item):
                log.info("[%s] already done, skipping", item)
                continue

            resp = requests.get(
                "https://httpbin.org/get",
                params={"item": item},
                headers=headers,
                timeout=10,
            )

            if resp.status_code == 200:
                cp.mark_done(item)
                log.info("[%s] done", item)
            else:
                cp.mark_failed(item, f"http_{resp.status_code}")
                log.warning("[%s] failed: %d", item, resp.status_code)
            cp.save()

        log.info("checkpoint: %s", cp.summary())
        log.info("run this script again - checkpoint will skip completed items")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
