# gov-miner

Coleta e extração estruturada de acórdãos do **TCE-AM** (Tribunal de Contas do Estado do Amazonas).

O projeto tem duas partes:

1. **Coletor (Node.js + Puppeteer)**: consulta a base de jurisprudência do TCE-AM por período, pagina os resultados e baixa os PDFs em lote, com concorrência e intervalos configuráveis.
2. **Minerador (Python)**: lê PDFs ou textos, separa cada acórdão e extrai os dados para **JSON/CSV** num schema fixo: número do documento, órgão auditado, veredito, motivo da sanção, valor das multas e gestores penalizados.

A extração é feita por regras locais (regex + heurísticas). Para textos difíceis, existe um modo opcional com LLM.

## Stack

Python 3 · pypdf · Node.js · Puppeteer · unittest

## Exemplo de saída

```json
{
  "numero_documento": "ACÓRDÃO Nº 2385/2025 - TCE - SEGUNDA CÂMARA",
  "tipo_processo": "Tomada de contas especial",
  "orgao_auditado": "Prefeitura Municipal de Manaus",
  "veredito_contas": "Irregular",
  "motivo_sancao": "Ausência de prestação de contas",
  "valor_total_multas_aplicadas": 13654.39,
  "gestores_penalizados": [
    { "nome": "João da Silva", "cargo": "Prefeito Municipal", "valor_multa_individual": 13654.39 }
  ]
}
```

*(dados da amostra em `samples/`, fictícios)*

## Como rodar

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate  |  Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# extrair da amostra
python -m gov_miner mine --input samples --output outputs/exemplo.jsonl --csv outputs/exemplo.csv --include-meta

# testes
python -m unittest discover -s tests
```

### Coletar PDFs do TCE-AM

```bash
npm install
DATA_INICIAL=01/01/2025 MAX_DOWNLOADS=20 node minerador.js
```

Variáveis úteis: `DATA_INICIAL`, `DATA_FINAL`, `MAX_PAGINAS`, `MAX_DOWNLOADS`, `DOWNLOAD_CONCURRENCY`, `HEADLESS`.

### Baixar a partir de uma lista de URLs

```bash
python -m gov_miner fetch-urls --urls urls.txt --output-dir data/downloads
python -m gov_miner mine --input data/downloads --output outputs/extracoes.jsonl --include-meta
```

### Análise de causa raiz

O subcomando `problemas` encontra trechos recorrentes (ausência de prestação de contas, falhas em licitação, dano ao erário, glosas...) e gera um ranking por órgão, município e categoria.

```bash
python -m gov_miner problemas --input data/downloads --limit 50
```

### Modo LLM (opcional)

```bash
export OPENAI_API_KEY=...
python -m gov_miner mine --input data/raw --llm --include-meta
```

Sem a chave, tudo roda em modo local.

## Estrutura

```
gov_miner/      pacote Python (CLI, leitura de PDF, divisão, extração, saída)
minerador.js    coletor do site de jurisprudência (Puppeteer)
samples/        acórdão de exemplo
tests/          testes do extrator
```
