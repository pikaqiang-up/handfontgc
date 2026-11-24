#!/usr/bin/env python3
"""本地字体工坊 · 命令行入口。

常用：
  ./handfont.py template --preset common3500
  ./handfont.py build --project projects/xxx --photos photos/*.jpg
  ./handfont.py serve
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from handfont.chars import PRESETS, clean_chars, load_preset
from handfont.config import DEFAULT_COLS, DEFAULT_ROWS, PROJECTS_DIR, ensure_dirs
from handfont.pipeline import build_project
from handfont.template import generate_template
from handfont.util import natural_key, slugify


def _make_project_dir(name: str) -> Path:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = PROJECTS_DIR / f"{stamp}-{slugify(name)}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def cmd_template(args: argparse.Namespace) -> int:
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
        preset = "custom"
    elif args.text:
        text = args.text
        preset = "custom"
    else:
        text = load_preset(args.preset)
        preset = args.preset
    chars, _ = clean_chars(text)

    label = PRESETS.get(preset, {}).get("label", "自定义文字")
    if preset == "custom":
        label = f"自定义 {len(chars)} 字"
    title = args.title or args.family or "我的手写体"
    out = Path(args.out) if args.out else _make_project_dir(title)
    out.mkdir(parents=True, exist_ok=True)

    def progress(done: int, total: int, msg: str) -> None:
        print(f"\r[{done}/{total}] {msg}", end="", flush=True)

    meta = generate_template(
        out,
        chars,
        title=title,
        family_name=args.family or title,
        preset=preset,
        preset_label=label,
        cols=args.cols,
        rows=args.rows,
        progress=progress,
    )
    print()
    pages = len(meta["pages"])
    print(f"模板已生成：{out}")
    print(f"  字数：{len(chars)}  页数：{pages}  每页：{args.cols}×{args.rows}")
    if meta.get("pdf"):
        print(f"  PDF：{out / 'template.pdf'}")
    png_page = meta.get("png_page", meta["page"])
    print(f"  PNG：{out / 'template'}（{png_page['width']}×{png_page['height']}，{png_page['dpi']} DPI）")
    if meta.get("png_zip"):
        print(f"  PNG 压缩包：{out / 'template_png.zip'}")
    print("\n下一步：打印模板 / 在平板上打开 PNG → 书写 → 拍照 → 运行")
    print(f"  ./handfont.py build --project {out} --photos 照片目录/*.jpg")
    return 0


def _expand_photos(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        p = Path(value)
        if p.is_dir():
            for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp", "*.tif", "*.tiff"):
                paths.extend(p.glob(ext))
                paths.extend(p.glob(ext.upper()))
        elif p.exists():
            paths.append(p)
        else:
            print(f"跳过不存在的文件：{value}", file=sys.stderr)
    unique = {p.resolve(): p for p in paths}
    return sorted(unique.values(), key=natural_key)


def cmd_build(args: argparse.Namespace) -> int:
    project = Path(args.project)
    if not (project / "meta.json").exists():
        print(f"找不到项目：{project / 'meta.json'}", file=sys.stderr)
        return 1
    photos = _expand_photos(args.photos)
    if not photos:
        print("没有找到照片。把照片放在一个目录里，然后传目录或文件列表。", file=sys.stderr)
        return 1

    def progress(done: int, total: int, msg: str) -> None:
        pct = 100 * done / max(1, total)
        print(f"\r[{pct:5.1f}%] {msg:<40}", end="", flush=True)

    result = build_project(
        project,
        photos,
        family=args.family,
        style=args.style,
        normalize=not args.no_normalize,
        progress=progress,
    )
    print()
    print(f"字体已生成：{project / result['font']}")
    print(f"  字形：{result['glyphs']} / {result['total']}")
    print(f"  样张：{project / result['preview']}")
    if result["missing"]:
        preview = "".join(result["missing"][:40])
        print(f"  缺字 {len(result['missing'])} 个：{preview}{'…' if len(result['missing']) > 40 else ''}")
    if result["warnings"]:
        for warning in result["warnings"][:10]:
            print(f"  提示：{warning}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    from fontTools.ttLib import TTFont
    from handfont.build import render_preview

    font = Path(args.font)
    font_obj = TTFont(font)
    cmap = font_obj.getBestCmap() or {}
    font_obj.close()
    chars = [chr(cp) for cp in sorted(cmap)]
    out = Path(args.out) if args.out else font.with_name("preview.png")
    render_preview(font, out, family=args.family or font.stem, chars=chars, text=args.text)
    print(f"样张已生成：{out}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    from fontTools.ttLib import TTFont

    font = TTFont(args.font)
    cmap = font.getBestCmap() or {}
    print(f"文件：{args.font}")
    print(f"字体名：{font['name'].getDebugName(1)} {font['name'].getDebugName(2)}")
    print(f"字形数：{len(font.getGlyphOrder())}  字符数：{len(cmap)}")
    print(f"单位：{font['head'].unitsPerEm}  Ascender：{font['hhea'].ascent}  Descender：{font['hhea'].descent}")
    chars = "".join(chr(cp) for cp in sorted(cmap) if cp >= 0x20)
    print(f"字符预览：{chars[:80]}{'…' if len(chars) > 80 else ''}")
    font.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print("本地字体工坊 · 环境检查")
    print(f"  Python：{sys.version.split()[0]}")
    ok = True
    for mod in ("numpy", "PIL", "cv2", "fontTools", "img2pdf"):
        try:
            m = __import__(mod)
            print(f"  {mod:<10} OK  {getattr(m, '__version__', '')}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  {mod:<10} 缺失：{exc}")
    potrace = shutil.which("potrace")
    print(f"  potrace    {'OK  ' + potrace if potrace else '缺失（请安装 potrace）'}")
    ok = ok and bool(potrace)
    from handfont.template import _label_font

    try:
        _label_font(20)
        print("  中文字体    OK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  中文字体    缺失：{exc}")
    print(f"  CPU 核心：{os.cpu_count()}")
    print("结论：" + ("一切就绪" if ok else "请先修复上面的缺失项"))
    return 0 if ok else 1


def cmd_selftest(args: argparse.Namespace) -> int:
    """用系统手写字体模拟填写模板，跑通全流程。"""
    from PIL import Image, ImageDraw, ImageFont
    from handfont.template import writing_box
    import json
    import numpy as np
    import cv2

    chars, _ = clean_chars("永和九年岁在癸丑暮春之初会于稽山阴兰亭")
    out = _make_project_dir("selftest")
    meta = generate_template(
        out, chars, title="自检字体", family_name="自检字体", preset_label="自检", cols=6, rows=4
    )
    photo_dir = out / "photos"
    photo_dir.mkdir()
    font_path = "/usr/share/fonts/TTF/LXGWWenKai-Regular.ttf"
    for page in meta["pages"]:
        img = Image.open(out / page["file"]).convert("RGB")
        d = ImageDraw.Draw(img)
        for idx, ch in enumerate(page["chars"]):
            x0, y0, x1, y1 = writing_box(meta, idx)
            w, h = x1 - x0, y1 - y0
            f = ImageFont.truetype(font_path, int(min(w, h) * 0.78))
            bbox = f.getbbox(ch)
            gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
            d.text(
                (x0 + (w - gw) / 2 - bbox[0], y0 + (h - gh) / 2 - bbox[1]),
                ch,
                font=f,
                fill=(25, 23, 21),
            )
        img.save(photo_dir / f"page_{page['index']}.png")

    result = build_project(
        out, sorted(photo_dir.glob("page_*.png")), family="自检字体", normalize=True
    )
    print(f"自检完成：{out}")
    print(f"  字形：{result['glyphs']} / {result['total']}，缺字：{len(result['missing'])}")
    print(f"  字体：{out / result['font']}")
    if result["missing"]:
        print("  缺字：" + "".join(result["missing"]))
        return 1
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from server import main as server_main

    return server_main(port=args.port, open_browser=not args.no_browser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="handfont.py",
        description="本地字体工坊：把手写模板照片变成 TTF 字体（完全离线）",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("template", help="生成 A4 手写模板")
    p.add_argument("--preset", default="common3500", choices=sorted(PRESETS))
    p.add_argument("--text", help="自定义文字（与 --text-file 二选一）")
    p.add_argument("--text-file", help="从文本文件读取字符")
    p.add_argument("--cols", type=int, default=DEFAULT_COLS)
    p.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    p.add_argument("--title", help="模板标题")
    p.add_argument("--family", help="字体家族名，默认同标题")
    p.add_argument("--out", help="项目目录，默认 projects/时间-名称")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("build", help="用照片合成字体")
    p.add_argument("--project", required=True)
    p.add_argument("--photos", nargs="+", required=True, help="照片文件或目录")
    p.add_argument("--family", help="覆盖字体家族名")
    p.add_argument("--style", default="Regular")
    p.add_argument("--no-normalize", action="store_true", help="保留每个字原始大小，不做统一")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("preview", help="为 TTF 生成样张")
    p.add_argument("--font", required=True)
    p.add_argument("--text", help="自定义样张文字")
    p.add_argument("--family", help="样张标题")
    p.add_argument("--out", help="输出 PNG")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("info", help="查看 TTF 信息")
    p.add_argument("--font", required=True)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("doctor", help="检查运行环境")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("selftest", help="生成模拟手写并跑通全流程")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("serve", help="启动本地网页界面")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
