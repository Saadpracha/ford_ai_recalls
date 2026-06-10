import csv
import io
import logging
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from config import (
    COL_CODES,
    COL_HEURES,
    COL_PARTS_AVAILABLE,
    COL_RECALL,
    COL_TITLE,
    GOOGLE_CREDENTIALS_PATH,
    SHEET_ID,
    SHEET_NAME,
)
from models import RecallResult

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


class SheetsClient:
    def __init__(self, sheet_id: str = SHEET_ID, sheet_name: str = SHEET_NAME):
        if not sheet_id:
            raise ValueError("SHEET_ID is not set. Copy .env.example to .env and configure it.")

        creds = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
        )
        self._gc = gspread.authorize(creds)
        self._sheet = self._gc.open_by_key(sheet_id).worksheet(sheet_name)

    def read_recalls(self, min_len: int = 4, max_len: int = 8) -> list[tuple[int, str]]:
        """
        Return list of (row_number, recall_number) for rows needing processing.
        Row numbers are 1-based (sheet row index).
        Skips rows that already have a title in column B.
        """
        all_values = self._sheet.get_all_values()
        recalls: list[tuple[int, str]] = []

        for idx, row in enumerate(all_values):
            if idx == 0:
                continue

            row_num = idx + 1
            recall = row[COL_RECALL].strip() if len(row) > COL_RECALL else ""

            if not recall or len(recall) < min_len or len(recall) > max_len:
                continue

            existing_title = row[COL_TITLE].strip() if len(row) > COL_TITLE else ""
            if existing_title:
                continue

            recalls.append((row_num, recall))

        logger.info("Found %d recalls to process", len(recalls))
        return recalls

    def write_row(self, row_num: int, result: RecallResult) -> None:
        """Write columns B through G for the given row."""
        values = result.to_sheet_row()
        cell_range = f"B{row_num}:G{row_num}"
        self._sheet.update(
            range_name=cell_range,
            values=[values],
            value_input_option="USER_ENTERED",
        )
        logger.info("Wrote row %d for recall %s", row_num, result.recall_number)

    def verify_row(self, row_num: int) -> dict:
        """Lightweight verify: title and core extracted columns populated."""
        row = self._sheet.row_values(row_num)
        title = row[COL_TITLE].strip() if len(row) > COL_TITLE else ""
        parts = row[COL_PARTS_AVAILABLE].strip() if len(row) > COL_PARTS_AVAILABLE else ""
        codes = row[COL_CODES].strip() if len(row) > COL_CODES else ""
        heures = row[COL_HEURES].strip() if len(row) > COL_HEURES else ""

        ok = len(title) > 5
        if parts in ("yes", "no"):
            ok = ok and True
        elif codes or heures:
            ok = ok and (len(codes) > 2 or len(heures) > 2)
        else:
            ok = False

        return {"ok": ok, "parts_available": parts, "title": title}


def read_recalls_public_csv(sheet_id: str) -> list[str]:
    """
    Fallback: read recall numbers from public CSV export (read-only, no auth).
    Does not filter already-processed rows.
    """
    import urllib.request

    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
    with urllib.request.urlopen(url) as resp:
        text = resp.read().decode("utf-8")

    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    recalls = []
    for row in reader:
        if not row:
            continue
        recall = row[0].strip().strip('"')
        if 4 <= len(recall) <= 8:
            recalls.append(recall)
    return recalls
