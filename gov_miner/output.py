from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable


CSV_FIELDS = [
    "numero_documento",
    "tipo_processo",
    "orgao_auditado",
    "veredito_contas",
    "motivo_sancao",
    "valor_total_multas_aplicadas",
    "gestores_penalizados",
    "_source_file",
    "_chunk_index",
]


def write_jsonl(records: Iterable[dict[str, Any]], path: Path, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            if pretty:
                file.write(json.dumps(record, ensure_ascii=False, indent=2))
                file.write("\n")
            else:
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")


def write_csv(records: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["gestores_penalizados"] = json.dumps(
                row.get("gestores_penalizados") or [], ensure_ascii=False
            )
            writer.writerow(row)

