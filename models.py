from dataclasses import dataclass, field


MAX_OWNER_LETTERS = 5


@dataclass
class RecallResult:
    recall_number: str
    language: str = ""
    title: str = ""
    failed: bool = False
    codes: str = ""
    heures: str = ""
    parts_required: str = ""
    service_part_numbers: str = ""
    service_part_descriptions: str = ""
    bulletin_url: str = ""
    lettre_url: str = ""
    lettre_urls: list[str] = field(default_factory=list)
    bulletin_file: str = ""
    lettre_file: str = ""
    lettre_files: list[str] = field(default_factory=list)
    parts_available: str = ""
    owner_letter_parts: list[str] = field(default_factory=list)
    owner_letter_remedies: list[str] = field(default_factory=list)

    def to_sheet_row(self) -> list[str]:
        """Columns B–G: title, parts yes/no, codes, heures, bulletin url, owner letter url."""
        return [
            self.title,
            self.parts_available,
            self.codes,
            self.heures,
            self.bulletin_url,
            self.lettre_url,
        ]

    def to_csv_row(self, headers: list[str]) -> list[str]:
        row_map = {
            "recall_number": self.recall_number,
            "language": self.language,
            "title": self.title,
            "codes": self.codes,
            "heures": self.heures,
            "parts_required": self.parts_required,
            "service_part_numbers": self.service_part_numbers,
            "service_part_descriptions": self.service_part_descriptions,
            "bulletin_url": self.bulletin_url,
            "lettre_url": self.lettre_url,
            "lettre_urls": " | ".join(self.lettre_urls or ([self.lettre_url] if self.lettre_url else [])),
            "bulletin_file": self.bulletin_file,
            "lettre_file": self.lettre_file,
            "lettre_files": " | ".join(self.lettre_files or ([self.lettre_file] if self.lettre_file else [])),
            "parts_available": self.parts_available,
            "owner_letter_count": str(len(self.owner_letter_parts)),
        }

        for idx in range(1, MAX_OWNER_LETTERS + 1):
            parts = self.owner_letter_parts[idx - 1] if idx <= len(self.owner_letter_parts) else ""
            remedy = self.owner_letter_remedies[idx - 1] if idx <= len(self.owner_letter_remedies) else ""
            row_map[f"owner_letter_{idx}"] = parts
            row_map[f"owner_letter_{idx}_remedy"] = remedy

        return [row_map.get(header, "") for header in headers]
    @property
    def has_solution(self) -> bool:
        return bool(self.bulletin_url)

    def print_summary(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"  Recall: {self.recall_number}  |  Language: {self.language}")
        print(f"{'=' * 60}")
        print(f"  Title:    {self.title}")
        print(f"  Codes:    {self.codes or '(none)'}")
        print(f"  Heures:   {self.heures or '(none)'}")
        if self.parts_required:
            print(f"  Parts required (bulletin): {self.parts_required}")
        if self.service_part_numbers:
            print(f"  Service parts: {self.service_part_numbers}")
        if self.service_part_descriptions:
            print(f"  Descriptions:  {self.service_part_descriptions}")
        print(f"  Bulletin: {self.bulletin_url or '(none)'}")
        if self.lettre_urls:
            print(f"  Lettres:  {len(self.lettre_urls)} found")
            for idx, url in enumerate(self.lettre_urls, start=1):
                print(f"    [{idx}] {url}")
        else:
            print(f"  Lettre:   {self.lettre_url or '(none)'}")
        if self.parts_available:
            print(f"  Parts:    {self.parts_available} (from last owner letter)")
        if self.owner_letter_parts:
            for idx, parts in enumerate(self.owner_letter_parts, start=1):
                print(f"  Owner letter {idx} parts: {parts or '(unknown)'}")
        if self.bulletin_file:
            print(f"  Bulletin file: {self.bulletin_file}")
        if self.lettre_files:
            for idx, path in enumerate(self.lettre_files, start=1):
                print(f"  Lettre file {idx}: {path}")
        elif self.lettre_file:
            print(f"  Lettre file:   {self.lettre_file}")
        if self.failed:
            print(f"  Status:   Failed")
