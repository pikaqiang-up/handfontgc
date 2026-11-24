"""生成可打印的 A4 手写模板（PDF + 单页 PNG）。"""
from __future__ import annotations

import math
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .config import (
    CELL_INSET,
    DEFAULT_COLS,
    DEFAULT_ROWS,
    DPI,
    GRID_X0,
    GRID_X1,
    GRID_Y0,
    GRID_Y1,
    LABEL_FONT_CANDIDATES,
    LABEL_H,
    MARKER_CENTERS,
    MARKER_SIZE,
    PAGE_H,
    PAGE_W,
    PNG_DPI,
    PNG_SCALE,
)
from .util import write_json

Progress = Callable[[int, int, str], None] | None

INK = (28, 26, 24)
MUTED = (120, 116, 110)
GRID = (198, 194, 188)
LABEL = (150, 146, 140)


def _label_font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in LABEL_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _cell_rect(col: int, row: int, cols: int, rows: int) -> tuple[int, int, int, int]:
    x0 = round(GRID_X0 + (GRID_X1 - GRID_X0) * col / cols)
    x1 = round(GRID_X0 + (GRID_X1 - GRID_X0) * (col + 1) / cols)
    y0 = round(GRID_Y0 + (GRID_Y1 - GRID_Y0) * row / rows)
    y1 = round(GRID_Y0 + (GRID_Y1 - GRID_Y0) * (row + 1) / rows)
    return x0, y0, x1, y1


def _draw_page(
    page_no: int,
    page_count: int,
    chars: list[str],
    cols: int,
    rows: int,
    title: str,
    preset_label: str,
    scale: int,
) -> Image.Image:
    """按 scale 倍渲染一页。scale=2 即 600 DPI。"""
    width, height = PAGE_W * scale, PAGE_H * scale
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    title_font = _label_font(38 * scale)
    info_font = _label_font(25 * scale)
    hint_font = _label_font(22 * scale)
    label_font = _label_font(30 * scale)
    footer_font = _label_font(19 * scale)

    # 四角定位块
    half = (MARKER_SIZE * scale) // 2
    for cx, cy in MARKER_CENTERS:
        x, y = cx * scale, cy * scale
        d.rectangle([x - half, y - half, x + half, y + half], fill=(0, 0, 0))

    # 页眉
    d.text((GRID_X0 * scale, 196 * scale), title, font=title_font, fill=INK, anchor="lm")
    d.text(
        (GRID_X1 * scale, 200 * scale),
        f"第 {page_no} / {page_count} 页",
        font=info_font,
        fill=MUTED,
        anchor="rm",
    )
    d.text(
        (GRID_X0 * scale, 254 * scale),
        f"{preset_label} · 每格一字 · 不要写出格 · 拍照时四角黑块要完整入镜",
        font=hint_font,
        fill=MUTED,
        anchor="lm",
    )

    # 字格
    for idx in range(cols * rows):
        col, row = idx % cols, idx // cols
        x0, y0, x1, y1 = _cell_rect(col, row, cols, rows)
        d.rectangle(
            [x0 * scale, y0 * scale, x1 * scale, y1 * scale],
            outline=GRID,
            width=2 * scale,
        )
        d.line(
            [
                ((x0 + 10) * scale, (y0 + LABEL_H) * scale),
                ((x1 - 10) * scale, (y0 + LABEL_H) * scale),
            ],
            fill=(228, 225, 220),
            width=scale,
        )
        if idx < len(chars):
            d.text(
                (((x0 + x1) / 2) * scale, (y0 + LABEL_H / 2 + 1) * scale),
                chars[idx],
                font=label_font,
                fill=LABEL,
                anchor="mm",
            )

    d.text(
        (width // 2, 3458 * scale),
        "本地字体工坊 · 完全离线 · 你的字迹只留在本机",
        font=footer_font,
        fill=(165, 161, 155),
        anchor="mm",
    )
    return img


def generate_template(
    project_dir: str | Path,
    chars: list[str],
    *,
    title: str = "我的手写体",
    family_name: str = "我的手写体",
    preset: str = "custom",
    preset_label: str = "自定义文字",
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    progress: Progress = None,
) -> dict:
    """在 project_dir 下生成模板，返回 meta 字典。"""
    project_dir = Path(project_dir)
    template_dir = project_dir / "template"
    preview_dir = project_dir / "preview"
    template_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    per_page = cols * rows
    page_count = max(1, math.ceil(len(chars) / per_page))
    pages_meta: list[dict] = []
    page_paths: list[str] = []

    for page_no in range(1, page_count + 1):
        page_chars = chars[(page_no - 1) * per_page : page_no * per_page]
        img = _draw_page(
            page_no, page_count, page_chars, cols, rows, title, preset_label, PNG_SCALE
        )
        name = f"page_{page_no:03d}.png"
        path = template_dir / name
        img.save(path, dpi=(PNG_DPI, PNG_DPI), optimize=True)
        page_paths.append(str(path))

        thumb = img.resize((PAGE_W // 4, PAGE_H // 4), Image.LANCZOS)
        thumb.save(preview_dir / f"page_{page_no:03d}.jpg", quality=88, optimize=True)
        img.close()
        thumb.close()

        pages_meta.append(
            {
                "index": page_no,
                "file": f"template/{name}",
                "preview": f"preview/page_{page_no:03d}.jpg",
                "chars": page_chars,
            }
        )
        if progress:
            progress(page_no, page_count, f"正在生成第 {page_no}/{page_count} 页")

    pdf_path = project_dir / "template.pdf"
    pdf_ok = False
    try:
        import img2pdf

        with open(pdf_path, "wb") as fh:
            fh.write(img2pdf.convert(page_paths))
        pdf_ok = True
    except Exception:
        pdf_path = None  # type: ignore[assignment]

    # 高清 PNG 打包，方便在平板/电脑上直接书写，或单页下载
    png_zip_path = project_dir / "template_png.zip"
    try:
        tmp_zip = project_dir / ".template_png.zip.tmp"
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_STORED) as zf:
            for path in page_paths:
                zf.write(path, arcname=Path(path).name)
        tmp_zip.replace(png_zip_path)
    except Exception:
        png_zip_path = None  # type: ignore[assignment]

    meta = {
        "version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": title,
        "family_name": family_name,
        "preset": preset,
        "preset_label": preset_label,
        "total_chars": len(chars),
        "page": {"width": PAGE_W, "height": PAGE_H, "dpi": DPI},
        "png_page": {"width": PAGE_W * PNG_SCALE, "height": PAGE_H * PNG_SCALE, "dpi": PNG_DPI},
        "layout": {
            "cols": cols,
            "rows": rows,
            "grid": [GRID_X0, GRID_Y0, GRID_X1, GRID_Y1],
            "label_h": LABEL_H,
            "inset": CELL_INSET,
            "markers": [list(p) for p in MARKER_CENTERS],
        },
        "pages": pages_meta,
        "pdf": "template.pdf" if pdf_ok else None,
        "png_zip": "template_png.zip" if png_zip_path else None,
    }
    write_json(project_dir / "meta.json", meta)
    (project_dir / "chars.txt").write_text("\n".join(chars) + "\n", encoding="utf-8")
    (project_dir / "chars_compact.txt").write_text("".join(chars), encoding="utf-8")
    (project_dir / "使用说明.txt").write_text(
        "本地字体工坊 · 使用说明\n"
        "========================\n\n"
        f"1. 打印 template.pdf（A4，实际大小 / 100%，不要勾选“适应页面”）。\n"
        f"   如需在平板/电脑上直接书写，使用 template/ 里的高清 PNG（{PAGE_W * PNG_SCALE}×{PAGE_H * PNG_SCALE}，{PNG_DPI} DPI），\n"
        f"   或下载 template_png.zip 一次取走全部页面。\n"
        f"2. 用黑色笔在每格内写一个字，不要写出格线，也不要涂改。\n"
        "3. 拍照或扫描所有页面，按页序上传到本地工具。\n"
        "   拍照要求：正面、光线均匀、四角黑块完整入镜、不要裁掉页面。\n"
        "4. 在网页中点击“合成字体”，完成后下载 TTF。\n\n"
        f"本模板共 {len(chars)} 字，{page_count} 页，每页 {cols}×{rows} 格。\n",
        encoding="utf-8",
    )
    return meta


def cell_rect(meta: dict, index: int) -> tuple[int, int, int, int]:
    layout = meta["layout"]
    cols, rows = layout["cols"], layout["rows"]
    return _cell_rect(index % cols, index // cols, cols, rows)


def writing_box(meta: dict, index: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = cell_rect(meta, index)
    label_h = meta["layout"]["label_h"]
    inset = meta["layout"]["inset"]
    return x0 + inset, y0 + label_h + 2, x1 - inset, y1 - inset
