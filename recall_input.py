from pathlib import Path

import openpyxl


def load_recall_numbers(xlsx_path: Path) -> list[str]:
    if not xlsx_path.is_file():
        raise FileNotFoundError(f"Input file not found: {xlsx_path}")

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active

    recalls: list[str] = []
    for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
        if not row or row[0] is None:
            continue
        value = str(row[0]).strip()
        if not value:
            continue
        if row_idx == 0 and value.lower().replace("_", "").replace(" ", "") in (
            "recallno",
            "recallnumber",
            "recall",
        ):
            continue
        recalls.append(value.upper())

    wb.close()
    if not recalls:
        raise ValueError(f"No recall numbers found in {xlsx_path}")

    return recalls
