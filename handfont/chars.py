"""字符集：预设、去重、字符分类。"""
from __future__ import annotations

import unicodedata
from pathlib import Path

from .config import DATA_DIR

ASCII_TEXT = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    ".,;:!?'\"()[]{}<>+-=*/\\|@#$%&_~^`"
    "，。、；：！？“”‘’（）【】《》〈〉—…·"
)

PRESETS = {
    "common3500": {
        "label": "常用 3500 字",
        "desc": "覆盖日常书写，约 33 页（9×12 格）",
        "file": "chars_3500.txt",
    },
    "ascii": {
        "label": "英文 / 数字 / 标点",
        "desc": "字母、数字与常用中英标点，约 1 页",
        "text": ASCII_TEXT,
    },
    "custom": {
        "label": "自定义文字",
        "desc": "粘贴任意文字，按顺序生成模板",
        "text": "",
    },
}


def load_preset(name: str) -> str:
    preset = PRESETS.get(name)
    if preset is None:
        raise ValueError(f"未知字符集: {name}")
    if "file" in preset:
        path = DATA_DIR / preset["file"]
        return path.read_text(encoding="utf-8")
    return preset.get("text", "")


def clean_chars(text: str) -> tuple[list[str], list[str]]:
    """按顺序去重，跳过空白与不可打印字符。

    返回 (字符列表, 警告列表)。
    """
    seen: set[str] = set()
    chars: list[str] = []
    warnings: list[str] = []
    for ch in text:
        if ch.isspace() or ch == "\ufeff":
            continue
        if unicodedata.category(ch) in {"Cc", "Cf", "Cs"}:
            continue
        if ch in seen:
            continue
        seen.add(ch)
        chars.append(ch)
    if not chars:
        raise ValueError("没有可生成的字符")
    if len(chars) > 12000:
        raise ValueError("一次最多 12000 个字符（约 111 页），请分批制作")
    return chars, warnings


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2FA1F
        or ch in "〇〡〢〣〤〥〦〧〨〩"
    )


def is_ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def is_punctuation(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def is_fullwidth(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in {"W", "F"}


def glyph_advance(ch: str) -> int:
    """字身宽度（1000 units/em）。"""
    if is_cjk(ch):
        return 1000
    if is_ascii_alnum(ch):
        return 600
    if ch == " ":
        return 500
    if is_fullwidth(ch):
        return 1000
    return 500


def unicode_name(ch: str) -> str:
    cp = ord(ch)
    if cp <= 0xFFFF:
        return f"uni{cp:04X}"
    return f"u{cp:05X}"


def display_char(ch: str) -> str:
    if ch == " ":
        return "空格"
    return ch
