from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from .models import ExtractionRecord, PenalizedManager
from .splitter import ACORDAO_RE


MONEY_RE = re.compile(
    r"(?:R\$\s*)?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})"
)

PROCESS_TYPES = [
    "Tomada de contas especial",
    "Tomada de contas",
    "Prestacao de contas",
    "Prestação de contas",
    "Representacao",
    "Representação",
    "Denuncia",
    "Denúncia",
    "Auditoria",
    "Inspecao",
    "Inspeção",
    "Recurso",
    "Admissao de pessoal",
    "Admissão de pessoal",
    "Aposentadoria",
    "Contrato",
    "Convenio",
    "Convênio",
]

ORG_LABEL_RE = re.compile(
    r"(?im)^\s*(?:[ÓO]RG[ÃA]O(?:/ENTIDADE)?|UNIDADE\s+GESTORA|"
    r"UNIDADE\s+JURISDICIONADA|ENTIDADE|JURISDICIONADO|ORIGEM|"
    r"INTERESSAD[OA])\s*[:\-]\s*(.+)$"
)

ORG_INLINE_RE = re.compile(
    r"\b((?:Prefeitura|C[âa]mara|Secretaria|Fundo|Instituto|"
    r"Autarquia|Associa[çc][ãa]o|Funda[çc][ãa]o)(?:\s+Municipal)?"
    r"\s+(?:de|do|da|dos|das)\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][^,.;\n]{2,120})",
    re.IGNORECASE,
)

NAME_TITLE_RE = re.compile(
    r"\b(?:Sr\.?|Sra\.?|Senhor(?:a)?|Gestor(?:a)?|Respons[áa]vel|"
    r"Ordenador(?:a)?|Presidente|Prefeito(?:a)?|Secret[áa]rio(?:a)?)\s+"
    r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÁÉÍÓÚÂÊÔÃÕÇ'`´-]+"
    r"(?:\s+(?:da|de|do|das|dos|e|[A-ZÁÉÍÓÚÂÊÔÃÕÇ]"
    r"[A-Za-zÁÉÍÓÚÂÊÔÃÕÇ'`´-]+)){1,8})"
)

NAME_UPPER_RE = re.compile(
    r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,}(?:\s+(?:DA|DE|DO|DAS|DOS|E|"
    r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,})){1,8})\b"
)

CARGO_RE = re.compile(
    r"\b(Prefeito(?:a)?(?:\s+Municipal)?|Vice-Prefeito(?:a)?|"
    r"Secret[áa]rio(?:a)?(?:\s+Municipal)?(?:\s+de\s+[^,.;\n]{2,80})?|"
    r"Presidente(?:\s+da\s+[^,.;\n]{2,80})?|"
    r"Ordenador(?:a)?\s+de\s+Despesas|Gestor(?:a)?|Diretor(?:a)?|"
    r"Tesoureiro(?:a)?|Contador(?:a)?)\b",
    re.IGNORECASE,
)

STOP_NAME_WORDS = {
    "ACORDAO",
    "ACÓRDÃO",
    "TRIBUNAL",
    "CONTAS",
    "ESTADO",
    "PROCESSO",
    "RELATOR",
    "EMENTA",
    "MULTA",
    "VALOR",
    "RESPONSAVEL",
    "RESPONSÁVEL",
    "PREFEITURA",
    "SECRETARIA",
    "CAMARA",
    "CÂMARA",
}


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\n\r,.;:-")


def parse_money(value: str) -> float | None:
    cleaned = value.replace(".", "").replace(",", ".")
    try:
        return float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


def round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def normalize_output_text(value: str | None) -> str | None:
    if not value:
        return None
    value = clean_spaces(value)
    return value or None


def extract_document_number(text: str) -> str | None:
    match = ACORDAO_RE.search(text)
    if match:
        return normalize_output_text(match.group(1))

    match = re.search(
        r"(?im)^\s*((?:DECIS[ÃA]O|RESOLU[ÇC][ÃA]O|PARECER)\s*"
        r"(?:N[º°.]?|NO\.?)\s*[0-9A-Z./-]+[^\n]*)",
        text,
    )
    return normalize_output_text(match.group(1)) if match else None


def extract_process_type(text: str) -> str | None:
    ascii_text = strip_accents(text).lower()
    for process_type in PROCESS_TYPES:
        if strip_accents(process_type).lower() in ascii_text:
            return process_type.replace("Prestacao", "Prestação").replace(
                "Representacao", "Representação"
            ).replace("Denuncia", "Denúncia").replace("Inspecao", "Inspeção")

    match = re.search(
        r"(?im)^\s*(?:NATUREZA|ASSUNTO|TIPO\s+DE\s+PROCESSO)\s*[:\-]\s*(.+)$",
        text,
    )
    return normalize_output_text(match.group(1)) if match else None


def extract_org(text: str) -> str | None:
    for match in ORG_LABEL_RE.finditer(text):
        value = clean_spaces(match.group(1))
        value = re.split(r"\s{2,}|;|\|", value)[0]
        if len(value) >= 4:
            return normalize_output_text(value)

    match = ORG_INLINE_RE.search(text)
    return normalize_output_text(match.group(1)) if match else None


def extract_verdict(text: str) -> str | None:
    ascii_text = strip_accents(text).lower()

    if re.search(r"\b(considerar|julgar|declarar)[^.\n]{0,120}\bilegal", ascii_text):
        return "Ilegal"
    if "regular com ressalvas" in ascii_text:
        return "Regular com ressalvas"
    if re.search(r"\b(julgar|considerar)[^.\n]{0,120}\birregular", ascii_text):
        return "Irregular"
    if re.search(r"\bcontas\s+irregulares\b", ascii_text):
        return "Irregular"
    if re.search(r"\bcontas\s+regulares\s+com\s+ressalvas\b", ascii_text):
        return "Regular com ressalvas"
    if re.search(r"\b(julgar|considerar)[^.\n]{0,120}\bregular", ascii_text):
        return "Regular"
    return None


def extract_reason(text: str) -> str | None:
    ascii_text = re.sub(r"\s+", " ", strip_accents(text).lower())
    known_reasons = [
        ("ausencia de prestacao de contas", "Ausência de prestação de contas"),
        ("falta de prestacao de contas", "Ausência de prestação de contas"),
        ("omissao no dever de prestar contas", "Omissão no dever de prestar contas"),
        ("falta de comprovantes", "Falta de comprovantes"),
        ("nao comprovacao", "Não comprovação de despesas"),
        ("dano ao erario", "Dano ao erário"),
        ("irregularidade em licitacao", "Irregularidade em licitação"),
        ("dispensa indevida de licitacao", "Dispensa indevida de licitação"),
        ("fragmentacao de despesa", "Fragmentação de despesa"),
        ("atraso no envio", "Atraso no envio de documentos"),
        ("descumprimento", "Descumprimento de determinação legal"),
    ]
    for needle, label in known_reasons:
        if needle in ascii_text:
            return label

    reason_re = re.compile(
        r"(?is)(?:em raz[ãa]o de|em virtude de|devido [àa]|ante |"
        r"pela?|por)\s+([^.;\n]{20,220})"
    )
    for match in reason_re.finditer(text):
        candidate = clean_spaces(match.group(1))
        candidate_ascii = strip_accents(candidate).lower()
        if any(
            term in candidate_ascii
            for term in (
                "irregular",
                "ausencia",
                "falta",
                "omissao",
                "dano",
                "descumpr",
                "nao ",
                "prestacao",
                "comprov",
            )
        ):
            candidate = candidate[:1].upper() + candidate[1:]
            return normalize_output_text(candidate[:180])
    return None


def is_probable_name(value: str) -> bool:
    clean = clean_spaces(value)
    if not clean:
        return False
    words = [word for word in re.split(r"\s+", clean) if word]
    useful_words = [
        word
        for word in words
        if strip_accents(word).upper() not in {"DA", "DE", "DO", "DAS", "DOS", "E"}
    ]
    if len(useful_words) < 2:
        return False
    upper_value = strip_accents(clean).upper()
    return not any(stop == upper_value or upper_value.startswith(stop + " ") for stop in STOP_NAME_WORDS)


def clean_name(value: str) -> str | None:
    value = clean_spaces(value)
    value = re.sub(
        r"\b(?:CPF|CNPJ|RG|PROCESSO|RELATOR|MULTA|VALOR)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = clean_spaces(value)
    if not is_probable_name(value):
        return None
    if value.isupper():
        value = value.title()
        for particle in (" Da ", " De ", " Do ", " Das ", " Dos ", " E "):
            value = value.replace(particle, particle.lower())
    return value


def find_name_near(context: str, money_start: int) -> str | None:
    before = context[:money_start]
    title_matches = list(NAME_TITLE_RE.finditer(before))
    for match in reversed(title_matches):
        name = clean_name(match.group(1))
        if name:
            return name

    upper_matches = list(NAME_UPPER_RE.finditer(before))
    for match in reversed(upper_matches):
        name = clean_name(match.group(1))
        if name:
            return name
    return None


def find_cargo_near(context: str, name: str | None) -> str | None:
    if name:
        pos = strip_accents(context).lower().find(strip_accents(name).lower())
        if pos >= 0:
            window = context[max(0, pos - 100) : pos + len(name) + 160]
        else:
            window = context
    else:
        window = context

    quality_match = re.search(
        r"(?i)(?:na\s+qualidade\s+de|no\s+cargo\s+de|ent[ãa]o)\s+([^,.;\n]{3,100})",
        window,
    )
    if quality_match:
        return normalize_output_text(quality_match.group(1))

    cargo_match = CARGO_RE.search(window)
    return normalize_output_text(cargo_match.group(1)) if cargo_match else None


def contexts_with_fines(text: str) -> list[tuple[str, int, float]]:
    contexts: list[tuple[str, int, float]] = []
    ascii_text = strip_accents(text).lower()
    for match in MONEY_RE.finditer(text):
        value = parse_money(match.group(1))
        if value is None:
            continue
        start = max(0, match.start() - 350)
        end = min(len(text), match.end() + 250)
        context = text[start:end]
        context_ascii = ascii_text[start:end]
        if any(term in context_ascii for term in ("multa", "multar", "sancao", "penalidade")):
            contexts.append((context, match.start() - start, value))
    return contexts


def extract_managers_and_fines(text: str) -> tuple[list[PenalizedManager], float | None]:
    managers_by_key: OrderedDict[tuple[str | None, float | None], PenalizedManager] = OrderedDict()
    fine_values: list[float] = []

    for context, money_start, value in contexts_with_fines(text):
        value = round_money(value)
        fine_values.append(value)
        name = find_name_near(context, money_start)
        cargo = find_cargo_near(context, name)
        key = (strip_accents(name).lower() if name else None, value)
        if key not in managers_by_key:
            managers_by_key[key] = PenalizedManager(
                nome=name,
                cargo=cargo,
                valor_multa_individual=value,
            )

    managers = [
        manager
        for manager in managers_by_key.values()
        if manager.nome or manager.cargo or manager.valor_multa_individual is not None
    ]

    individual_values = [
        manager.valor_multa_individual
        for manager in managers
        if manager.valor_multa_individual is not None
    ]
    if individual_values:
        total = round_money(sum(individual_values))
    elif fine_values:
        total = round_money(sum(dict.fromkeys(fine_values)))
    else:
        total = None
    return managers, total


def extract_record(text: str, source_file: str | None = None, chunk_index: int | None = None) -> ExtractionRecord:
    managers, total_fines = extract_managers_and_fines(text)
    return ExtractionRecord(
        numero_documento=extract_document_number(text),
        tipo_processo=extract_process_type(text),
        orgao_auditado=extract_org(text),
        veredito_contas=extract_verdict(text),
        motivo_sancao=extract_reason(text),
        valor_total_multas_aplicadas=total_fines,
        gestores_penalizados=managers,
        source_file=source_file,
        chunk_index=chunk_index,
    )
