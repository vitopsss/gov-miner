from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request
from pathlib import Path


def safe_filename_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name or "documento"
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    if "." not in name:
        name = f"{name}.pdf"
    stem = Path(name).stem[:80]
    suffix = Path(name).suffix[:10] or ".pdf"
    return f"{stem}_{digest}{suffix}"


def download_url(url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / safe_filename_from_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "gov-miner/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    return target


def download_urls_file(urls_file: Path, output_dir: Path) -> list[Path]:
    urls = [
        line.strip()
        for line in urls_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return [download_url(url, output_dir) for url in urls]

