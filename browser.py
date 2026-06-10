"""Stealth browser launch — hides Playwright automation signals."""

import os

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
]

# Use real Google Chrome instead of bundled Chromium when available.
# Set BROWSER_CHANNEL=chromium to force bundled Chromium (omit channel).
_channel = os.getenv("BROWSER_CHANNEL", "chrome").strip().lower()
BROWSER_CHANNEL = None if _channel in ("", "chromium", "bundled") else _channel

CHROME_LINUX_INSTALL_HINT = (
    "Install Google Chrome on Linux for best session compatibility:\n"
    "  wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb\n"
    "  sudo apt install ./google-chrome-stable_current_amd64.deb\n"
    "Or: playwright install chrome"
)
