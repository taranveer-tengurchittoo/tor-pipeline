# tor-pipeline

Privacy-first distributed scraping toolkit. Composable building blocks for anonymous, resilient web data extraction over Tor.

Built to solve a specific problem: scraping tens of thousands of records from sites behind Cloudflare Turnstile, where each request triggers a fresh JS challenge and rate limiting is aggressive. The architecture routes all traffic through Tor with automatic circuit renewal, distributes work across parallel browser instances, and checkpoints progress so a crash at record 40,000 doesn't mean starting over.

## Quick start

Try the toolkit without Tor. Just Chrome:

```bash
pip install -e .
python examples/quickstart.py
```

This runs against httpbin.org and demonstrates the stealth browser, network interceptor, cookie extraction, and checkpoint system in under 30 seconds.

## What this solves

Most scraping tools fall apart when the target has serious bot protection. Turnstile doesn't present a CAPTCHA. It runs invisible JavaScript proof-of-work that requires a real browser environment. Rate limiting kicks in fast. And you need to rotate IPs continuously.

tor-pipeline handles this by:

- **Routing through Tor** with automatic circuit renewal via the Stem control port
- **Solving Cloudflare Turnstile** by waiting through the JS challenge in Selenium, then extracting session cookies for direct API calls
- **Intercepting API responses** with injected JavaScript instead of parsing the DOM (survives frontend redesigns)
- **Distributing work** across N independent browser processes, each with its own proxy connection
- **Checkpointing every operation** so you can resume from exactly where you left off

## Architecture

```
                           ┌─────────────┐
                           │  Tor Daemon  │
                           │  (SOCKS5)    │
                           └──────┬───────┘
                                  │
           ┌──────────────────────┼──────────────────────┐
           │                      │                      │
     ┌─────┴──────┐        ┌─────┴──────┐        ┌─────┴──────┐
     │  Worker 1   │        │  Worker 2   │        │  Worker N   │
     │             │        │             │        │             │
     │  Browser    │        │  Browser    │        │  Browser    │
     │  Challenge  │        │  Challenge  │        │  Challenge  │
     │  Intercept  │        │  Intercept  │        │  Intercept  │
     │  Checkpoint │        │  Checkpoint │        │  Checkpoint │
     └─────────────┘        └─────────────┘        └─────────────┘
```

Each worker is an independent OS process with its own Chrome instance. Workers share nothing: no locks, no queues, no coordination. Work is partitioned upfront (e.g., alphabetic ranges, ID batches) and each worker plows through its slice independently.

This is deliberate. Shared-nothing makes the system trivially fault-tolerant: if worker 3 dies, workers 1-2 and 4-N keep going. Restart worker 3 and it picks up from its last checkpoint.

## Components

### `TorManager`

Manages the Tor SOCKS5 proxy. Verifies connectivity through `check.torproject.org`, renews circuits via the Stem control port, and provides proxy URLs for both `requests` and Selenium.

```python
from tor_pipeline import TorManager

tor = TorManager()
tor.verify()             # confirm routing through Tor
print(tor.get_ip())      # current exit node IP

# in a scraping loop:
for i, item in enumerate(items):
    tor.tick()           # auto-renews circuit every N operations
    scrape(item, proxy=tor.proxy_dict)
```

### `ProxyChain`

Rotating proxy pool with per-proxy health tracking. Tracks failure counts and automatically skips degraded proxies. When the entire pool is exhausted, counters reset and all proxies are retried.

```python
from tor_pipeline import ProxyChain

chain = ProxyChain([
    "socks5h://127.0.0.1:9050",    # Tor
    "http://proxy2.example:8080",
    "socks5://proxy3.example:1080",
])

url = chain.current
try:
    response = requests.get(target, proxies=chain.proxy_dict())
    chain.record_success()
except RequestException:
    chain.record_failure()
    chain.rotate()
```

### `StealthBrowser`

Chrome WebDriver with anti-detection hardening. Removes `navigator.webdriver`, strips automation flags, and provides cookie/header extraction for hybrid browser+requests workflows. The `navigator.webdriver` override persists across page navigations via CDP's `Page.addScriptToEvaluateOnNewDocument`.

```python
from tor_pipeline import StealthBrowser, ProxyChain

chain = ProxyChain(["socks5h://127.0.0.1:9050"])
browser = StealthBrowser(proxy_chain=chain)

driver = browser.create()
driver.get("https://target.example.com")

cookies = browser.extract_cookies(driver)
headers = browser.build_headers(driver, origin="https://target.example.com")
# now use cookies + headers with requests for fast API calls
```

### `ChallengeHandler`

Waits through Cloudflare Turnstile (or similar JS challenges). Turnstile doesn't need CAPTCHA solving. The browser just has to run the challenge script. This handler navigates, waits, and extracts the resulting session cookies. Supports periodic re-solving when tokens expire.

```python
from tor_pipeline import ChallengeHandler

handler = ChallengeHandler(target_url="https://target.example.com")
handler.solve(driver)                  # wait through challenge
cookies = handler.get_cookies(driver)  # extract session cookies

for item in items:
    if handler.needs_refresh():        # check before each operation
        handler.solve(driver)
        cookies = handler.get_cookies(driver)
    handler.tick(driver)               # increment op counter
    scrape(item, cookies=cookies)
```

### `NetworkInterceptor`

Captures API responses by injecting JavaScript that monkey-patches `fetch()` and `XMLHttpRequest`. You specify a URL pattern to watch for; when a matching response arrives, it's stored on `window` where Python can retrieve it.

This is how you get structured JSON from SPAs without parsing HTML. The frontend makes an API call, the interceptor captures the response before React/Vue/Angular renders it, and you get clean data.

```python
from tor_pipeline import NetworkInterceptor

interceptor = NetworkInterceptor(driver)
interceptor.watch("api/v1/details", key="details")

# trigger the API call (click a button, submit a form, etc.)
interceptor.click_with_events('[title="View"]')

data = interceptor.wait_for("details", timeout=30)
```

The `click_with_events` method dispatches the full `mousedown > mouseup > click` sequence. Some SPAs don't respond to bare `.click()` calls because they listen for the complete mouse event chain.

### `WorkerPool`

Launches N parallel worker processes, each running the same script with a different `--worker` argument. Handles staggered startup and per-worker log files.

```python
from tor_pipeline import WorkerPool
from tor_pipeline.workers import PoolConfig, distribute_work

# split 70,000 items across 10 workers
batches = distribute_work(items, num_workers=10)

pool = WorkerPool(
    script="my_scraper.py",
    command="scrape",
    config=PoolConfig(num_workers=10, start_delay=2.0),
)
pool.launch()
```

### `Checkpoint`

Persistent progress tracker. Records which items succeeded and which failed (with reasons). Uses atomic writes (temp file + rename) so a kill -9 during save won't corrupt the file. Can also scan the output directory for completed work that isn't in the checkpoint yet.

```python
from tor_pipeline import Checkpoint

cp = Checkpoint("progress/worker_1.json")
cp.load()
cp.merge_from_disk("output/", extension=".json")  # recover from stale checkpoint

for item in items:
    if cp.is_done(item.id):
        continue
    try:
        result = scrape(item)
        save(result)
        cp.mark_done(item.id)
    except Exception as e:
        cp.mark_failed(item.id, str(e))
    cp.save()

print(cp.summary())
# processed: 43210
# failed:    47
#   - timeout: 31
#   - not_found: 16
```

## Setup

### Prerequisites

- Python 3.10+
- Tor daemon running with control port enabled
- Chrome/Chromium (ChromeDriver is auto-managed by Selenium 4)

### Install

```bash
pip install -e .
```

This installs `selenium`, `requests[socks]` (includes PySocks for SOCKS proxy support), and `stem`.

### Tor configuration

Enable the control port in your `torrc`:

```
ControlPort 9051
CookieAuthentication 1
```

If you set a password (`HashedControlPassword` in torrc), export it so tor-pipeline can authenticate:

```bash
export TOR_CONTROL_PASSWORD=mypass
```

`TorConfig` reads this environment variable automatically. You can also pass it explicitly:

```python
from tor_pipeline.tor import TorConfig, TorManager

tor = TorManager(config=TorConfig(control_password="mypass"))
```

Start or restart Tor:

```bash
# Linux
sudo systemctl restart tor

# macOS
brew services restart tor

# Docker (see below)
docker compose up -d tor
```

Verify:

```bash
curl --socks5 127.0.0.1:9050 https://api.ipify.org
```

### Docker

The included `docker-compose.yml` runs Tor with the control port pre-configured:

```bash
docker compose up -d
```

The Tor control password defaults to `tor-pipeline`. To use a custom password, set `TOR_CONTROL_PASSWORD` before starting:

```bash
export TOR_CONTROL_PASSWORD=my_secret
docker compose up -d
```

Both the Docker container and `TorConfig` read from the same environment variable, so circuit renewal works without additional configuration.

## Examples

```
examples/
├── quickstart.py        # works immediately, no Tor needed
├── basic_scrape.py      # single-threaded template with Tor
└── parallel_scrape.py   # multi-worker template with Tor
```

`quickstart.py` is runnable as-is. The other two are templates: update the target URL, API endpoint, payload, and CSS selectors at the top of each file for your target site.

## Project structure

```
tor_pipeline/
├── __init__.py          # public API
├── tor.py               # Tor circuit management
├── proxy.py             # proxy chain rotation
├── browser.py           # stealth Selenium driver
├── challenge.py         # Cloudflare Turnstile bypass
├── interceptor.py       # fetch/XHR response capture
├── workers.py           # parallel process pool
└── checkpoint.py        # progress tracking + resume
```

## Design decisions

**Why Selenium instead of Playwright?** Turnstile's bot detection is tuned against Playwright's CDP-based architecture. Selenium's WebDriver protocol has fewer detectable fingerprints out of the box. The anti-detection surface is smaller.

**Why subprocesses instead of threads or asyncio?** Each worker needs its own browser instance with independent cookies, proxy state, and challenge tokens. Subprocesses give true isolation: no GIL contention, no shared state, no coordination overhead. If a worker segfaults (Chrome does this), it doesn't take down the others.

**Why monkey-patch fetch/XHR instead of CDP network interception?** CDP's `Network.getResponseBody` is detectable by some anti-bot systems. Injecting JavaScript that patches `fetch()` and `XMLHttpRequest.prototype` is invisible to detection scripts because it looks like the page's own code. The tradeoff is that it only captures JSON responses, not binary data.

**Why atomic checkpoint writes?** A `kill -9` during `json.dump()` can truncate the file. Writing to a temp file and calling `os.replace()` is atomic on POSIX: the file is either fully written or not updated at all. Losing checkpoint state on a 70K-record job is expensive.

## Limitations

- **Turnstile detection evolves.** Cloudflare updates their bot fingerprinting regularly. The anti-detection techniques here work as of early 2026. If Turnstile starts checking new browser signals, `StealthBrowser` may need updates.
- **Chrome only.** The stealth hardening and CDP commands target Chrome/Chromium. Firefox or WebKit would need different anti-detection approaches.
- **No built-in rate limiting.** The examples use `time.sleep()` between requests. If your target has specific rate-limit patterns, add your own throttling logic.
- **Shared Tor daemon.** All workers route through the same Tor SOCKS5 port. Circuit renewal via NEWNYM affects new connections from all workers. For true per-worker circuit isolation, run multiple Tor instances on different ports.

## License

MIT
