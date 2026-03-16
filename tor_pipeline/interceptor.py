"""
Network interceptor: captures API responses by monkey-patching the
browser's fetch() and XMLHttpRequest at runtime.

Injects a small JavaScript shim that watches outgoing requests for
URL patterns you specify. When a matching response arrives, it's
stored on ``window`` where Python can poll for it via
``driver.execute_script``.

This is useful when the data you need is loaded via XHR/fetch after
a user interaction (button click, form submit) and you want the raw
JSON without parsing the rendered DOM.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from selenium import webdriver

log = logging.getLogger(__name__)

# JavaScript that patches fetch() and XMLHttpRequest.prototype to
# capture responses whose URL contains a target substring.
# Stores the captured JSON on window.__tp_captured[key].
_INTERCEPTOR_JS = """
(function(pattern, key) {
    if (!window.__tp_captured) window.__tp_captured = {};

    // -- fetch() --
    if (!window['__tp_fetch_' + key]) {
        var origFetch = window.fetch;
        window.fetch = function(url, opts) {
            var result = origFetch.apply(this, arguments);
            if (url && typeof url === 'string' && url.indexOf(pattern) !== -1) {
                result.then(function(resp) {
                    resp.clone().json().then(function(data) {
                        window.__tp_captured[key] = data;
                    }).catch(function(){});
                }).catch(function(){});
            }
            return result;
        };
        window['__tp_fetch_' + key] = true;
    }

    // -- XMLHttpRequest --
    if (!window['__tp_xhr_' + key]) {
        var origOpen = XMLHttpRequest.prototype.open;
        var origSend = XMLHttpRequest.prototype.send;

        XMLHttpRequest.prototype.open = function(method, url) {
            this.__tp_url = url;
            return origOpen.apply(this, arguments);
        };

        XMLHttpRequest.prototype.send = function(body) {
            var xhr = this;
            if (xhr.__tp_url && xhr.__tp_url.indexOf(pattern) !== -1) {
                xhr.addEventListener('load', function() {
                    try {
                        window.__tp_captured[key] = JSON.parse(xhr.responseText);
                    } catch(e) {}
                });
            }
            return origSend.apply(this, arguments);
        };
        window['__tp_xhr_' + key] = true;
    }
})(arguments[0], arguments[1]);
"""


class NetworkInterceptor:
    """Intercepts browser network responses matching a URL pattern.

    Usage::

        interceptor = NetworkInterceptor(driver)
        interceptor.watch("viewCompanyDetails", key="details")

        # trigger the request (e.g. click a button)
        driver.find_element(By.ID, "view-btn").click()

        data = interceptor.wait_for("details", timeout=30)
        if data:
            print(data)
    """

    def __init__(self, driver: webdriver.Chrome) -> None:
        self.driver = driver

    def watch(self, url_pattern: str, key: str | None = None) -> str:
        """Start watching for responses whose URL contains ``url_pattern``.

        Args:
            url_pattern: substring to match in the request URL
            key: storage key for the captured response (defaults to
                 ``url_pattern`` itself)

        Returns:
            The key under which the response will be stored.
        """
        key = key or url_pattern
        self.driver.execute_script(_INTERCEPTOR_JS, url_pattern, key)
        log.debug("watching for URL pattern '%s' (key=%s)", url_pattern, key)
        return key

    def poll(self, key: str) -> Any | None:
        """Check once whether a response has been captured for ``key``."""
        return self.driver.execute_script(
            "return (window.__tp_captured || {})[arguments[0]] || null;",
            key,
        )

    def wait_for(
        self,
        key: str,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> Any | None:
        """Block until a response is captured or timeout is reached.

        Returns the parsed JSON data, or None on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.poll(key)
            if data is not None:
                log.debug("captured response for key '%s'", key)
                return data
            time.sleep(poll_interval)

        log.warning("timeout waiting for response (key=%s, timeout=%.0fs)", key, timeout)
        return None

    def clear(self, key: str | None = None) -> None:
        """Clear captured data. If ``key`` is None, clears everything."""
        if key:
            self.driver.execute_script(
                "if(window.__tp_captured) delete window.__tp_captured[arguments[0]];",
                key,
            )
        else:
            self.driver.execute_script("window.__tp_captured = {};")

    def click_with_events(
        self,
        selector: str,
        parent_selector: str | None = None,
    ) -> bool:
        """Dispatch a full mouse-event sequence on an element.

        Some SPAs only respond to the complete mousedown -> mouseup -> click
        chain, not a bare ``element.click()``. This method dispatches all
        three events with proper bubbling.

        Args:
            selector: CSS selector for the target element
            parent_selector: optional CSS selector for the parent to receive
                events (falls back to closest button/anchor/parent)

        Returns:
            True if the element was found and clicked.
        """
        return self.driver.execute_script(
            """
            var el = document.querySelector(arguments[0]);
            if (!el) return false;

            var target = arguments[1]
                ? document.querySelector(arguments[1])
                : (el.closest('button') || el.closest('a') || el.parentElement || el);
            if (!target) return false;

            ['mousedown', 'mouseup', 'click'].forEach(function(evtType) {
                var evt = new MouseEvent(evtType, {
                    bubbles: true,
                    cancelable: true,
                    view: window
                });
                target.dispatchEvent(evt);
            });
            el.click();
            return true;
            """,
            selector,
            parent_selector,
        )
