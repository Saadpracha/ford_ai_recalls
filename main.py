"""
Ford Recall Automation
Manual login once (SSO + 2FA via DSPS), then batch-process recalls from Excel.
Same browser session across all recalls; each recall tab is closed after extraction.
Results saved incrementally to CSV (utf-8-sig) after each language is processed.
"""

import argparse
import asyncio
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from browser import BROWSER_ARGS, BROWSER_CHANNEL, STEALTH_INIT_SCRIPT
from config import (
    FORD_PROFILE_DIR,
    INPUT_RECALLS_FILE,
    LOGS_DIR,
    OUTPUT_CSV_FILE,
    PROXY_CSV_PATH,
    PROXY_SESSION_FILE,
    RECALL_DELAY,
    resolve_recall_languages,
    USE_PROXY,
    USE_VIRTUAL_DISPLAY,
)
from csv_output import CsvWriter
from ford_scraper import ensure_logged_in, process_recall
from proxies import (
    Proxy,
    has_proxy_session,
    load_proxies,
    load_proxy_session,
    pick_proxy,
    pick_random_proxy,
    save_proxy_session,
)
from recall_input import load_recall_numbers

logger = logging.getLogger(__name__)

VIRTUAL_DISPLAY_SIZE = (1400, 900)


def resolve_virtual_display(
    *,
    cli_flag: bool = False,
    env_flag: bool = USE_VIRTUAL_DISPLAY,
) -> bool:
    """Use pyvirtualdisplay on Linux servers (headless=False in a virtual X session)."""
    if cli_flag or env_flag:
        return True
    return sys.platform.startswith("linux") and not os.environ.get("DISPLAY")


@contextmanager
def virtual_display_context(enabled: bool):
    display = None
    if enabled:
        try:
            from pyvirtualdisplay import Display
        except ImportError as exc:
            raise SystemExit(
                "pyvirtualdisplay is required for virtual display mode.\n"
                "Install: pip install pyvirtualdisplay\n"
                "On Linux also install Xvfb: sudo apt install xvfb"
            ) from exc

        display = Display(visible=0, size=VIRTUAL_DISPLAY_SIZE)
        display.start()
        logger.info(
            "Virtual display started (DISPLAY=%s, size=%sx%s)",
            os.environ.get("DISPLAY"),
            VIRTUAL_DISPLAY_SIZE[0],
            VIRTUAL_DISPLAY_SIZE[1],
        )

    try:
        yield
    finally:
        if display is not None:
            display.stop()
            logger.info("Virtual display stopped")


def wait_to_close_browser(*, interactive: bool) -> None:
    if not interactive:
        return
    input("Press ENTER to close the browser... ")


def setup_logging() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"ford_{datetime.now():%Y%m%d_%H%M%S}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


def resolve_proxy(
    use_proxy: bool,
    *,
    login_only: bool = False,
    proxy_index: int | None = None,
    new_proxy: bool = False,
) -> tuple[dict[str, str] | None, Proxy | None]:
    if not use_proxy:
        if login_only:
            save_proxy_session(PROXY_SESSION_FILE, None)
        logger.info("Running without proxy")
        return None, None

    if (
        not login_only
        and not new_proxy
        and proxy_index is None
        and not has_proxy_session(PROXY_SESSION_FILE)
    ):
        raise SystemExit(
            "No saved proxy for this session.\n"
            "Run login first:  python main.py --login-only\n"
            "Then scrape:      python main.py"
        )

    proxies = load_proxies(Path(PROXY_CSV_PATH))
    proxy: Proxy | None = None

    if (
        not login_only
        and not new_proxy
        and proxy_index is None
        and has_proxy_session(PROXY_SESSION_FILE)
    ):
        saved = load_proxy_session(PROXY_SESSION_FILE)
        if saved is None:
            logger.info("Reusing saved session (direct connection, no proxy)")
            print("\nConnection: direct (saved session, no proxy)\n")
            return None, None
        proxy = saved
        logger.info("Reusing saved session proxy: %s", proxy)
        print(f"\nProxy (saved session): {proxy.host}:{proxy.port}\n")
        return proxy.to_playwright(), proxy

    if proxy_index is not None:
        proxy = pick_proxy(proxies, index=proxy_index)
        logger.info("Using fixed proxy [%s]: %s", proxy_index, proxy)
        print(f"\nProxy (fixed #{proxy_index}): {proxy.host}:{proxy.port}\n")
    else:
        proxy = pick_random_proxy(proxies)
        logger.info("Using random proxy: %s (pool size: %d)", proxy, len(proxies))
        print(f"\nProxy (random): {proxy.host}:{proxy.port}  [{len(proxies)} in pool]\n")

    if login_only:
        save_proxy_session(PROXY_SESSION_FILE, proxy)

    return proxy.to_playwright(), proxy


async def process_all_recalls(
    context,
    recalls: list[str],
    csv_writer: CsvWriter,
    languages: list[str],
) -> None:
    total = len(recalls)
    ok_count = 0
    fail_count = 0

    for idx, recall_number in enumerate(recalls, start=1):
        print(f"\n{'=' * 60}")
        print(f"  Recall {idx}/{total}: {recall_number}")
        print(f"{'=' * 60}")

        for language in languages:
            if csv_writer.is_done(recall_number, language):
                logger.info("Skipping (already in CSV): %s [%s]", recall_number, language)
                print(f"  [{language}] skipped — already extracted")
                continue

            result = None
            try:
                result = await process_recall(context, recall_number, language)
                result.print_summary()
            except Exception as exc:
                logger.exception("Unexpected error for %s [%s]", recall_number, language)
                from models import RecallResult

                result = RecallResult(
                    recall_number=recall_number,
                    language=language,
                    failed=True,
                )
                logger.error("Recall %s [%s] failed: %s", recall_number, language, exc)
            finally:
                if result is not None:
                    csv_writer.save(result)
                    if result.failed:
                        fail_count += 1
                    else:
                        ok_count += 1

        if idx < total and RECALL_DELAY > 0:
            await asyncio.sleep(RECALL_DELAY)

    print(f"\n{'=' * 60}")
    print("  BATCH SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Recalls processed: {total}")
    print(f"  Rows saved:        {ok_count + fail_count}")
    print(f"  Output CSV:        {OUTPUT_CSV_FILE}")
    print()


async def run(
    login_only: bool = False,
    headless: bool = False,
    use_proxy: bool = True,
    proxy_index: int | None = None,
    new_proxy: bool = False,
    lang: str | None = None,
    virtual_display: bool = False,
) -> None:
    languages = resolve_recall_languages(lang)
    Path(FORD_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    proxy_dict, _ = resolve_proxy(
        use_proxy,
        login_only=login_only,
        proxy_index=proxy_index,
        new_proxy=new_proxy,
    )

    use_vdisplay = resolve_virtual_display(cli_flag=virtual_display)
    browser_headless = False if use_vdisplay else headless
    interactive_close = not use_vdisplay and sys.stdin.isatty()

    if use_vdisplay and headless:
        logger.info(
            "Virtual display enabled — running browser with headless=False "
            "(ignoring --headless)"
        )

    launch_kwargs: dict = {
        "user_data_dir": FORD_PROFILE_DIR,
        "headless": browser_headless,
        "accept_downloads": True,
        "viewport": {"width": 1400, "height": 900},
        "channel": BROWSER_CHANNEL,
        "ignore_default_args": ["--enable-automation"],
        "args": BROWSER_ARGS,
    }
    if proxy_dict:
        launch_kwargs["proxy"] = proxy_dict

    with virtual_display_context(use_vdisplay):
        async with async_playwright() as p:
            context = None
            try:
                try:
                    context = await p.chromium.launch_persistent_context(**launch_kwargs)
                except Exception:
                    logger.warning(
                        "Google Chrome not found — falling back to bundled Chromium"
                    )
                    launch_kwargs.pop("channel", None)
                    context = await p.chromium.launch_persistent_context(**launch_kwargs)

                await context.add_init_script(STEALTH_INIT_SCRIPT)
                await ensure_logged_in(context, reuse_session=not login_only)

                if login_only:
                    print("Done. Session saved in:", FORD_PROFILE_DIR)
                    print("Proxy saved in:", PROXY_SESSION_FILE)
                    print("Browser tabs left open until you close the window.")
                    wait_to_close_browser(interactive=interactive_close)
                    return

                recalls = load_recall_numbers(Path(INPUT_RECALLS_FILE))
                csv_writer = CsvWriter(Path(OUTPUT_CSV_FILE))

                print(f"\nInput file:  {INPUT_RECALLS_FILE}")
                print(f"Output CSV:  {OUTPUT_CSV_FILE}")
                print(f"Recalls:     {len(recalls)}")
                print(f"Languages:   {', '.join(languages)}")
                print(f"Already done: {csv_writer.done_count} row(s)\n")

                await process_all_recalls(context, recalls, csv_writer, languages)

                wait_to_close_browser(interactive=interactive_close)
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Ford Recall Automation")
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Open browser, login via DSPS, save session + proxy, then exit",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Playwright headless mode (only after session is saved)",
    )
    parser.add_argument(
        "--virtual-display",
        action="store_true",
        help="Linux server: use pyvirtualdisplay + headless=False (needs xvfb)",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Run without Canada proxy (direct connection)",
    )
    parser.add_argument(
        "--proxy-index",
        type=int,
        default=None,
        metavar="N",
        help="Use a fixed proxy by index (0-based). Random on --login-only only.",
    )
    parser.add_argument(
        "--new-proxy",
        action="store_true",
        help="Pick a new random proxy (requires re-login: --login-only)",
    )
    parser.add_argument(
        "--lang",
        choices=["en", "fr", "all"],
        default=None,
        help="Scrape English only (en), French only (fr), or both (all). "
        "Overrides RECALL_LANG / LANG in .env.",
    )
    args = parser.parse_args()

    use_proxy = USE_PROXY and not args.no_proxy

    log_file = setup_logging()
    logger.info("Log file: %s", log_file)
    languages = resolve_recall_languages(args.lang)
    use_vdisplay = resolve_virtual_display(cli_flag=args.virtual_display)
    logger.info(
        "Input: %s | Output: %s | Proxy: %s | Languages: %s | Mode: %s | Virtual display: %s",
        INPUT_RECALLS_FILE,
        OUTPUT_CSV_FILE,
        use_proxy,
        ",".join(languages),
        "login-only" if args.login_only else "batch",
        use_vdisplay,
    )

    try:
        asyncio.run(
            run(
                login_only=args.login_only,
                headless=args.headless,
                use_proxy=use_proxy,
                proxy_index=args.proxy_index,
                new_proxy=args.new_proxy,
                lang=args.lang,
                virtual_display=args.virtual_display,
            )
        )
    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C). Partial results are saved in CSV.")


if __name__ == "__main__":
    main()
