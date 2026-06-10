"""Stealth browser launch — hides Playwright automation signals."""

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
]

# Use real Google Chrome instead of bundled Chromium when available.
BROWSER_CHANNEL = "chrome"
