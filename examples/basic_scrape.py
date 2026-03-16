#!/usr/bin/env python3
"""
Template: single-threaded scrape through Tor with challenge bypass.

Shows the core workflow: Tor verification, stealth browser, Turnstile
bypass, cookie-based API calls, and checkpointed progress. Adapt the
constants and API payload below for your target site.

Run:
    python examples/basic_scrape.py
"""

import json
import logging
import os
import sys

import requests

from tor_pipeline import (
    ChallengeHandler,
    Checkpoint,
    ProxyChain,
    StealthBrowser,
    TorManager,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("basic_scrape")

# ---------------------------------------------------------------------------
# CONFIGURE THESE for your target site
# ---------------------------------------------------------------------------
TARGET_URL = "https://example.com"  # page that triggers the Turnstile challenge
SEARCH_API = "https://example.com/api/search"  # API endpoint to call after solving
OUTPUT_DIR = "data/output"


def main():
    # -- Tor setup --
    tor = TorManager()
    if not tor.verify():
        log.error("Tor is not reachable. Start it with: sudo systemctl start tor")
        sys.exit(1)
    log.info("exit IP: %s", tor.get_ip())

    # -- Browser setup --
    chain = ProxyChain([tor.proxy_url])
    browser = StealthBrowser(proxy_chain=chain)
    driver = browser.create()

    # -- Challenge --
    # Navigate to the target; the browser runs the Turnstile JS automatically.
    # After the wait, session cookies carry the solved challenge token.
    challenge = ChallengeHandler(target_url=TARGET_URL)
    challenge.solve(driver)
    cookies = challenge.get_cookies(driver)
    headers = browser.build_headers(driver, origin=TARGET_URL, referer=TARGET_URL + "/")

    # -- Checkpoint --
    cp = Checkpoint("progress/basic.json")
    cp.load()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # -- Scrape via API --
    # Replace with your own search terms or item list.
    search_terms = ["AAA", "AAB", "AAC"]

    try:
        for term in search_terms:
            if cp.is_done(term):
                continue

            # Re-solve challenge before cookies expire (default: every 30 ops)
            if challenge.needs_refresh():
                challenge.solve(driver)
                cookies = challenge.get_cookies(driver)
                headers = browser.build_headers(driver, origin=TARGET_URL, referer=TARGET_URL + "/")
            challenge.tick(driver)

            # Rotate Tor exit node periodically (default: every 10 ops)
            tor.tick()

            # Replace the payload with whatever your target API expects
            response = requests.post(
                SEARCH_API,
                json={"query": term, "page": 0, "size": 100},
                cookies=cookies,
                headers=headers,
                proxies=tor.proxy_dict,
            )

            if response.status_code != 200:
                cp.mark_failed(term, f"http_{response.status_code}")
                cp.save()
                continue

            data = response.json()
            results = data.get("results", [])
            log.info("[%s] %d results", term, len(results))

            with open(f"{OUTPUT_DIR}/{term}.json", "w") as f:
                json.dump(data, f, indent=2)

            cp.mark_done(term)
            cp.save()

    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        driver.quit()
        cp.save()
        log.info("done. %s", cp.summary())


if __name__ == "__main__":
    main()
