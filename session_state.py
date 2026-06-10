"""Portable Playwright session export/import (works across Windows/Linux)."""

import json
import logging
from pathlib import Path

from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

PROFILE_COOKIES = ("Default/Cookies", "Default/Network/Cookies")


def profile_has_saved_cookies(profile_dir: Path) -> bool:
    return any((profile_dir / rel).exists() for rel in PROFILE_COOKIES)


def cross_platform_profile_hint(profile_dir: Path) -> str:
    if not profile_has_saved_cookies(profile_dir):
        return ""
    return (
        "\n\nNote: ford_profile/ cannot be copied between Windows and Linux — "
        "Chrome encrypts cookies per machine/OS.\n"
        "On your PC (after login):  python main.py --export-session\n"
        "Copy ford_session.json to the server, then run:  python main.py"
    )


async def apply_storage_state(context: BrowserContext, path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    if cookies:
        await context.add_cookies(cookies)
        logger.info("Loaded %d cookie(s) from %s", len(cookies), path)

    for origin_data in data.get("origins") or []:
        origin = origin_data.get("origin")
        items = origin_data.get("localStorage") or []
        if not origin or not items:
            continue
        page = await context.new_page()
        try:
            await page.goto(origin, wait_until="domcontentloaded", timeout=30_000)
            await page.evaluate(
                """(entries) => {
                    for (const [name, value] of entries) {
                        localStorage.setItem(name, value);
                    }
                }""",
                [[item["name"], item["value"]] for item in items],
            )
            logger.info("Restored localStorage for %s (%d item(s))", origin, len(items))
        except Exception as exc:
            logger.warning("Could not restore localStorage for %s: %s", origin, exc)
        finally:
            await page.close()


async def export_storage_state(context: BrowserContext, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    cookie_count = len(data.get("cookies") or [])
    origin_count = len(data.get("origins") or [])
    logger.info(
        "Exported session to %s (%d cookies, %d origins)",
        path,
        cookie_count,
        origin_count,
    )
