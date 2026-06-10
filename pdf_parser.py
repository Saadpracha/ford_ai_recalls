import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import fitz
import pdfplumber
import tabula

from config import USE_OPENAI
from openai_extract import BulletinExtract, extract_bulletin_ai, extract_owner_letter_ai
from owner_letter_parser import extract_remedy_section, parse_parts_availability
from regex_extract import LaborRow, extract_labor_codes, format_results

logger = logging.getLogger(__name__)

INDEMNITY_MARKERS = ("INDEMNIT", "indemnit")
LABOUR_OP_MARKERS = ("labour operation", "opération de main", "operation de main")
LABOUR_TIME_MARKERS = ("labour time", "temps de main", "main-d'œuvre", "main-d'oeuvre")
SERVICE_PART_MARKERS = ("service part", "numéro de pièce", "numero de piece", "pièce de service")
DESCRIPTION_MARKERS = ("description",)
PARTS_NOT_REQUIRED_PATTERNS = (
    re.compile(r"parts are not required", re.IGNORECASE),
    re.compile(r"pièces ne sont pas requises", re.IGNORECASE),
    re.compile(r"pieces ne sont pas requises", re.IGNORECASE),
)


def filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1].strip()
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf" if name else "document.pdf"
    return re.sub(r'[<>:"/\\|?*]', "_", name)


def pdf_save_path(
    downloads_dir: Path,
    recall_number: str,
    language: str,
    url: str,
    doc_kind: str,
) -> Path:
    """downloads/{recall}/{language}/{kind}_{filename}.pdf"""
    lang = language.replace("-", "_")
    recall = recall_number.upper()
    filename = filename_from_url(url)
    return downloads_dir / recall / lang / f"{doc_kind}_{filename}"


def save_pdf_bytes(pdf_bytes: bytes, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf_bytes)
    logger.info("Saved PDF: %s", dest)
    return dest


def extract_labor_text(pdf_bytes: bytes) -> str:
    """Extract text from pages that contain labor/indemnity information."""
    labor_text = ""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    try:
        for page_num in range(doc.page_count):
            page = doc[page_num]
            text = page.get_text()
            if any(marker in text for marker in INDEMNITY_MARKERS):
                labor_text += text + " "
    finally:
        doc.close()

    return labor_text


def _normalize_col(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).replace("\r", " ").lower()).strip()


def _find_column(columns: list[str], markers: tuple[str, ...]) -> int | None:
    for idx, col in enumerate(columns):
        normalized = _normalize_col(col)
        if any(marker in normalized for marker in markers):
            return idx
    return None


def extract_labor_from_tables(tables: list) -> list[LaborRow]:
    """Extract labour operation codes and times from tabula table DataFrames."""
    rows: list[LaborRow] = []
    seen: set[str] = set()

    for table_df in tables:
        if table_df is None or table_df.empty:
            continue

        columns = [str(c) for c in table_df.columns]
        op_idx = _find_column(columns, LABOUR_OP_MARKERS)
        time_idx = _find_column(columns, LABOUR_TIME_MARKERS)
        if op_idx is None or time_idx is None:
            continue

        for _, row in table_df.iterrows():
            code = re.sub(r"\s+", " ", str(row.iloc[op_idx]).replace("\r", " ")).strip()
            hours_raw = re.sub(r"\s+", " ", str(row.iloc[time_idx]).replace("\r", " ")).strip()
            if not code or code.lower() in ("nan", "labour operation", "opération de main"):
                continue

            code_match = re.search(
                r"\b((?:MT)?[0-9]{2}[A-Z]{1,2}[0-9]{1,2}[A-Z0-9]{0,6})\b",
                code,
                re.IGNORECASE,
            )
            if not code_match:
                continue
            code = code_match.group(1).upper()

            time_match = re.search(r"(\d+[,\.]\d+)", hours_raw)
            if not time_match:
                continue

            if code in seen:
                continue
            seen.add(code)
            hours = time_match.group(1).replace(".", ",") + "h"
            rows.append(LaborRow(code=code, hours=hours))

    return rows


def parse_parts_required_from_text(text: str) -> str:
    for pattern in PARTS_NOT_REQUIRED_PATTERNS:
        if pattern.search(text):
            return "no"
    if re.search(
        r"parts requirements\s*/\s*ordering information",
        text,
        re.IGNORECASE,
    ) and re.search(r"service part", text, re.IGNORECASE):
        return "yes"
    return ""


def extract_service_parts_from_tables(tables: list) -> tuple[list[str], list[str]]:
    """Extract service part numbers and descriptions from tabula tables."""
    part_numbers: list[str] = []
    descriptions: list[str] = []

    for table_df in tables:
        if table_df is None or table_df.empty:
            continue

        columns = [str(c) for c in table_df.columns]
        part_idx = _find_column(columns, SERVICE_PART_MARKERS)
        desc_idx = _find_column(columns, DESCRIPTION_MARKERS)
        if part_idx is None or desc_idx is None:
            continue

        for _, row in table_df.iterrows():
            part_number = re.sub(r"\s+", " ", str(row.iloc[part_idx]).replace("\r", " ")).strip()
            description = re.sub(r"\s+", " ", str(row.iloc[desc_idx]).replace("\r", " ")).strip()
            lower_part = part_number.lower()
            if (
                not part_number
                or lower_part in ("nan", "service part number", "numéro de pièce de service")
                or not re.search(r"[A-Z0-9]-[A-Z0-9]", part_number, re.IGNORECASE)
            ):
                continue
            part_numbers.append(part_number)
            descriptions.append(description)

    return part_numbers, descriptions


def hybrid_extract(pdf_path: Path) -> dict:
    """Extract text with pdfplumber and tables with tabula-py."""
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n\n"

    tables = tabula.read_pdf(str(pdf_path), pages="all", multiple_tables=True)
    return {"text": full_text, "tables": tables}


def build_bulletin_ai_text(extracted_data: dict) -> str:
    """Combine plain text and structured tables for OpenAI input."""
    parts = [extracted_data.get("text", "")]
    tables = extracted_data.get("tables") or []
    if tables:
        parts.append("\n\n--- STRUCTURED TABLES ---\n\n")
        for i, table_df in enumerate(tables):
            parts.append(f"Table {i + 1}:\n")
            parts.append(table_df.to_string())
            parts.append("\n\n")
    return "".join(parts)


def save_bulletin_extracted_text(pdf_path: Path, extracted_data: dict) -> Path:
    """Save combined bulletin text + structured tables for reference."""
    out_path = pdf_path.with_name(pdf_path.stem + "_extracted.txt")
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(extracted_data["text"])
        fh.write("\n\n--- STRUCTURED TABLES ---\n\n")
        for i, table_df in enumerate(extracted_data["tables"]):
            fh.write(f"Table {i + 1}:\n")
            fh.write(table_df.to_string())
            fh.write("\n\n")
    logger.info("Saved bulletin extracted text: %s", out_path)
    return out_path


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract plain text from a PDF using pdfplumber."""
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n\n"
    return full_text


def parse_bulletin_pdf_from_path(pdf_path: Path, language: str = "EN-US") -> BulletinExtract:
    """Saved bulletin PDF -> labour codes, hours, and parts info."""
    result = BulletinExtract()
    try:
        extracted = hybrid_extract(pdf_path)
        save_bulletin_extracted_text(pdf_path, extracted)
        ai_text = build_bulletin_ai_text(extracted)

        if USE_OPENAI:
            ai_result = extract_bulletin_ai(ai_text, language)
            if ai_result.codes or ai_result.heures or ai_result.parts_required:
                return ai_result
            logger.info("OpenAI had no bulletin data; using local fallback for %s", pdf_path)

        rows = extract_labor_from_tables(extracted["tables"])
        if rows:
            result.codes, result.heures = format_results(rows)
        else:
            rows = extract_labor_codes(extracted["text"])
            if rows:
                logger.info("Labour table not found; used text fallback for %s", pdf_path)
                result.codes, result.heures = format_results(rows)

        result.parts_required = parse_parts_required_from_text(extracted["text"])
        if result.parts_required == "yes":
            part_numbers, descriptions = extract_service_parts_from_tables(extracted["tables"])
            result.service_part_numbers = " / ".join(part_numbers)
            result.service_part_descriptions = " / ".join(descriptions)
        return result
    except Exception as exc:
        logger.warning("Hybrid bulletin extraction failed for %s: %s", pdf_path, exc)

    pdf_bytes = pdf_path.read_bytes()
    fallback = parse_bulletin_pdf(pdf_bytes)
    result.codes, result.heures = fallback.codes, fallback.heures
    return result


def parse_bulletin_pdf(pdf_bytes: bytes) -> BulletinExtract:
    """Downloaded PDF bytes -> labour codes/hours (PyMuPDF fallback)."""
    labor_text = extract_labor_text(pdf_bytes)
    if not labor_text.strip():
        logger.warning("No indemnity/labor text found in PDF")
        return BulletinExtract()

    rows = extract_labor_codes(labor_text)
    codes, heures = format_results(rows)
    return BulletinExtract(codes=codes, heures=heures)


def parse_owner_letter_pdf(pdf_path: Path, language: str) -> tuple[str, str]:
    """
    Parse owner letter PDF -> (parts_available yes/no, remedy_section_text).
    Sends full PDF text to OpenAI first; falls back to regex if needed.
    Saves remedy text to a sidecar file for reference.
    """
    text = extract_pdf_text(pdf_path)
    parts_available = ""
    remedy_text = ""

    if USE_OPENAI:
        parts_available, remedy_text = extract_owner_letter_ai(text, language)

    if not parts_available and not remedy_text:
        remedy_text = extract_remedy_section(text, language)
        parts_available = parse_parts_availability(remedy_text, language)
        if remedy_text or parts_available:
            logger.info("Owner letter parsed with regex fallback for %s", pdf_path)

    if remedy_text:
        remedy_path = pdf_path.with_name(pdf_path.stem + "_remedy.txt")
        remedy_path.write_text(remedy_text, encoding="utf-8")
        logger.info("Saved owner letter remedy text: %s", remedy_path)

    return parts_available, remedy_text


async def download_pdf(request_context, url: str) -> bytes:
    """Download PDF using Playwright's authenticated request context."""
    response = await request_context.get(url)
    if not response.ok:
        raise RuntimeError(f"PDF download failed ({response.status}): {url}")
    return await response.body()


async def download_and_save_pdf(request_context, url: str, dest: Path) -> Path:
    """Download PDF from URL and save to disk. Returns saved file path."""
    pdf_bytes = await download_pdf(request_context, url)
    return save_pdf_bytes(pdf_bytes, dest)
