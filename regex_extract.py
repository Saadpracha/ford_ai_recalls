import re
from dataclasses import dataclass


@dataclass
class LaborRow:
    code: str
    hours: str


CODE_PATTERN = re.compile(
    r"\b((?:MT)?[0-9]{2}[A-Z]{1,2}[0-9]{1,2}[A-Z0-9]{0,6})\b"
    r"[\s\S]{0,60}?"
    r"\b(\d[,\.]\d)\b",
    re.IGNORECASE,
)


def extract_labor_codes(labor_text: str) -> list[LaborRow]:
    """Extract operation codes and hours from bulletin labor section text."""
    rows: list[LaborRow] = []
    seen: set[str] = set()

    for match in CODE_PATTERN.finditer(labor_text):
        code = match.group(1)
        if len(code) < 5 or len(code) > 10 or code in seen:
            continue

        seen.add(code)
        before = labor_text[max(0, match.start() - 25) : match.end()]
        prefix = "jusqu'à " if "jusqu" in before.lower() else ""
        hours = prefix + match.group(2).replace(".", ",") + "h"
        rows.append(LaborRow(code=code, hours=hours))

    return rows


def format_results(rows: list[LaborRow]) -> tuple[str, str]:
    if not rows:
        return "", ""
    return (
        " / ".join(r.code for r in rows),
        " / ".join(r.hours for r in rows),
    )
