import logging
import re
from urllib.parse import urljoin

from playwright.async_api import BrowserContext, Frame, Page, Error as PlaywrightError

from config import DOWNLOADS_DIR, FORD_BASE_URL, FORD_COUNTRY, FORD_DSPS_URL, FORD_RECALL_URL
from models import RecallResult
from pdf_parser import (
    download_and_save_pdf,
    download_pdf,
    parse_bulletin_pdf_from_path,
    parse_owner_letter_pdf,
    pdf_save_path,
    save_pdf_bytes,
)

logger = logging.getLogger(__name__)

LOGIN_PATTERNS = ("login", "adfs", "microsoftonline", "signin")

BLOCKED_TITLE_PATTERNS = ("access denied",)
BLOCKED_BODY_PATTERNS = (
    "access denied",
    "you don't have permission",
    "errors.edgesuite.net",
)

PORTAL_DOMAINS = (
    "fordtechservice.dealerconnection.com",
    "dsps.dealerconnection.com",
)

BULLETIN_LABELS = {
    "FR-CA": ("Bulletin du concessionnaire", "Bulletin"),
    "EN-US": ("Dealer Bulletin", "Service Bulletin", "Bulletin"),
}

LETTRE_LABELS = {
    "FR-CA": ("Lettre", "lettre"),
    "EN-US": ("Letter", "letter", "Customer Letter", "Owner Letter"),
}


def recall_page_url(recall_number: str, language: str) -> str:
    return (
        f"{FORD_RECALL_URL}/{recall_number}"
        f"?country={FORD_COUNTRY}&language={language}"
    )


def is_login_url(url: str) -> bool:
    lower = url.lower()
    return any(p in lower for p in LOGIN_PATTERNS)


def is_portal_logged_in(url: str) -> bool:
    lower = url.lower()
    return any(d in lower for d in PORTAL_DOMAINS) and not is_login_url(url)


def is_logged_in(url: str) -> bool:
    return "fordtechservice.dealerconnection.com" in url.lower() and not is_login_url(url)


async def _wait_for_page_settle(page: Page) -> None:
    """Wait for redirects/SSO to finish before reading page content."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=20_000)
    except PlaywrightError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightError:
        pass


async def is_page_blocked(page: Page) -> bool:
    """Return True if Akamai/WAF blocked the page. Tolerates mid-redirect reads."""
    await _wait_for_page_settle(page)

    for _ in range(3):
        try:
            title = (await page.title()).lower()
            if any(p in title for p in BLOCKED_TITLE_PATTERNS):
                return True
            body = (await page.locator("body").inner_text(timeout=3000)).lower()
            return any(p in body for p in BLOCKED_BODY_PATTERNS)
        except PlaywrightError as exc:
            msg = str(exc).lower()
            if "execution context was destroyed" in msg or "navigation" in msg:
                await page.wait_for_timeout(1500)
                continue
            logger.debug("Blocked check skipped: %s", exc)
            return False
        except Exception as exc:
            logger.debug("Blocked check skipped: %s", exc)
            return False
    return False


async def is_portal_ready(page: Page) -> bool:
    if await is_page_blocked(page):
        return False
    return is_portal_logged_in(page.url)


async def is_ford_ready(page: Page) -> bool:
    if await is_page_blocked(page):
        return False
    return is_logged_in(page.url)


async def _wait_for_manual_login(page: Page, portal_name: str) -> None:
    print("\n" + "=" * 60)
    print(f"  LOGIN REQUIRED — {portal_name}")
    print("  Complete SSO and 2FA in the browser window.")
    print("  The browser stays open; do not close it.")
    print(f"  When you see the {portal_name} portal, press ENTER here.")
    print("=" * 60 + "\n")
    input("Press ENTER after login is complete... ")


async def ensure_logged_in(context: BrowserContext, reuse_session: bool = True) -> Page:
    """
    If reuse_session and cookies are valid, go straight to Ford TechService.
    Otherwise: DSPS login (tab 1) then verify Ford TechService (tab 2).
    """
    page = context.pages[0] if context.pages else await context.new_page()

    if reuse_session:
        await page.goto(FORD_BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        await _wait_for_page_settle(page)
        if await is_ford_ready(page):
            logger.info("Existing session reused. URL: %s", page.url)
            print(f"\nSession reused (no re-login): {page.url}\n")
            return page
        logger.info("Saved session expired or missing — starting login flow")

    login_page = page
    await login_page.goto(FORD_DSPS_URL, wait_until="domcontentloaded", timeout=60_000)
    await _wait_for_page_settle(login_page)

    # SSO redirect to Microsoft login is expected — not a block
    if is_login_url(login_page.url):
        await _wait_for_manual_login(login_page, "DSPS DealerConnection")
        await login_page.goto(FORD_DSPS_URL, wait_until="domcontentloaded", timeout=60_000)
        await _wait_for_page_settle(login_page)

    if is_portal_logged_in(login_page.url) and await is_page_blocked(login_page):
        raise RuntimeError(
            "DSPS blocked this connection (Access Denied).\n"
            "Ford's CDN detects automated browsers — restart with a fresh profile:\n"
            "  1. Delete ford_profile/ folder\n"
            "  2. python main.py --login-only\n"
            "If it persists, try: python main.py --no-proxy  or  python main.py --proxy-index 1"
        )

    if not await is_portal_ready(login_page):
        await _wait_for_manual_login(login_page, "DSPS DealerConnection")
        await login_page.goto(FORD_DSPS_URL, wait_until="domcontentloaded", timeout=60_000)
        await _wait_for_page_settle(login_page)
        if is_portal_logged_in(login_page.url) and await is_page_blocked(login_page):
            raise RuntimeError(
                "DSPS still blocked (Access Denied). The site rejected the browser/proxy.\n"
                "Try --no-proxy or: python main.py --proxy-index 1"
            )
        if not await is_portal_ready(login_page):
            raise RuntimeError(
                f"Still not logged in to DSPS. Current URL: {login_page.url}\n"
                "Try again: python main.py --login-only"
            )

    logger.info("DSPS session active. URL: %s", login_page.url)
    print(f"\nDSPS login OK (tab kept open): {login_page.url}\n")

    verify_page = await context.new_page()
    await verify_page.goto(FORD_BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    await _wait_for_page_settle(verify_page)

    if is_login_url(verify_page.url):
        await _wait_for_manual_login(verify_page, "Ford TechService")
        await verify_page.goto(FORD_BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        await _wait_for_page_settle(verify_page)

    if is_logged_in(verify_page.url) and await is_page_blocked(verify_page):
        raise RuntimeError(
            "Ford TechService blocked (Access Denied). Try --no-proxy or another proxy."
        )

    if not await is_ford_ready(verify_page):
        await _wait_for_manual_login(verify_page, "Ford TechService")
        await verify_page.goto(FORD_BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        await _wait_for_page_settle(verify_page)
        if is_logged_in(verify_page.url) and await is_page_blocked(verify_page):
            raise RuntimeError(
                "Ford TechService blocked (Access Denied). Try --no-proxy or another proxy."
            )
        if not await is_ford_ready(verify_page):
            raise RuntimeError(
                f"Ford TechService not accessible. Current URL: {verify_page.url}\n"
                "Session may not be shared — try logging in again."
            )

    logger.info("Ford TechService verified. URL: %s", verify_page.url)
    print(f"Ford TechService OK (new tab): {verify_page.url}\n")
    return verify_page


def _matches_label(text: str, labels: tuple[str, ...]) -> bool:
    return any(label in text for label in labels)


def _is_doc_href(href: str) -> bool:
    lower = href.lower()
    return "download" in lower or "/file/" in lower or lower.endswith(".pdf")


def _href_matches_doc(href: str, kind: str, language: str) -> bool:
    lower = href.lower()
    if not _is_doc_href(href):
        return False

    is_french = "french" in lower or "_fr_" in lower or "fr-ca" in lower
    is_english = "french" not in lower and "fr_" not in lower

    if kind == "bulletin":
        if "bulletin" not in lower:
            return False
        if language == "FR-CA":
            return is_french or not is_english
        return is_english or not is_french

    has_letter = any(k in lower for k in ("letter", "lettre", "owner_letter"))
    if not has_letter:
        return False
    if language == "FR-CA":
        return is_french or not is_english
    return is_english or not is_french


def _score_doc_link(href: str, text: str, kind: str, language: str) -> int:
    lower = href.lower()
    text_lower = text.lower()
    score = 0

    if "download" in lower or "/file/" in lower:
        score += 1
    if lower.endswith(".pdf"):
        score += 1
    if kind == "bulletin" and "bulletin" in lower:
        score += 5
    if kind == "lettre" and any(k in lower for k in ("letter", "lettre", "owner_letter")):
        score += 5

    if language == "FR-CA":
        if "french" in lower:
            score += 20
        if any(k in text_lower for k in ("bulletin", "lettre")):
            score += 3
        if "dealer bulletin" in lower and "french" not in lower:
            score -= 10
    else:
        if "french" in lower:
            score -= 15
        if any(k in text_lower for k in ("dealer bulletin", "letter", "owner letter")):
            score += 3

    if "revised" in lower and kind == "lettre":
        score += 2
    if "repair_available" in lower and kind == "lettre":
        score += 3
    if "full_dealer" in lower and kind == "bulletin":
        score += 2

    return score


async def _scan_frame_for_docs(
    frame: Frame,
    docs: dict[str, list[tuple[str, int]]],
    language: str,
) -> None:
    bulletin_labels = BULLETIN_LABELS.get(language, BULLETIN_LABELS["FR-CA"])
    lettre_labels = LETTRE_LABELS.get(language, LETTRE_LABELS["FR-CA"])

    try:
        links = await frame.query_selector_all("a")
        for link in links:
            text = (await link.inner_text()).strip()
            href = await link.get_attribute("href") or ""
            if not href:
                continue

            absolute = urljoin(frame.url, href) if href else ""

            if _matches_label(text, bulletin_labels) or _href_matches_doc(href, "bulletin", language):
                docs["bulletin"].append((absolute, _score_doc_link(href, text, "bulletin", language)))

            if _matches_label(text, lettre_labels) or _href_matches_doc(href, "lettre", language):
                docs["lettre"].append((absolute, _score_doc_link(href, text, "lettre", language)))

        for child in frame.child_frames:
            await _scan_frame_for_docs(child, docs, language)
    except Exception as exc:
        logger.debug("Frame scan skipped: %s", exc)


def _pick_best(candidates: list[tuple[str, int]]) -> str | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


def _pick_all_lettres(candidates: list[tuple[str, int]]) -> list[str]:
    """Return all unique owner letter URLs, lowest score first (last = most valuable)."""
    if not candidates:
        return []

    best_score: dict[str, int] = {}
    for url, score in candidates:
        if url not in best_score or score > best_score[url]:
            best_score[url] = score

    return sorted(best_score.keys(), key=lambda url: best_score[url])


async def find_document_links(page: Page, language: str) -> tuple[str | None, list[str]]:
    docs: dict[str, list[tuple[str, int]]] = {"bulletin": [], "lettre": []}
    await _scan_frame_for_docs(page.main_frame, docs, language)
    return _pick_best(docs["bulletin"]), _pick_all_lettres(docs["lettre"])


async def process_recall(
    context: BrowserContext,
    recall_number: str,
    language: str,
) -> RecallResult:
    """Open a new tab, extract recall data, then close the tab (browser stays open)."""
    url = recall_page_url(recall_number, language)
    logger.info("Processing recall %s [%s] — %s", recall_number, language, url)

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        await _wait_for_page_settle(page)
        await page.wait_for_timeout(2000)

        if is_login_url(page.url):
            raise RuntimeError(
                f"Session expired on recall page. URL: {page.url}"
            )

        title = re.sub(r"\s+", " ", (await page.title()).strip())

        bulletin_url, lettre_urls = await find_document_links(page, language)

        result = RecallResult(
            recall_number=recall_number,
            language=language,
            title=title,
            bulletin_url=bulletin_url or "",
            lettre_urls=lettre_urls,
            lettre_url=lettre_urls[-1] if lettre_urls else "",
        )

        if bulletin_url:
            bulletin_path = pdf_save_path(
                DOWNLOADS_DIR, recall_number, language, bulletin_url, "bulletin"
            )
            pdf_bytes = await download_pdf(context.request, bulletin_url)
            save_pdf_bytes(pdf_bytes, bulletin_path)
            result.bulletin_file = str(bulletin_path)

            bulletin_data = parse_bulletin_pdf_from_path(bulletin_path, language)
            result.codes = bulletin_data.codes
            result.heures = bulletin_data.heures
            result.parts_required = bulletin_data.parts_required
            result.service_part_numbers = bulletin_data.service_part_numbers
            result.service_part_descriptions = bulletin_data.service_part_descriptions
            logger.info(
                "Recall %s [%s]: codes=%s heures=%s parts_required=%s parts=%s",
                recall_number,
                language,
                bulletin_data.codes or "(none)",
                bulletin_data.heures or "(none)",
                bulletin_data.parts_required or "(unknown)",
                bulletin_data.service_part_numbers or "(none)",
            )
        else:
            logger.info("Recall %s [%s]: no bulletin link found", recall_number, language)

        if lettre_urls:
            letter_parts: list[str] = []
            letter_remedies: list[str] = []
            letter_files: list[str] = []

            for idx, lettre_url in enumerate(lettre_urls, start=1):
                lettre_path = pdf_save_path(
                    DOWNLOADS_DIR,
                    recall_number,
                    language,
                    lettre_url,
                    f"lettre_{idx}",
                )
                saved = await download_and_save_pdf(context.request, lettre_url, lettre_path)
                letter_files.append(str(saved))

                parts_available, remedy_text = parse_owner_letter_pdf(saved, language)
                letter_parts.append(parts_available)
                letter_remedies.append(remedy_text)
                logger.info(
                    "Recall %s [%s]: owner letter %d parts=%s",
                    recall_number,
                    language,
                    idx,
                    parts_available or "(unknown)",
                )

            result.lettre_files = letter_files
            result.lettre_file = letter_files[-1]
            result.owner_letter_parts = letter_parts
            result.owner_letter_remedies = letter_remedies
            result.parts_available = letter_parts[-1] if letter_parts else ""

        return result

    except Exception as exc:
        logger.exception("Failed recall %s [%s]", recall_number, language)
        return RecallResult(
            recall_number=recall_number,
            language=language,
            failed=True,
        )
    finally:
        try:
            await page.close()
        except Exception:
            pass
