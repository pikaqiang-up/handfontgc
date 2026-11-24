"""完整流程：照片 → 校正 → 取字 → 矢量 → TTF。"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import cv2

from .build import build_font, render_preview, render_specimen_html
from .detect import load_image, warp_page
from .extract import extract_cell
from .util import natural_key, read_json, safe_filename, write_json
from .vector import build_glyph

Progress = Callable[[int, int, str], None] | None


def build_project(
    project_dir: str | Path,
    photos: list[str | Path],
    *,
    family: str | None = None,
    style: str = "Regular",
    normalize: bool = True,
    progress: Progress = None,
    debug: bool = True,
) -> dict:
    project_dir = Path(project_dir)
    meta = read_json(project_dir / "meta.json")
    pages = meta["pages"]
    family = family or meta.get("family_name") or meta.get("title") or "我的手写体"

    photos = [Path(p) for p in photos]
    photos.sort(key=natural_key)
    total = sum(len(p["chars"]) for p in pages)
    done = 0
    missing: list[str] = []
    warnings: list[str] = []
    page_methods: list[dict] = []
    glyph_items: list[tuple[str, object, int]] = []
    warped_dir = project_dir / "build" / "warped"
    if debug:
        warped_dir.mkdir(parents=True, exist_ok=True)

    if len(photos) != len(pages):
        warnings.append(f"上传了 {len(photos)} 张照片，模板共 {len(pages)} 页。将按文件名顺序对应。")

    workers = max(2, min(8, (os.cpu_count() or 4)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for page_idx, page in enumerate(pages):
            if page_idx >= len(photos):
                missing.extend(page["chars"])
                done += len(page["chars"])
                if progress:
                    progress(done, total, f"缺少第 {page_idx + 1} 页照片")
                continue

            photo = photos[page_idx]
            if progress:
                progress(done, total, f"正在校正第 {page_idx + 1}/{len(pages)} 页")
            img = load_image(photo)
            warped, method, quad = warp_page(img)
            page_methods.append(
                {"page": page_idx + 1, "file": photo.name, "method": method, "quad": quad}
            )
            if method == "resize":
                warnings.append(f"第 {page_idx + 1} 页未找到四角黑块，按 A4 比例裁剪，可能偏移。")
            elif method == "page":
                warnings.append(f"第 {page_idx + 1} 页用纸张边缘校正，精度略低。")

            if debug:
                small = cv2.resize(warped, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
                cv2.imwrite(
                    str(warped_dir / f"page_{page_idx + 1:03d}.jpg"),
                    small,
                    [cv2.IMWRITE_JPEG_QUALITY, 85],
                )

            inks = []
            edge_count = 0
            for idx, ch in enumerate(page["chars"]):
                ink = extract_cell(warped, meta, idx)
                if ink is None:
                    missing.append(ch)
                else:
                    if ink.warnings:
                        edge_count += 1
                    inks.append((ch, ink))
            if edge_count:
                warnings.append(
                    f"第 {page_idx + 1} 页有 {edge_count} 个字贴到书写框边缘，可能写出格。"
                )

            futures = {
                pool.submit(build_glyph, ch, ink, normalize): ch for ch, ink in inks
            }
            for fut in as_completed(futures):
                ch = futures[fut]
                try:
                    glyph, advance, _ = fut.result()
                except Exception as exc:  # noqa: BLE001
                    missing.append(ch)
                    warnings.append(f"“{ch}”矢量化失败：{exc}")
                    continue
                if not getattr(glyph, "coordinates", None):
                    missing.append(ch)
                    continue
                glyph_items.append((ch, glyph, advance))

            done += len(page["chars"])
            if progress:
                progress(done, total, f"已处理 {done}/{total} 字")

    # 按模板字序排列，保证字体字形顺序和样张稳定
    order: dict[str, int] = {}
    for page in pages:
        for ch in page["chars"]:
            order.setdefault(ch, len(order))
    glyph_items.sort(key=lambda item: order.get(item[0], 10**9))

    # 去重（保序）
    seen: set[str] = set()
    unique_items = []
    for ch, glyph, adv in glyph_items:
        if ch not in seen:
            seen.add(ch)
            unique_items.append((ch, glyph, adv))
    glyph_items = unique_items
    missing = list(dict.fromkeys(missing))

    if not glyph_items:
        raise RuntimeError("没有提取到任何字。请确认照片是这份模板，且字写在格子里。")

    build_dir = project_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    font_name = safe_filename(f"{family}.ttf", "MyFont.ttf")
    if not font_name.lower().endswith(".ttf"):
        font_name += ".ttf"
    font_path = build_dir / font_name
    info = build_font(glyph_items, font_path, family=family, style=style)

    chars = [ch for ch, _, _ in glyph_items]
    preview_path = build_dir / "preview.png"
    render_preview(font_path, preview_path, family=family, chars=chars)
    specimen_path = build_dir / "specimen.html"
    render_specimen_html(font_path, specimen_path, family=family)

    result = {
        "ok": True,
        "family": family,
        "font": f"build/{font_name}",
        "preview": "build/preview.png",
        "specimen": "build/specimen.html",
        "glyphs": len(glyph_items),
        "total": total,
        "missing": missing,
        "warnings": warnings,
        "pages": page_methods,
        "info": info,
    }
    write_json(build_dir / "result.json", result)
    if progress:
        progress(total, total, f"完成：{len(glyph_items)} 个字，字体已生成")
    return result
