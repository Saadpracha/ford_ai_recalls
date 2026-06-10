import re


EN_SECTION = re.compile(
    r"What will Ford and your dealer do\??\s*(.*?)(?="
    r"What should you do|How long will it take|If you do not already|"
    r"Have you previously paid|What if you no longer|\Z)",
    re.IGNORECASE | re.DOTALL,
)

FR_SECTION = re.compile(
    r"(?:Que feront Ford et votre concessionnaire\??|"
    r"Quelles mesures Ford et votre concessionnaire[^?]*\??)\s*(.*?)(?="
    r"Que devez-vous faire|Combien de temps|Si vous n'avez pas déjà|"
    r"Avez-vous déjà payé|Si vous n'êtes plus|\Z)",
    re.IGNORECASE | re.DOTALL,
)

PARTS_AVAILABLE_PATTERNS = (
    re.compile(r"parts are now available", re.IGNORECASE),
    re.compile(r"parts are available", re.IGNORECASE),
    re.compile(r"pièces sont maintenant disponibles", re.IGNORECASE),
    re.compile(r"pièces sont disponibles", re.IGNORECASE),
    re.compile(r"les pièces sont maintenant disponibles", re.IGNORECASE),
    re.compile(r"les pièces sont disponibles", re.IGNORECASE),
)

PARTS_NOT_AVAILABLE_PATTERNS = (
    re.compile(r"determine if additional repairs", re.IGNORECASE),
    re.compile(r"déterminer si des réparations supplémentaires", re.IGNORECASE),
    re.compile(r"parts are not available", re.IGNORECASE),
    re.compile(r"pièces ne sont pas disponibles", re.IGNORECASE),
)


def extract_remedy_section(text: str, language: str) -> str:
    """Extract the 'What will Ford and your dealer do?' section from owner letter text."""
    pattern = EN_SECTION if language == "EN-US" else FR_SECTION
    match = pattern.search(text.replace("\r", " "))
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def parse_parts_availability(remedy_text: str, language: str) -> str:
    """
    Return 'yes' if parts are available, 'no' if not yet available, '' if unknown.
    """
    if not remedy_text.strip():
        return ""

    for pattern in PARTS_NOT_AVAILABLE_PATTERNS:
        if pattern.search(remedy_text):
            return "no"

    for pattern in PARTS_AVAILABLE_PATTERNS:
        if pattern.search(remedy_text):
            return "yes"

    lower = remedy_text.lower()
    if language == "EN-US":
        if "inspect" in lower and "parts are" not in lower:
            return "no"
    else:
        if "inspect" in lower or "inspecter" in lower:
            if "pièces" not in lower and "disponibles" not in lower:
                return "no"

    return ""
