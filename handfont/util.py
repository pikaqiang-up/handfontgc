"""通用小工具。"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def natural_key(path: str | Path) -> list[Any]:
    name = Path(path).name
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", name)
    ]


def safe_filename(name: str, fallback: str = "file") -> str:
    name = Path(name).name
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name)
    name = name.strip("._") or fallback
    return name[:120]


def slugify(name: str, fallback: str = "project") -> str:
    text = unicodedata.normalize("NFKC", name).strip().lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", text)
    text = text.strip("-")
    return (text or fallback)[:40]


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> None:
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def human_size(num: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
