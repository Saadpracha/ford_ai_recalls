import csv
import logging
from pathlib import Path

from models import MAX_OWNER_LETTERS, RecallResult

logger = logging.getLogger(__name__)

BASE_CSV_HEADERS = [
    "recall_number",
    "language",
    "title",
    "parts_available",
    "codes",
    "heures",
    "parts_required",
    "service_part_numbers",
    "service_part_descriptions",
    "bulletin_url",
    "lettre_url",
    "lettre_urls",
    "bulletin_file",
    "lettre_file",
    "lettre_files",
    "owner_letter_count",
]

OWNER_LETTER_HEADERS = []
for idx in range(1, MAX_OWNER_LETTERS + 1):
    OWNER_LETTER_HEADERS.append(f"owner_letter_{idx}")
    OWNER_LETTER_HEADERS.append(f"owner_letter_{idx}_remedy")

CSV_HEADERS = BASE_CSV_HEADERS + OWNER_LETTER_HEADERS


def _migrate_csv(path: Path) -> None:
    """Rewrite existing CSV when headers are missing newer columns."""
    if not path.exists():
        return

    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        existing_headers = reader.fieldnames or []
        rows = list(reader)

    if existing_headers == CSV_HEADERS:
        return

    logger.info("Migrating CSV headers: %s", path)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in CSV_HEADERS})


class CsvWriter:
    """Append-only CSV writer (utf-8-sig). Saves each row immediately after extraction."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _migrate_csv(self.path)
        self._done = self._load_done_keys()
        if not self.path.exists():
            self._write_row(CSV_HEADERS)

    def _load_done_keys(self) -> set[tuple[str, str]]:
        if not self.path.exists():
            return set()
        done: set[tuple[str, str]] = set()
        with self.path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                recall = (row.get("recall_number") or "").strip().upper()
                language = (row.get("language") or "").strip()
                if recall and language:
                    done.add((recall, language))
        return done

    def _write_row(self, row: list[str]) -> None:
        with self.path.open("a", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerow(row)

    def is_done(self, recall_number: str, language: str) -> bool:
        return (recall_number.upper(), language) in self._done

    @property
    def done_count(self) -> int:
        return len(self._done)

    def save(self, result: RecallResult) -> None:
        key = (result.recall_number.upper(), result.language)
        if key in self._done:
            logger.info("Already in CSV, skipping: %s [%s]", result.recall_number, result.language)
            return
        self._write_row(result.to_csv_row(CSV_HEADERS))
        self._done.add(key)
        logger.info("Saved to CSV: %s [%s]", result.recall_number, result.language)
