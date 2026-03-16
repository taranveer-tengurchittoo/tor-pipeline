#!/usr/bin/env python3
"""
Template: multi-worker parallel scraping with Tor and checkpointing.

Two-stage pipeline:
  Stage 1 (discovery): search the API to collect all record IDs
  Stage 2 (extraction): visit each record's detail page via browser,
                         intercept the API response, save the JSON

Each stage runs N independent workers. Workers share nothing: each has
its own browser, proxy connection, checkpoint file, and output directory.

Usage:
    python parallel_scrape.py stage1 --worker 1    # run one worker
    python parallel_scrape.py stage1 --all          # launch all workers
    python parallel_scrape.py merge                 # deduplicate stage 1 output
    python parallel_scrape.py stage2 --worker 1
    python parallel_scrape.py stage2 --all

Adapt the constants, API payload, CSS selectors, and record ID field
for your target site.
"""

import argparse
import json
import logging
import os
import time
from collections import OrderedDict

import requests
from selenium.webdriver.common.by import By

from tor_pipeline import (
    ChallengeHandler,
    Checkpoint,
    NetworkInterceptor,
    ProxyChain,
    StealthBrowser,
    TorManager,
    WorkerPool,
)
from tor_pipeline.workers import PoolConfig, distribute_work, generate_search_space

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parallel_scrape")

# ---------------------------------------------------------------------------
# CONFIGURE THESE for your target site
# ---------------------------------------------------------------------------
TARGET_URL = "https://example.com"  # page that triggers the challenge
SEARCH_API = "https://example.com/api/search"  # search/listing endpoint
DETAIL_PATTERN = "api/detail"  # substring in the detail API URL to intercept
RECORD_ID_FIELD = "id"  # JSON field name for the unique record identifier
NUM_WORKERS = 10

# How to split the alphabet across workers. Each worker searches its
# assigned letter range exhaustively (e.g., AAA-BZZ for worker 1).
LETTER_RANGES = {
    1: ("A", "B"),
    2: ("C", "D"),
    3: ("E", "F"),
    4: ("G", "H"),
    5: ("I", "J"),
    6: ("K", "L"),
    7: ("M", "N"),
    8: ("O", "P"),
    9: ("Q", "R"),
    10: ("S", "Z"),
}


def run_stage1(worker_id: int):
    """Collect all record IDs via API search."""
    terms = generate_search_space(LETTER_RANGES, worker_id, depth=3)
    log.info("stage1 worker %d: %d search terms", worker_id, len(terms))

    tor = TorManager()
    tor.verify()

    chain = ProxyChain([tor.proxy_url])
    browser = StealthBrowser(proxy_chain=chain)
    driver = browser.create()

    challenge = ChallengeHandler(target_url=TARGET_URL)
    challenge.solve(driver)
    cookies = challenge.get_cookies(driver)
    headers = browser.build_headers(driver, origin=TARGET_URL, referer=TARGET_URL + "/")

    cp = Checkpoint(f"progress/stage1_worker_{worker_id}.json")
    cp.load()

    output_path = f"data/ids/worker_{worker_id}.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    collected = []

    try:
        for i, term in enumerate(terms):
            if cp.is_done(term):
                continue

            if challenge.needs_refresh():
                challenge.solve(driver)
                cookies = challenge.get_cookies(driver)
                headers = browser.build_headers(driver, origin=TARGET_URL, referer=TARGET_URL + "/")
            challenge.tick(driver)

            tor.tick()

            # Paginate through all results for this search term.
            # Replace the payload with whatever your target API expects.
            page = 0
            while True:
                resp = requests.post(
                    SEARCH_API,
                    json={"query": term, "page": page, "size": 100},
                    cookies=cookies,
                    headers=headers,
                    proxies=tor.proxy_dict,
                )

                if resp.status_code != 200:
                    log.warning("[%s] HTTP %d, refreshing session", term, resp.status_code)
                    challenge.solve(driver)
                    cookies = challenge.get_cookies(driver)
                    break

                data = resp.json()
                results = data.get("results", [])
                if not results:
                    break

                collected.extend(results)

                if len(results) < 100:
                    break
                page += 1
                time.sleep(0.3)

            cp.mark_done(term)
            if i % 50 == 0:
                cp.save()
                with open(output_path, "w") as f:
                    json.dump(collected, f)

    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        driver.quit()
        cp.save()
        with open(output_path, "w") as f:
            json.dump(collected, f, indent=2)
        log.info("stage1 worker %d: %d records collected", worker_id, len(collected))


def run_merge():
    """Deduplicate stage 1 output and split into batches for stage 2."""
    all_records: OrderedDict[str, dict] = OrderedDict()

    for i in range(1, NUM_WORKERS + 1):
        path = f"data/ids/worker_{i}.json"
        if not os.path.exists(path):
            continue
        with open(path) as f:
            records = json.load(f)
        before = len(all_records)
        for r in records:
            key = str(r.get(RECORD_ID_FIELD, r))
            if key not in all_records:
                all_records[key] = r
        log.info("worker %d: %d records, %d new", i, len(records), len(all_records) - before)

    master = list(all_records.values())
    log.info("total unique: %d", len(master))

    os.makedirs("data/batches", exist_ok=True)
    batches = distribute_work(master, NUM_WORKERS)
    for i, batch in enumerate(batches, 1):
        with open(f"data/batches/batch_{i}.json", "w") as f:
            json.dump(batch, f, indent=2)
        log.info("batch %d: %d records", i, len(batch))


def run_stage2(worker_id: int):
    """Fetch detail pages via browser + API interception."""
    batch_path = f"data/batches/batch_{worker_id}.json"
    if not os.path.exists(batch_path):
        log.error("batch file not found: %s (run merge first)", batch_path)
        return

    with open(batch_path) as f:
        batch = json.load(f)
    log.info("stage2 worker %d: %d records", worker_id, len(batch))

    tor = TorManager()
    tor.verify()

    chain = ProxyChain([tor.proxy_url])
    browser = StealthBrowser(proxy_chain=chain)
    driver = browser.create()

    challenge = ChallengeHandler(target_url=TARGET_URL)
    challenge.solve(driver)

    interceptor = NetworkInterceptor(driver)
    interceptor.watch(DETAIL_PATTERN, key="detail")

    cp = Checkpoint(f"progress/stage2_worker_{worker_id}.json")
    cp.load()

    output_dir = f"data/details/worker_{worker_id}"
    os.makedirs(output_dir, exist_ok=True)
    cp.merge_from_disk(output_dir)

    try:
        for i, record in enumerate(batch):
            record_id = str(record.get(RECORD_ID_FIELD, ""))
            if cp.is_done(record_id):
                continue

            challenge.tick(driver)
            tor.tick()

            # Navigate to the target and search for this record.
            # Replace the CSS selectors below with ones matching your target.
            driver.get(TARGET_URL)
            time.sleep(2)
            interceptor.watch(DETAIL_PATTERN, key="detail")

            search_input = driver.find_element(By.CSS_SELECTOR, "input[type='text']")
            search_input.clear()
            search_input.send_keys(record_id)
            time.sleep(1)

            # Click the button that triggers the detail API call.
            interceptor.clear("detail")
            interceptor.click_with_events('[title="View"]')

            time.sleep(8)
            data = interceptor.wait_for("detail", timeout=30)

            if data:
                safe_id = record_id.replace("/", "_")
                with open(f"{output_dir}/{safe_id}.json", "w") as f:
                    json.dump(data, f, indent=2)
                cp.mark_done(record_id)
                log.info("[%d/%d] saved %s", i + 1, len(batch), safe_id)
            else:
                cp.mark_failed(record_id, "timeout")
                log.warning("[%d/%d] failed %s", i + 1, len(batch), record_id)

            cp.save()

    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        driver.quit()
        cp.save()
        log.info("stage2 worker %d: %s", worker_id, cp.summary())


def main():
    parser = argparse.ArgumentParser(description="Parallel scraping pipeline")
    sub = parser.add_subparsers(dest="command")

    s1 = sub.add_parser("stage1", help="Collect record IDs via API")
    s1_group = s1.add_mutually_exclusive_group(required=True)
    s1_group.add_argument("--worker", type=int)
    s1_group.add_argument("--all", action="store_true")

    sub.add_parser("merge", help="Deduplicate and batch")

    s2 = sub.add_parser("stage2", help="Fetch details via browser")
    s2_group = s2.add_mutually_exclusive_group(required=True)
    s2_group.add_argument("--worker", type=int)
    s2_group.add_argument("--all", action="store_true")

    args = parser.parse_args()

    if args.command == "stage1":
        if args.all:
            pool = WorkerPool(
                script=__file__,
                command="stage1",
                config=PoolConfig(num_workers=NUM_WORKERS),
            )
            pool.launch()
        else:
            run_stage1(args.worker)

    elif args.command == "merge":
        run_merge()

    elif args.command == "stage2":
        if args.all:
            pool = WorkerPool(
                script=__file__,
                command="stage2",
                config=PoolConfig(num_workers=NUM_WORKERS),
            )
            pool.launch()
        else:
            run_stage2(args.worker)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
