import json
import logging
import re
from dataclasses import dataclass

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from regex_extract import LaborRow, format_results

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


@dataclass
class BulletinExtract:
    codes: str = ""
    heures: str = ""
    parts_required: str = ""
    service_part_numbers: str = ""
    service_part_descriptions: str = ""


def _get_client() -> OpenAI | None:
    global _client
    if not OPENAI_API_KEY:
        return None
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _parse_json_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


def _format_hours(value: str) -> str:
    value = str(value).strip()
    if not value:
        return ""
    match = re.search(r"(\d+[,\.]\d+)", value)
    if not match:
        return value
    return match.group(1).replace(".", ",") + "h"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).replace("\r", " ")).strip()


def _join_parts(values: list[str]) -> str:
    return " / ".join(v for v in values if v)


BULLETIN_SYSTEM_PROMPT = """You extract data from Ford dealer recall bulletins.
Return ONLY valid JSON with this shape:
{
  "labour_rows": [
    {"code": "25C08A", "hours": "0,2h"}
  ],
  "parts_required": "yes",
  "service_parts": [
    {"part_number": "BC3Z-4234-D", "description": "SHAFT ASY – REAR AXLE RH 10.5 RG"}
  ]
}

Rules for labour_rows:
- Extract ONLY rows from the LABOUR ALLOWANCES / INDEMNITÉS DE MAIN-D'ŒUVRE table.
- Each row must have a labour operation code (e.g. 25C08A, MT25S88C) and labour time in hours.
- Format hours as decimal with comma and trailing h (e.g. 0,2h / 0,5h). Use "jusqu'à 0,8h" prefix only when the source text says so.
- If no labour table is found, return {"labour_rows": []}.
- Do not invent codes or hours not present in the document.

Rules for parts_required (PARTS REQUIREMENTS / ORDERING INFORMATION section):
- "no" when the bulletin explicitly states parts are NOT required
  (e.g. "Parts are not required to complete this repair",
  "Les pièces ne sont pas requises pour effectuer cette réparation").
- "yes" when a parts ordering table lists Service Part Number(s),
  or the section describes restricted/required part ordering.
- "" only if you truly cannot determine from the text.

Rules for service_parts:
- ONLY populate when parts_required is "yes".
- Extract rows from the parts table under PARTS REQUIREMENTS / ORDERING INFORMATION.
- Include ONLY service part number and description. Ignore Claim Quantity,
  Package Order Quantity, Number in Package, and other columns.
- Normalize part numbers exactly as shown (e.g. BC3Z-4234-D, SV4Z-19G490-B).
- Normalize descriptions to a single line with clean whitespace.
- If the same part number appears on multiple rows with different descriptions,
  include each row separately.
- Return [] when parts_required is "no" or no parts table exists.
- Do not invent parts not present in the document."""

OWNER_LETTER_SYSTEM_PROMPT = """You extract owner notification letter data from Ford recall letters.
Return ONLY valid JSON with this shape:
{
  "parts_available": "yes",
  "remedy_text": "..."
}

Rules for parts_available:
- "yes" when the letter says parts are available or parts are now available to repair the vehicle.
- "no" when the letter only authorizes inspection and determining if additional repairs are needed, meaning repair parts are NOT yet available.
- "" (empty string) only if you truly cannot determine from the text.

For remedy_text:
- Extract the full answer to "What will Ford and your dealer do?" (English)
  or "Que feront Ford et votre concessionnaire?" / equivalent (French).
- Return the remedy paragraph verbatim (normalized whitespace), not a summary.
- If that section is missing, return ""."""


def extract_bulletin_ai(text: str, language: str) -> BulletinExtract:
    """Send bulletin PDF text to OpenAI and return labour + parts data."""
    client = _get_client()
    if client is None:
        return BulletinExtract()

    lang_label = "English" if language == "EN-US" else "French"
    user_prompt = (
        f"Language: {lang_label}\n\n"
        "Extract labour allowances and parts requirements from this bulletin text:\n\n"
        f"{text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": BULLETIN_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        data = _parse_json_response(response.choices[0].message.content or "{}")

        labour_rows: list[LaborRow] = []
        seen_codes: set[str] = set()
        for item in data.get("labour_rows", []):
            code = str(item.get("code", "")).strip().upper()
            hours = _format_hours(str(item.get("hours", "")))
            if not code or not hours or code in seen_codes:
                continue
            seen_codes.add(code)
            labour_rows.append(LaborRow(code=code, hours=hours))

        codes, heures = format_results(labour_rows)

        parts_required = str(data.get("parts_required", "")).strip().lower()
        if parts_required not in ("yes", "no"):
            parts_required = ""

        part_numbers: list[str] = []
        part_descriptions: list[str] = []
        for item in data.get("service_parts", []):
            part_number = _normalize_text(item.get("part_number", ""))
            description = _normalize_text(item.get("description", ""))
            if not part_number:
                continue
            part_numbers.append(part_number)
            part_descriptions.append(description)

        if parts_required == "no":
            part_numbers = []
            part_descriptions = []

        logger.info(
            "OpenAI bulletin: labour=%d parts_required=%s service_parts=%d",
            len(labour_rows),
            parts_required or "(unknown)",
            len(part_numbers),
        )
        return BulletinExtract(
            codes=codes,
            heures=heures,
            parts_required=parts_required,
            service_part_numbers=_join_parts(part_numbers),
            service_part_descriptions=_join_parts(part_descriptions),
        )
    except Exception as exc:
        logger.warning("OpenAI bulletin extraction failed: %s", exc)
        return BulletinExtract()


def extract_bulletin_labor_ai(text: str, language: str) -> tuple[str, str]:
    """Backward-compatible wrapper returning only labour codes and hours."""
    result = extract_bulletin_ai(text, language)
    return result.codes, result.heures


def extract_owner_letter_ai(text: str, language: str) -> tuple[str, str]:
    """Send owner letter PDF text to OpenAI and return (parts_available, remedy_text)."""
    client = _get_client()
    if client is None:
        return "", ""

    lang_label = "English" if language == "EN-US" else "French"
    user_prompt = (
        f"Language: {lang_label}\n\n"
        "Analyze this owner notification letter:\n\n"
        f"{text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": OWNER_LETTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        data = _parse_json_response(response.choices[0].message.content or "{}")
        parts_available = str(data.get("parts_available", "")).strip().lower()
        remedy_text = _normalize_text(data.get("remedy_text", ""))

        if parts_available not in ("yes", "no"):
            parts_available = ""

        logger.info("OpenAI owner letter parts_available=%s", parts_available or "(unknown)")
        return parts_available, remedy_text
    except Exception as exc:
        logger.warning("OpenAI owner letter extraction failed: %s", exc)
        return "", ""
