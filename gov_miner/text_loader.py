from __future__ import annotations

import subprocess
from pathlib import Path


TEXT_EXTENSIONS = {".txt", ".md", ".csv"}
PDF_EXTENSIONS = {".pdf"}


def iter_input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]

    if not input_path.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")

    files: list[Path] = []
    for path in input_path.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS | PDF_EXTENSIONS:
            files.append(path)
    return sorted(files)


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def read_pdf_file(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n\n".join(pages)
    except ImportError:
        pass

    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "PDF extraction requires pypdf (`pip install -r requirements.txt`) "
            "or the `pdftotext` command available in PATH."
        ) from exc


def load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return read_text_file(path)
    if suffix in PDF_EXTENSIONS:
        return read_pdf_file(path)
    raise ValueError(f"Unsupported input file: {path}")

