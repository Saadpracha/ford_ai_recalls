import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

SHEET_ID = os.getenv("SHEET_ID", "")
GOOGLE_CREDENTIALS_PATH = os.getenv(
    "GOOGLE_CREDENTIALS_PATH", str(BASE_DIR / "credentials.json")
)
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")

FORD_COUNTRY = os.getenv("FORD_COUNTRY", "CAN")
FORD_DSPS_URL = "https://www.dsps.dealerconnection.com"
FORD_BASE_URL = "https://www.fordtechservice.dealerconnection.com"

# Input / output
INPUT_RECALLS_FILE = os.getenv(
    "INPUT_RECALLS_FILE", str(BASE_DIR / "Input_recalls_number.xlsx")
)
OUTPUT_CSV_FILE = os.getenv("OUTPUT_CSV_FILE", str(BASE_DIR / "en_ford_recalls_output.csv"))
ALL_RECALL_LANGUAGES = ["EN-US", "FR-CA"]


def _normalize_lang_key(lang: str | None) -> str:
    if lang is None:
        return ""
    key = str(lang).strip().lower()
    if key in ("none", "null", "undefined"):
        return ""
    return key


# Use RECALL_LANG only — do not fall back to system LANG (often "None" on Windows).
RECALL_LANG = _normalize_lang_key(os.getenv("RECALL_LANG", ""))


def resolve_recall_languages(lang: str | None = None) -> list[str]:
    """
    Return Ford portal language codes to scrape.
    lang=en -> EN-US only | lang=fr -> FR-CA only | empty/all -> both.
    """
    key = _normalize_lang_key(lang) or RECALL_LANG or "all"
    if key in ("en", "english", "en-us"):
        return ["EN-US"]
    if key in ("fr", "french", "fr-ca"):
        return ["FR-CA"]
    if key in ("", "all", "both"):
        return list(ALL_RECALL_LANGUAGES)
    raise ValueError(f"Invalid language '{key}'. Use en, fr, or all.")


RECALL_LANGUAGES = resolve_recall_languages()
FORD_RECALL_URL = (
    f"{FORD_BASE_URL}/vdirsnet/ApplicationServices/FieldServiceAction/Index"
)

FORD_PROFILE_DIR = os.getenv("FORD_PROFILE_DIR", str(BASE_DIR / "ford_profile"))
PROXY_SESSION_FILE = Path(FORD_PROFILE_DIR) / "proxy_session.json"
LOGS_DIR = BASE_DIR / "logs"
CHECKPOINT_FILE = BASE_DIR / "processed.json"
DOWNLOADS_DIR = BASE_DIR / "downloads"

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "25"))
RECALL_DELAY = float(os.getenv("RECALL_DELAY", "1.5"))

# Canada residential proxies (iproyal CSV: Host,Port,User,Pass)
PROXY_CSV_PATH = os.getenv("PROXY_CSV_PATH", str(BASE_DIR / "iproyal-proxies-10.csv"))
USE_PROXY = os.getenv("USE_PROXY", "true").lower() in ("1", "true", "yes")

# OpenAI — PDF text sent for labour codes and owner letter parts availability
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
USE_OPENAI = os.getenv("USE_OPENAI", "true").lower() in ("1", "true", "yes")

# Column layout (0-indexed, column A = 0):
# 0 recall number | 1 recall title | 2 parts yes/no (owner letter)
# 3 codes | 4 heures (bulletin) | 5 bulletin url | 6 owner letter url
COL_RECALL = 0
COL_TITLE = 1
COL_PARTS_AVAILABLE = 2
COL_CODES = 3
COL_HEURES = 4
COL_BULLETIN = 5
COL_LETTRE = 6
