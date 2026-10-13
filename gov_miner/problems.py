from __future__ import annotations

import csv
import json
import os
import re
import unicodedata
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .extractor import MONEY_RE, clean_spaces, extract_record, parse_money, round_money, strip_accents


MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00e2\u20ac", "\ufffd")
MAX_SNIPPETS_PER_DOCUMENT = 14
MAX_EVIDENCE_PER_PROBLEM = 3


PROBLEM_SIGNALS = (
    "ausencia",
    "ausente",
    "falta",
    "nao ",
    "nao-",
    "nao comprov",
    "nao apresent",
    "sem ",
    "omissao",
    "irregular",
    "ilegal",
    "indevid",
    "dano",
    "glosa",
    "debito",
    "ressarc",
    "descumpr",
    "inexist",
    "intempest",
    "impropri",
    "insuficien",
    "divergenc",
    "sobrepreco",
    "superfatur",
)


TRIGGER_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"\bausenci[aa]\b",
        r"\bfalta\s+de\b",
        r"\bnao\s+(?:comprov|apresent|encaminh|junt)",
        r"\bsem\s+(?:comprov|document|nota|recibo|extrato|prestacao)",
        r"\bnota[s]?\s+fisca",
        r"\brecibo[s]?\b",
        r"\bextrato[s]?\b",
        r"\bcomprovante[s]?\b",
        r"\bdano\s+ao\s+erario\b",
        r"\bglosa\b",
        r"\bdebito\b",
        r"\blicitac",
        r"\bdispensa\b",
        r"\binexigibilidade\b",
        r"\bcontrat",
        r"\bconveni",
        r"\bprestacao\s+de\s+contas\b",
        r"\bomissao\b",
        r"\bpagamento[s]?\b",
        r"\bdespesa[s]?\b",
        r"\bempenho[s]?\b",
        r"\bliquidac",
        r"\bordem\s+bancaria\b",
        r"\bmedic(?:ao|oes)\b",
        r"\batesto\b",
        r"\btermo\s+de\s+referencia\b",
        r"\bpesquisa\s+de\s+precos\b",
        r"\bfolha\s+de\s+pagamento\b",
        r"\bdiaria[s]?\b",
        r"\bsobrepreco\b",
        r"\bsuperfatur",
    )
]


DOCUMENT_PATTERNS = [
    ("nota fiscal", r"\bnota[s]?\s+fisca"),
    ("recibo", r"\brecibo[s]?\b"),
    ("extrato bancario", r"\bextrato[s]?\b"),
    ("comprovante de pagamento", r"\bcomprovante[s]?\s+de\s+pagamento\b"),
    ("comprovacao da despesa", r"\bcomprovac"),
    ("prestacao de contas", r"\bprestacao\s+de\s+contas\b"),
    ("processo licitatorio", r"\blicitac|\bdispensa\b|\binexigibilidade\b"),
    ("contrato", r"\bcontrato\b|\bcontratual\b|\bcontratad[oa]s?\b"),
    ("convenio", r"\bconveni"),
    ("termo de fomento", r"\btermo\s+de\s+fomento\b"),
    ("adiantamento", r"\badiantamento\b"),
    ("empenho", r"\bempenho[s]?\b"),
    ("liquidacao", r"\bliquidac"),
    ("ordem bancaria", r"\bordem\s+bancaria\b"),
    ("relatorio de execucao", r"\brelatorio\b|\bexecuc"),
    ("medicao/atesto", r"\bmedic(?:ao|oes)\b|\batesto\b"),
    ("termo de referencia", r"\btermo\s+de\s+referencia\b"),
    ("pesquisa de precos", r"\bpesquisa\s+de\s+precos\b|\bcotac"),
    ("folha de pagamento", r"\bfolha\s+de\s+pagamento\b"),
]


PROBLEM_CSV_FIELDS = [
    "_source_file",
    "_chunk_index",
    "numero_documento",
    "tipo_processo",
    "orgao_auditado",
    "municipio",
    "secretaria",
    "gestor_principal",
    "valor_total_multas_aplicadas",
    "problema_indice",
    "problema",
    "categoria",
    "subcategoria",
    "documentos_relacionados",
    "valor_envolvido",
    "oportunidade_solucao",
    "trechos_evidencia",
    "classificacao_llm",
]


RANKING_FIELDS = [
    "ocorrencias",
    "documentos",
    "orgao_auditado",
    "municipio",
    "secretaria",
    "tipo_processo",
    "categoria",
    "subcategoria",
    "documento_relacionado",
    "gestor_principal",
    "valor_envolvido_total",
    "valor_multa_total",
]


OPPORTUNITY_FIELDS = [
    "ocorrencias",
    "documentos",
    "categoria",
    "subcategoria",
    "documentos_relacionados",
    "oportunidade_solucao",
    "produto_servico_tr",
    "orgaos_exemplo",
    "municipios_exemplo",
    "valor_envolvido_total",
    "valor_multa_total",
]


MANUAL_REVIEW_FIELDS = [
    "status_revisao",
    "achado_confere",
    "ajuste_sugerido",
    "_source_file",
    "arquivo_pdf",
    "_chunk_index",
    "numero_documento",
    "tipo_processo",
    "orgao_auditado",
    "municipio",
    "secretaria",
    "gestor_principal",
    "categoria",
    "subcategoria",
    "documentos_relacionados",
    "valor_envolvido",
    "valor_total_multas_aplicadas",
    "problema",
    "trecho_evidencia",
    "oportunidade_solucao",
]


def _mojibake_score(value: str) -> int:
    return sum(value.count(marker) for marker in MOJIBAKE_MARKERS)


def repair_mojibake(value: str) -> str:
    current = value
    for _ in range(2):
        best = current
        best_score = _mojibake_score(current)
        for encoding in ("cp1252", "latin-1"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except UnicodeError:
                continue
            candidate_score = _mojibake_score(candidate)
            if candidate_score < best_score:
                best = candidate
                best_score = candidate_score
        if best == current:
            break
        current = best
    return current


def normalize_match_text(value: str) -> str:
    value = repair_mojibake(value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower().replace("\xa0", " ")
    return re.sub(r"\s+", " ", value)


def _clip_window(text: str, start: int, end: int, radius: int = 280) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)

    while left > 0 and text[left - 1] not in ".;:\n":
        left -= 1
    while right < len(text) and text[right] not in ".;:\n":
        right += 1

    snippet = text[left:right].strip(" \t\r\n.;:-")
    return clean_spaces(snippet)


def _is_relevant_snippet(snippet: str) -> bool:
    normalized = normalize_match_text(snippet)
    if _is_penalty_payment_snippet(normalized):
        return False
    return any(signal in normalized for signal in PROBLEM_SIGNALS)


def _is_penalty_payment_snippet(normalized_snippet: str) -> bool:
    penalty_terms = (
        "faece",
        "fundo de apoio ao exercicio do controle externo",
        "dar avulso",
        "termo de quitacao",
        "multas aplicadas pelo tce",
        "recolha o valor da multa",
        "obrigacao pecuniaria",
    )
    has_penalty_payment = any(
        term in normalized_snippet
        for term in penalty_terms
    )
    if not has_penalty_payment:
        return False
    if any(term in normalized_snippet for term in ("glosa", "alcance", "dano ao erario")):
        return False
    return "multa" in normalized_snippet or "comprovante de pagamento" in normalized_snippet


def find_evidence_snippets(text: str) -> list[str]:
    repaired = repair_mojibake(text)
    normalized = normalize_match_text(repaired)
    snippets: list[str] = []
    seen: set[str] = set()
    seen_full: list[str] = []

    matches: list[tuple[int, int]] = []
    for pattern in TRIGGER_PATTERNS:
        for match in pattern.finditer(normalized):
            matches.append((match.start(), match.end()))

    for start, end in sorted(matches):
        snippet = _clip_window(repaired, start, end)
        if not snippet or not _is_relevant_snippet(snippet):
            continue
        normalized_snippet = re.sub(r"\W+", " ", normalize_match_text(snippet)).strip()
        if any(
            len(previous) > 120
            and (previous in normalized_snippet or normalized_snippet in previous)
            for previous in seen_full
        ):
            continue
        key = normalized_snippet[:220]
        if key in seen:
            continue
        seen.add(key)
        seen_full.append(normalized_snippet)
        snippets.append(snippet)
        if len(snippets) >= MAX_SNIPPETS_PER_DOCUMENT:
            break
    return snippets


def extract_documents(snippet: str) -> list[str]:
    normalized = normalize_match_text(snippet)
    documents = [
        label
        for label, pattern in DOCUMENT_PATTERNS
        if re.search(pattern, normalized)
    ]
    return sorted(dict.fromkeys(documents))


def extract_problem_value(snippet: str) -> float | None:
    normalized = normalize_match_text(snippet)
    values: list[float] = []
    for match in MONEY_RE.finditer(snippet):
        context = normalize_match_text(snippet[max(0, match.start() - 80) : match.end() + 25])
        if any(term in context for term in ("multa", "multar", "penalidade", "sancao")):
            continue
        value = parse_money(match.group(1))
        if value is not None:
            values.append(value)
    if not values:
        return None
    if "multa" in normalized and not any(
        term in normalized
        for term in ("dano", "glosa", "debito", "despesa", "pagamento", "superfatur", "sobrepreco")
    ):
        return None
    return round_money(max(values))


def classify_problem(snippet: str, documents: list[str]) -> tuple[str, str]:
    normalized = normalize_match_text(snippet)

    if any(term in normalized for term in ("dano ao erario", "glosa", "alcance", "debito", "ressarc")):
        return "Dano ao erario e glosa", "Valor a ressarcir ou glosar"
    if any(term in normalized for term in ("admissao de pessoal", "contratacoes temporarias", "processo seletivo", "admissao de servidores")):
        return "Atos de pessoal", "Admissao ou contratacao temporaria irregular"
    if any(term in normalized for term in ("licitac", "dispensa", "inexigibilidade", "pesquisa de precos", "termo de referencia")):
        return "Licitacao e contratacao", "Falha no processo de compra"
    if any(term in normalized for term in ("contrat", "medicao", "medicoes", "atesto", "execucao")):
        return "Execucao contratual", "Falha de execucao ou fiscalizacao"
    if any(term in normalized for term in ("nota fiscal", "recibo", "extrato", "comprovante", "comprovac")):
        if any(term in normalized for term in ("ausencia", "falta", "nao apresent", "sem ")):
            return "Documentacao fiscal e comprovacao", "Documento ausente"
        return "Documentacao fiscal e comprovacao", "Despesa nao comprovada"
    if "prestacao de contas" in normalized:
        return "Prestacao de contas", "Prestacao omitida ou incompleta"
    if "omissao" in normalized:
        return "Irregularidade operacional", "Omissao de providencia ou servico"
    if "conveni" in normalized:
        return "Convenios e transferencias", "Falha em convenio ou repasse"
    if any(term in normalized for term in ("pagamento", "despesa", "empenho", "liquidac", "ordem bancaria", "diaria", "folha de pagamento")):
        return "Despesa publica", "Falha na comprovacao da despesa"
    if documents:
        return "Documentacao fiscal e comprovacao", "Documento irregular"
    return "Irregularidade operacional", "Causa raiz a revisar"


def infer_opportunity(category: str, subcategory: str, documents: list[str]) -> str:
    if category == "Prestacao de contas":
        return "Implantar rotina de prestacao de contas com checklist, prazos e evidencia anexada."
    if category == "Documentacao fiscal e comprovacao":
        return "Montar dossie digital de despesas com validacao de documentos obrigatorios."
    if category == "Licitacao e contratacao":
        return "Padronizar fase preparatoria, pesquisa de precos e conferencia do processo licitatorio."
    if category == "Execucao contratual":
        return "Controlar medicao, atesto, fiscalizacao e entrega antes do pagamento."
    if category == "Dano ao erario e glosa":
        return "Criar trilha de saneamento, ressarcimento e prevencao de glosas recorrentes."
    if category == "Convenios e transferencias":
        return "Acompanhar convenio, plano de trabalho, execucao e prestacao de contas."
    if category == "Atos de pessoal":
        return "Revisar admissoes, temporarios e justificativas de excepcional interesse publico."
    if category == "Despesa publica":
        return "Validar empenho, liquidacao, pagamento e documentos de suporte da despesa."
    if documents:
        return "Criar checklist documental e alerta de pendencias antes do envio ao controle externo."
    return "Revisar fluxo operacional e criar controle preventivo com evidencias."


def build_problem_description(category: str, subcategory: str, documents: list[str]) -> str:
    if documents:
        return f"{subcategory}: {', '.join(documents)}"
    return f"{category}: {subcategory}"


def _merge_problem(existing: dict[str, Any], problem: dict[str, Any]) -> None:
    for evidence in problem["trechos_evidencia"]:
        if evidence not in existing["trechos_evidencia"]:
            existing["trechos_evidencia"].append(evidence)
    existing["trechos_evidencia"] = existing["trechos_evidencia"][:MAX_EVIDENCE_PER_PROBLEM]

    for document in problem["documentos_relacionados"]:
        if document not in existing["documentos_relacionados"]:
            existing["documentos_relacionados"].append(document)

    current_value = existing.get("valor_envolvido")
    new_value = problem.get("valor_envolvido")
    if current_value is None or (new_value is not None and new_value > current_value):
        existing["valor_envolvido"] = new_value


def local_extract_problems(text: str) -> list[dict[str, Any]]:
    grouped: OrderedDict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = OrderedDict()
    for snippet in find_evidence_snippets(text):
        documents = extract_documents(snippet)
        category, subcategory = classify_problem(snippet, documents)
        if not documents:
            if category == "Prestacao de contas":
                documents = ["prestacao de contas"]
            elif category == "Licitacao e contratacao":
                documents = ["processo licitatorio"]
            elif category == "Execucao contratual":
                documents = ["contrato"]

        problem = {
            "problema": build_problem_description(category, subcategory, documents),
            "categoria": category,
            "subcategoria": subcategory,
            "documentos_relacionados": documents,
            "valor_envolvido": extract_problem_value(snippet),
            "trechos_evidencia": [snippet],
            "oportunidade_solucao": infer_opportunity(category, subcategory, documents),
            "classificacao_llm": None,
        }
        key = (category, subcategory, tuple(documents))
        if key in grouped:
            _merge_problem(grouped[key], problem)
        else:
            grouped[key] = problem

    return list(grouped.values())


def classify_with_optional_llm(
    problem: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        return problem

    try:
        from .llm import classify_root_cause_with_openai

        llm_data = classify_root_cause_with_openai(problem, model=model)
    except Exception as exc:  # noqa: BLE001 - pilot should keep running.
        problem["classificacao_llm"] = {"erro": str(exc)}
        return problem

    problem["classificacao_llm"] = llm_data
    for field in (
        "problema",
        "categoria",
        "subcategoria",
        "documentos_relacionados",
        "valor_envolvido",
        "oportunidade_solucao",
    ):
        value = llm_data.get(field)
        if value not in (None, "", []):
            problem[field] = value
    return problem


def _extract_municipio_from_org(org: str | None) -> str | None:
    if not org:
        return None
    repaired = repair_mojibake(org)
    match = re.search(
        r"\b(?:Prefeitura|C.mara)\s+Municipal\s+(?:de|do|da)\s+([^,.;\n]+)",
        repaired,
        flags=re.IGNORECASE,
    )
    if match:
        return _clean_municipio(match.group(1))
    match = re.search(r"\bMunicipio\s+(?:de|do|da)\s+([^,.;\n]+)", repaired, flags=re.IGNORECASE)
    return _clean_municipio(match.group(1)) if match else None


def _clean_municipio(value: str | None) -> str | None:
    value = _clean_entity_tail(value)
    if not value:
        return None
    value = re.sub(r"\s*[-\u2013]\s*[A-Z]{2,}$", "", value)
    return clean_spaces(value) or None


def _is_truncated_entity(value: str | None) -> bool:
    if not value:
        return True
    normalized = normalize_match_text(value)
    return normalized.endswith((" de", " da", " do", " dos", " das", " e", "-"))


def _preferred_org_from_text(current_org: str | None, text: str) -> str | None:
    current_org = _clean_entity_tail(current_org)
    if current_org and not _is_truncated_entity(current_org):
        return current_org

    repaired = repair_mojibake(text)
    known_patterns = [
        r"Secretaria\s+de\s+Estado\s+da\s+Educa[c\u00e7][a\u00e3]o\s+e\s+Qualidade\s+d[eo]\s+Ensino\s*[-\u2013]?\s*SEDUC",
        r"Secretaria\s+de\s+Estado\s+de\s+Cultura\s+e\s+Economia\s+Criativa\s*[-\u2013]?\s*SEC",
        r"Secretaria\s+de\s+Estado\s+de\s+Cultura\s*[-\u2013]?\s*SEC",
        r"Secretaria\s+Municipal\s+de\s+Infraestrutura\s*[-\u2013]?\s*SEMINF",
        r"Funda[c\u00e7][a\u00e3]o\s+de\s+Amparo\s+[\u00c0A\u00e0a]\s+Pesquisa\s+do\s+Estado\s+do\s+Amazonas\s*[-\u2013]?\s*FAPEAM",
        r"Prefeitura\s+Municipal\s+(?:de|do|da)\s+[A-Z\u00c0-\u00dd][^,.;\n]{2,80}",
        r"C.mara\s+Municipal\s+(?:de|do|da)\s+[A-Z\u00c0-\u00dd][^,.;\n]{2,80}",
    ]
    for pattern in known_patterns:
        match = re.search(pattern, repaired, flags=re.IGNORECASE)
        if match:
            value = _clean_entity_tail(match.group(0))
            if value:
                return value
    abbreviations = [
        (r"\bSEMINF\b", "Secretaria Municipal de Infraestrutura - SEMINF"),
        (r"\bFAPEAM\b", "Fundacao de Amparo a Pesquisa do Estado do Amazonas - FAPEAM"),
        (r"\bSEDUC\b", "Secretaria de Estado da Educacao e Qualidade de Ensino - SEDUC"),
    ]
    for pattern, label in abbreviations:
        if re.search(pattern, repaired, flags=re.IGNORECASE):
            return label
    return current_org


def _clean_entity_tail(value: str | None) -> str | None:
    if not value:
        return None
    value = repair_mojibake(value)
    value = clean_spaces(value)
    value = re.split(
        r"\s+(?:no\s+valor|na\s+qualidade|referente|relativ[oa]|com\s+fulcro|"
        r"que\s+oficie|nos?\s+termos|e\s+(?:o|a|ao)\s+Sr|e\s+(?:o|a)\s+Sra|"
        r"e\s+(?:a|o)\s+(?:APMC|Apmc|Ordem|Gr[e\u00ea]mio|Associa[c\u00e7][a\u00e3]o|Empresa|Prefeitura|C.mara|Instituto|Funda[c\u00e7][a\u00e3]o)|"
        r"e\s+Ordenador|que\s+|na\s+Prefeitura|no\s+Municipio|\(art|\bart\.?|\bno\s+\d)",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"\s*\([^)]*$", "", value)
    value = re.sub(r"\s+e\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*[-\u2013]\s*$", "", value)
    value = clean_spaces(value)
    if not value:
        return None
    normalized_value = normalize_match_text(value)
    if normalized_value.startswith("secretaria do tribunal pleno"):
        return None
    if normalized_value.startswith("fundo de apoio ao"):
        return None
    if normalized_value.startswith("instituto de estudos de protesto"):
        return None
    return value[:160]


def _clean_manager_name(value: str | None) -> str | None:
    if not value:
        return None
    value = clean_spaces(repair_mojibake(value))
    original_normalized = normalize_match_text(value)
    if any(term in original_normalized for term in ("auditor", "relator", "conselheiro", "procurador")):
        return None
    value = re.sub(r"\s+e(?:\s+de)?\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:Auditor[-\s]?Relator|Relator|Conselheiro|Procurador).*$", "", value, flags=re.IGNORECASE)
    value = clean_spaces(value)
    normalized = normalize_match_text(value)
    if any(
        term in normalized
        for term in (
            "auditor",
            "relator",
            "conselheiro",
            "procurador",
            "estadual",
            "municipal",
            "secretaria",
            "prefeitura",
            "fundacao",
            "instituto",
            "tribunal",
            "processo",
            "valor",
            "multa",
        )
    ):
        return None
    useful_words = [
        word
        for word in re.split(r"\s+", value)
        if normalize_match_text(word) not in {"de", "da", "do", "das", "dos", "e"}
    ]
    if len(useful_words) < 2:
        return None
    return value


def _extract_responsible_name(text: str) -> str | None:
    repaired = repair_mojibake(text)
    patterns = [
        r"Considerar\s+em\s+Alcance[^.;\n]{0,120}?(?:ao|\u00e0|a|o)\s+(?:Sr\.?|Sra\.?)\s+([A-Z\u00c0-\u00dd][A-Za-z\u00c0-\u017f'`\u00b4-]+(?:\s+(?:da|de|do|das|dos|e|[A-Z\u00c0-\u00dd][A-Za-z\u00c0-\u017f'`\u00b4-]+)){1,8})",
        r"responsabilidade\s+d[ao]\s+(?:Sr\.?|Sra\.?|Senhor(?:a)?)\s+([A-Z\u00c0-\u00dd][A-Za-z\u00c0-\u017f'`\u00b4-]+(?:\s+(?:da|de|do|das|dos|e|[A-Z\u00c0-\u00dd][A-Za-z\u00c0-\u017f'`\u00b4-]+)){1,8})",
        r"(?:ao|\u00e0|a|o)\s+(?:Sr\.?|Sra\.?|Senhor(?:a)?)\s+([A-Z\u00c0-\u00dd][A-Za-z\u00c0-\u017f'`\u00b4-]+(?:\s+(?:da|de|do|das|dos|e|[A-Z\u00c0-\u00dd][A-Za-z\u00c0-\u017f'`\u00b4-]+)){1,8})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, repaired):
            name = _clean_manager_name(match.group(1))
            if name:
                return name
    return None


def _extract_secretaria(org: str | None, text: str) -> str | None:
    combined = f"{org or ''}\n{text[:5000]}"
    normalized = normalize_match_text(combined)
    normalized_org = normalize_match_text(org or "")
    secretarias = [
        ("Saude", ("saude", "fundo municipal de saude")),
        ("Educacao", ("educacao", "fundeb", "ensino")),
        ("Assistencia social", ("assistencia social", "acao social")),
        ("Obras/infraestrutura", ("obras", "infraestrutura", "servicos urbanos")),
        ("Administracao", ("administracao",)),
        ("Financas", ("financas", "fazenda")),
        ("Meio ambiente", ("meio ambiente",)),
        ("Transporte", ("transporte", "mobilidade")),
        ("Cultura/turismo", ("cultura", "turismo")),
    ]
    for label, needles in secretarias:
        if any(needle in normalized for needle in needles):
            return label
    if org and "secretaria" in normalized_org:
        return repair_mojibake(org)
    return None


def extract_problem_record(
    text: str,
    source_file: str | None = None,
    chunk_index: int | None = None,
    use_llm: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    base_record = extract_record(text, source_file=source_file, chunk_index=chunk_index).to_dict(
        include_meta=True
    )
    problems = local_extract_problems(text)
    if use_llm:
        problems = [classify_with_optional_llm(problem, model=model) for problem in problems]

    org = _preferred_org_from_text(base_record.get("orgao_auditado"), text)
    base_record["orgao_auditado"] = org
    managers = base_record.get("gestores_penalizados") or []
    gestor_principal = None
    responsible_from_text = _extract_responsible_name(text)
    if responsible_from_text:
        gestor_principal = responsible_from_text
    for manager in managers:
        if gestor_principal:
            break
        if isinstance(manager, dict):
            gestor_principal = _clean_manager_name(manager.get("nome"))
            if gestor_principal:
                break

    base_record.update(
        {
            "municipio": _extract_municipio_from_org(org),
            "secretaria": _extract_secretaria(org, text),
            "gestor_principal": gestor_principal,
            "problemas_detectados": problems,
            "classificacao_llm": None if not use_llm or not os.environ.get("OPENAI_API_KEY") else "por_problema",
        }
    )
    return base_record


def flatten_problem_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        base = {
            "_source_file": record.get("_source_file"),
            "_chunk_index": record.get("_chunk_index"),
            "numero_documento": record.get("numero_documento"),
            "tipo_processo": record.get("tipo_processo"),
            "orgao_auditado": record.get("orgao_auditado"),
            "municipio": record.get("municipio"),
            "secretaria": record.get("secretaria"),
            "gestor_principal": record.get("gestor_principal"),
            "valor_total_multas_aplicadas": record.get("valor_total_multas_aplicadas"),
        }
        for index, problem in enumerate(record.get("problemas_detectados") or [], start=1):
            row = dict(base)
            row.update(
                {
                    "problema_indice": index,
                    "problema": problem.get("problema"),
                    "categoria": problem.get("categoria"),
                    "subcategoria": problem.get("subcategoria"),
                    "documentos_relacionados": problem.get("documentos_relacionados") or [],
                    "valor_envolvido": problem.get("valor_envolvido"),
                    "oportunidade_solucao": problem.get("oportunidade_solucao"),
                    "trechos_evidencia": problem.get("trechos_evidencia") or [],
                    "classificacao_llm": problem.get("classificacao_llm"),
                }
            )
            rows.append(row)
    return rows


def _document_id(row: dict[str, Any]) -> str:
    return f"{row.get('_source_file') or ''}#{row.get('_chunk_index') or ''}"


def build_problem_rankings(problem_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in problem_rows:
        documents = row.get("documentos_relacionados") or [None]
        if isinstance(documents, str):
            documents = [documents]
        for document in documents:
            key = (
                row.get("orgao_auditado"),
                row.get("municipio"),
                row.get("secretaria"),
                row.get("tipo_processo"),
                row.get("categoria"),
                row.get("subcategoria"),
                document,
                row.get("gestor_principal"),
            )
            entry = grouped.setdefault(
                key,
                {
                    "ocorrencias": 0,
                    "_documentos": set(),
                    "orgao_auditado": row.get("orgao_auditado"),
                    "municipio": row.get("municipio"),
                    "secretaria": row.get("secretaria"),
                    "tipo_processo": row.get("tipo_processo"),
                    "categoria": row.get("categoria"),
                    "subcategoria": row.get("subcategoria"),
                    "documento_relacionado": document,
                    "gestor_principal": row.get("gestor_principal"),
                    "valor_envolvido_total": 0.0,
                    "valor_multa_total": 0.0,
                },
            )
            entry["ocorrencias"] += 1
            entry["_documentos"].add(_document_id(row))
            entry["valor_envolvido_total"] += float(row.get("valor_envolvido") or 0)
            entry["valor_multa_total"] += float(row.get("valor_total_multas_aplicadas") or 0)

    rankings = []
    for entry in grouped.values():
        entry = dict(entry)
        entry["documentos"] = len(entry.pop("_documentos"))
        entry["valor_envolvido_total"] = round_money(entry["valor_envolvido_total"])
        entry["valor_multa_total"] = round_money(entry["valor_multa_total"])
        rankings.append(entry)

    return sorted(
        rankings,
        key=lambda item: (
            item["ocorrencias"],
            item.get("valor_envolvido_total") or 0,
            item.get("valor_multa_total") or 0,
        ),
        reverse=True,
    )


def _produto_servico_tr(category: str | None, subcategory: str | None) -> str:
    if category == "Prestacao de contas":
        return "TR para sistema/servico de prestacao de contas e gestao de evidencias."
    if category == "Documentacao fiscal e comprovacao":
        return "TR para dossie digital de despesa, checklist documental e validacao fiscal."
    if category == "Licitacao e contratacao":
        return "TR para consultoria ou plataforma de governanca de compras publicas."
    if category == "Execucao contratual":
        return "TR para fiscalizacao contratual, medicao, atesto e controle de entregas."
    if category == "Dano ao erario e glosa":
        return "TR para auditoria preventiva, saneamento de achados e plano de ressarcimento."
    if category == "Convenios e transferencias":
        return "TR para gestao de convenios, plano de trabalho e prestacao de contas."
    if category == "Atos de pessoal":
        return "TR para auditoria de atos de pessoal, temporarios e admissoes."
    if category == "Despesa publica":
        return "TR para controle de empenho, liquidacao, pagamento e arquivo de suporte."
    return "TR para diagnostico de conformidade e plano de controle interno."


def build_opportunities(problem_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in problem_rows:
        documents = row.get("documentos_relacionados") or []
        if isinstance(documents, str):
            documents = [documents]
        document_key = "; ".join(documents)
        key = (
            row.get("categoria"),
            row.get("subcategoria"),
            document_key,
            row.get("oportunidade_solucao"),
        )
        entry = grouped.setdefault(
            key,
            {
                "ocorrencias": 0,
                "_documentos": set(),
                "categoria": row.get("categoria"),
                "subcategoria": row.get("subcategoria"),
                "documentos_relacionados": document_key,
                "oportunidade_solucao": row.get("oportunidade_solucao"),
                "produto_servico_tr": _produto_servico_tr(row.get("categoria"), row.get("subcategoria")),
                "_orgaos": defaultdict(int),
                "_municipios": defaultdict(int),
                "valor_envolvido_total": 0.0,
                "valor_multa_total": 0.0,
            },
        )
        entry["ocorrencias"] += 1
        entry["_documentos"].add(_document_id(row))
        if row.get("orgao_auditado"):
            entry["_orgaos"][row["orgao_auditado"]] += 1
        if row.get("municipio"):
            entry["_municipios"][row["municipio"]] += 1
        entry["valor_envolvido_total"] += float(row.get("valor_envolvido") or 0)
        entry["valor_multa_total"] += float(row.get("valor_total_multas_aplicadas") or 0)

    opportunities = []
    for entry in grouped.values():
        entry = dict(entry)
        entry["documentos"] = len(entry.pop("_documentos"))
        orgaos = entry.pop("_orgaos")
        municipios = entry.pop("_municipios")
        entry["orgaos_exemplo"] = "; ".join(
            item for item, _ in sorted(orgaos.items(), key=lambda value: value[1], reverse=True)[:5]
        )
        entry["municipios_exemplo"] = "; ".join(
            item for item, _ in sorted(municipios.items(), key=lambda value: value[1], reverse=True)[:5]
        )
        entry["valor_envolvido_total"] = round_money(entry["valor_envolvido_total"])
        entry["valor_multa_total"] = round_money(entry["valor_multa_total"])
        opportunities.append(entry)

    return sorted(
        opportunities,
        key=lambda item: (
            item["ocorrencias"],
            item.get("valor_envolvido_total") or 0,
            item.get("valor_multa_total") or 0,
        ),
        reverse=True,
    )


def select_manual_review_rows(
    problem_rows: Iterable[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = list(problem_rows)

    def priority(row: dict[str, Any]) -> tuple[float, float, int, str]:
        commercial_category_score = {
            "Dano ao erario e glosa": 6,
            "Documentacao fiscal e comprovacao": 5,
            "Convenios e transferencias": 4,
            "Licitacao e contratacao": 3,
            "Execucao contratual": 2,
            "Atos de pessoal": 1,
        }.get(row.get("categoria"), 0)
        return (
            float(row.get("valor_envolvido") or 0),
            float(row.get("valor_total_multas_aplicadas") or 0),
            commercial_category_score,
            str(row.get("numero_documento") or ""),
        )

    selected: list[dict[str, Any]] = []
    seen_documents: set[str] = set()
    for row in sorted(rows, key=priority, reverse=True):
        document_key = str(row.get("_source_file") or _document_id(row))
        if document_key in seen_documents:
            continue
        seen_documents.add(document_key)
        evidence = row.get("trechos_evidencia") or []
        if isinstance(evidence, str):
            first_evidence = evidence
        else:
            first_evidence = evidence[0] if evidence else None
        source_file = row.get("_source_file")
        selected.append(
            {
                "status_revisao": "",
                "achado_confere": "",
                "ajuste_sugerido": "",
                "_source_file": source_file,
                "arquivo_pdf": str(Path(source_file).resolve()) if source_file else None,
                "_chunk_index": row.get("_chunk_index"),
                "numero_documento": row.get("numero_documento"),
                "tipo_processo": row.get("tipo_processo"),
                "orgao_auditado": row.get("orgao_auditado"),
                "municipio": row.get("municipio"),
                "secretaria": row.get("secretaria"),
                "gestor_principal": row.get("gestor_principal"),
                "categoria": row.get("categoria"),
                "subcategoria": row.get("subcategoria"),
                "documentos_relacionados": row.get("documentos_relacionados") or [],
                "valor_envolvido": row.get("valor_envolvido"),
                "valor_total_multas_aplicadas": row.get("valor_total_multas_aplicadas"),
                "problema": row.get("problema"),
                "trecho_evidencia": first_evidence,
                "oportunidade_solucao": row.get("oportunidade_solucao"),
            }
        )
        if len(selected) >= limit:
            break

    return selected


def _csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def write_rows_csv(rows: Iterable[dict[str, Any]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})


def write_problem_csv(problem_rows: Iterable[dict[str, Any]], path: Path) -> None:
    write_rows_csv(problem_rows, path, PROBLEM_CSV_FIELDS)


def write_rankings_csv(rankings: Iterable[dict[str, Any]], path: Path) -> None:
    write_rows_csv(rankings, path, RANKING_FIELDS)


def write_opportunities_csv(opportunities: Iterable[dict[str, Any]], path: Path) -> None:
    write_rows_csv(opportunities, path, OPPORTUNITY_FIELDS)


def write_manual_review_csv(rows: Iterable[dict[str, Any]], path: Path) -> None:
    write_rows_csv(rows, path, MANUAL_REVIEW_FIELDS)
