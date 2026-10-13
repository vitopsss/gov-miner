from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PenalizedManager:
    nome: str | None = None
    cargo: str | None = None
    valor_multa_individual: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome": self.nome,
            "cargo": self.cargo,
            "valor_multa_individual": self.valor_multa_individual,
        }


@dataclass
class ExtractionRecord:
    numero_documento: str | None = None
    tipo_processo: str | None = None
    orgao_auditado: str | None = None
    veredito_contas: str | None = None
    motivo_sancao: str | None = None
    valor_total_multas_aplicadas: float | None = None
    gestores_penalizados: list[PenalizedManager] = field(default_factory=list)
    source_file: str | None = None
    chunk_index: int | None = None

    def to_dict(self, include_meta: bool = False) -> dict[str, Any]:
        data = {
            "numero_documento": self.numero_documento,
            "tipo_processo": self.tipo_processo,
            "orgao_auditado": self.orgao_auditado,
            "veredito_contas": self.veredito_contas,
            "motivo_sancao": self.motivo_sancao,
            "valor_total_multas_aplicadas": self.valor_total_multas_aplicadas,
            "gestores_penalizados": [
                manager.to_dict() for manager in self.gestores_penalizados
            ],
        }
        if include_meta:
            data["_source_file"] = self.source_file
            data["_chunk_index"] = self.chunk_index
        return data

