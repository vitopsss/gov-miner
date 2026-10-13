from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


SYSTEM_PROMPT = """Voce e um agente avancado de extracao de dados juridicos do TCE.
Retorne apenas JSON valido no schema solicitado. Campos ausentes devem ser null.
Valores monetarios devem ser numeros decimais, sem R$."""

ROOT_CAUSE_PROMPT = """Voce classifica achados de acordaos do TCE para inteligencia comercial.
Use apenas os trechos enviados. Retorne JSON valido com:
problema, categoria, subcategoria, documentos_relacionados, valor_envolvido,
oportunidade_solucao.
Valores monetarios devem ser numeros decimais, sem R$.
Se nao houver informacao suficiente para um campo, use null ou lista vazia."""


def extract_with_openai(text: str, model: str | None = None) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    payload = {
        "model": model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extraia os dados do seguinte texto juridico e retorne apenas JSON:\n\n"
                    + text[:120000]
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def classify_root_cause_with_openai(problem: dict, model: str | None = None) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    payload = {
        "model": model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
        "messages": [
            {"role": "system", "content": ROOT_CAUSE_PROMPT},
            {
                "role": "user",
                "content": (
                    "Classifique este achado local. Mantenha o foco em causa raiz "
                    "e oportunidade pratica para prefeitura/secretaria:\n\n"
                    + json.dumps(problem, ensure_ascii=False)
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
