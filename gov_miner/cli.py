from __future__ import annotations

import argparse
import os
import json
import sys
from pathlib import Path
from typing import Any

from .downloader import download_urls_file
from .extractor import extract_record
from .llm import extract_with_openai
from .output import write_csv, write_jsonl
from .problems import (
    build_opportunities,
    build_problem_rankings,
    extract_problem_record,
    flatten_problem_records,
    select_manual_review_rows,
    write_manual_review_csv,
    write_opportunities_csv,
    write_problem_csv,
    write_rankings_csv,
)
from .splitter import split_tce_documents
from .text_loader import iter_input_files, load_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gov-miner",
        description="Mine TCE acordaos and official journal excerpts into strict JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    mine = subparsers.add_parser("mine", help="Extract records from TXT/PDF files.")
    mine.add_argument("--input", "-i", required=True, help="Input file or directory.")
    mine.add_argument(
        "--output",
        "-o",
        default="outputs/extracoes.jsonl",
        help="JSONL output path.",
    )
    mine.add_argument("--csv", default="outputs/extracoes.csv", help="CSV output path.")
    mine.add_argument(
        "--include-meta",
        action="store_true",
        help="Include source filename and chunk index in the output.",
    )
    mine.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print each JSON object in the JSONL output.",
    )
    mine.add_argument(
        "--llm",
        action="store_true",
        help="Use OpenAI extraction instead of local heuristics. Requires OPENAI_API_KEY.",
    )

    fetch = subparsers.add_parser("fetch-urls", help="Download URLs listed in a text file.")
    fetch.add_argument("--urls", required=True, help="Text file with one URL per line.")
    fetch.add_argument(
        "--output-dir",
        default="data/downloads",
        help="Directory where downloaded files will be saved.",
    )

    problems = subparsers.add_parser(
        "problemas",
        help="Extract root-cause problems and commercial opportunities from TXT/PDF files.",
    )
    problems.add_argument("--input", "-i", required=True, help="Input file or directory.")
    problems.add_argument(
        "--output",
        "-o",
        default="outputs/problemas_piloto.jsonl",
        help="Detailed JSONL output path.",
    )
    problems.add_argument(
        "--csv",
        default="outputs/problemas_piloto.csv",
        help="Flat problem CSV output path.",
    )
    problems.add_argument(
        "--ranking",
        default="outputs/ranking_problemas.csv",
        help="Problem recurrence ranking CSV path.",
    )
    problems.add_argument(
        "--oportunidades",
        default="outputs/oportunidades_solucao.csv",
        help="Grouped solution opportunities CSV path.",
    )
    problems.add_argument(
        "--review",
        default="outputs/revisao_manual_10_acordaos.csv",
        help="Manual review CSV path with top problem samples.",
    )
    problems.add_argument(
        "--review-limit",
        type=int,
        default=10,
        help="Number of unique document/chunk samples for manual review.",
    )
    problems.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum files to process. Use 0 for all files.",
    )
    problems.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of sorted input files to skip before processing.",
    )
    problems.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print each JSON object in the JSONL output.",
    )
    problems.add_argument(
        "--llm",
        action="store_true",
        help="Optionally classify local snippets with OpenAI if OPENAI_API_KEY exists.",
    )
    problems.add_argument("--model", default=None, help="OpenAI model for optional classification.")
    return parser


def mine_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_path = Path(args.output)
    csv_path = Path(args.csv) if args.csv else None

    files = iter_input_files(input_path)
    if not files:
        print(f"No TXT/PDF files found in {input_path}", file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for path in files:
        try:
            text = load_text(path)
            chunks = split_tce_documents(text)
            for chunk in chunks:
                if args.llm:
                    data = extract_with_openai(chunk.text)
                    if args.include_meta:
                        data["_source_file"] = str(path)
                        data["_chunk_index"] = chunk.index
                else:
                    record = extract_record(
                        chunk.text,
                        source_file=str(path),
                        chunk_index=chunk.index,
                    )
                    data = record.to_dict(include_meta=args.include_meta)
                records.append(data)
        except Exception as exc:  # noqa: BLE001 - CLI should keep mining other files.
            errors.append({"file": str(path), "error": str(exc)})

    write_jsonl(records, output_path, pretty=args.pretty)
    if csv_path:
        csv_records = []
        for record in records:
            row = dict(record)
            row.setdefault("_source_file", None)
            row.setdefault("_chunk_index", None)
            csv_records.append(row)
        write_csv(csv_records, csv_path)

    error_path = output_path.with_suffix(".errors.jsonl")
    if errors:
        write_jsonl(errors, error_path)
        print(f"Completed with {len(errors)} error(s). See {error_path}", file=sys.stderr)
    else:
        write_jsonl([], error_path)

    print(f"Extracted {len(records)} record(s) from {len(files)} file(s).")
    print(f"JSONL: {output_path}")
    if csv_path:
        print(f"CSV: {csv_path}")
    return 1 if errors and not records else 0


def fetch_urls_command(args: argparse.Namespace) -> int:
    paths = download_urls_file(Path(args.urls), Path(args.output_dir))
    for path in paths:
        print(path)
    return 0


def problems_command(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_path = Path(args.output)
    csv_path = Path(args.csv) if args.csv else None
    ranking_path = Path(args.ranking) if args.ranking else None
    opportunities_path = Path(args.oportunidades) if args.oportunidades else None
    review_path = Path(args.review) if args.review else None

    files = iter_input_files(input_path)
    if not files:
        print(f"No TXT/PDF files found in {input_path}", file=sys.stderr)
        return 2

    offset = max(args.offset or 0, 0)
    limit = args.limit if args.limit is not None else 50
    selected_files = files[offset:]
    if limit > 0:
        selected_files = selected_files[:limit]

    if args.llm and not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not configured; running local mode with classificacao_llm=null.",
            file=sys.stderr,
        )

    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for path in selected_files:
        try:
            text = load_text(path)
            chunks = split_tce_documents(text)
            for chunk in chunks:
                records.append(
                    extract_problem_record(
                        chunk.text,
                        source_file=str(path),
                        chunk_index=chunk.index,
                        use_llm=args.llm,
                        model=args.model,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - pilot should keep mining other files.
            errors.append({"file": str(path), "error": str(exc)})

    problem_rows = flatten_problem_records(records)
    rankings = build_problem_rankings(problem_rows)
    opportunities = build_opportunities(problem_rows)
    review_rows = select_manual_review_rows(problem_rows, limit=max(args.review_limit or 0, 0))

    write_jsonl(records, output_path, pretty=args.pretty)
    if csv_path:
        write_problem_csv(problem_rows, csv_path)
    if ranking_path:
        write_rankings_csv(rankings, ranking_path)
    if opportunities_path:
        write_opportunities_csv(opportunities, opportunities_path)
    if review_path:
        write_manual_review_csv(review_rows, review_path)

    error_path = output_path.with_suffix(".errors.jsonl")
    if errors:
        write_jsonl(errors, error_path)
        print(f"Completed with {len(errors)} error(s). See {error_path}", file=sys.stderr)
    else:
        write_jsonl([], error_path)

    records_without_problems = sum(1 for record in records if not record.get("problemas_detectados"))
    print(
        "Extracted "
        f"{len(problem_rows)} problem(s) from {len(records)} record(s) "
        f"and {len(selected_files)} file(s)."
    )
    if records_without_problems:
        print(f"Records without relevant problem snippets: {records_without_problems}")
    print(f"JSONL: {output_path}")
    if csv_path:
        print(f"CSV: {csv_path}")
    if ranking_path:
        print(f"Ranking: {ranking_path}")
    if opportunities_path:
        print(f"Opportunities: {opportunities_path}")
    if review_path:
        print(f"Manual review: {review_path}")
    return 1 if errors and not records else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "mine":
        return mine_command(args)
    if args.command == "fetch-urls":
        return fetch_urls_command(args)
    if args.command == "problemas":
        return problems_command(args)
    parser.error(f"Unknown command: {args.command}")
    return 2
