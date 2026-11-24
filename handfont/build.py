"""组装 TTF 字体、生成样张。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import hashlib

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.timeTools import timestampNow
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

from .chars import glyph_advance, unicode_name
from .config import (
    ASCENT,
    DESCENT,
    LABEL_FONT_CANDIDATES,
    UNITS_PER_EM,
    WIN_ASCENT,
    WIN_DESCENT,
)


def _notdef_glyph():
    pen = TTGlyphPen(None)
    # 外框（顺时针）
    pen.moveTo((40, -40))
    pen.lineTo((40, 840))
    pen.lineTo((960, 840))
    pen.lineTo((960, -40))
    pen.closePath()
    # 内框（逆时针，形成空心）
    pen.moveTo((80, 0))
    pen.lineTo((920, 0))
    pen.lineTo((920, 800))
    pen.lineTo((80, 800))
    pen.closePath()
    return pen.glyph()


def _empty_glyph():
    return TTGlyphPen(None).glyph()


def _ps_name(family: str) -> str:
    ascii_name = re.sub(r"[^0-9A-Za-z]+", "", family)
    if not ascii_name:
        ascii_name = "MyHandwriting"
    digest = hashlib.sha1(family.encode("utf-8")).hexdigest()[:6].upper()
    return f"{ascii_name[:48]}-{digest}-Regular"


def build_font(
    items: list[tuple[str, object, int]],
    out_path: str | Path,
    *,
    family: str = "我的手写体",
    style: str = "Regular",
) -> dict:
    """items: [(char, glyph, advance), ...]"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    glyph_order = [".notdef", "space"]
    glyphs = {".notdef": _notdef_glyph(), "space": _empty_glyph()}
    metrics = {".notdef": (1000, 40), "space": (500, 0)}
    cmap = {0x20: "space"}
    seen: set[str] = set()

    for ch, glyph, advance in items:
        if ch in seen:
            continue
        seen.add(ch)
        name = unicode_name(ch)
        if name in glyphs:
            continue
        glyph_order.append(name)
        glyphs[name] = glyph
        xs = [p[0] for p in glyph.coordinates] if getattr(glyph, "coordinates", None) else []
        lsb = int(min(xs)) if xs else 0
        metrics[name] = (int(advance), lsb)
        cmap[ord(ch)] = name

    if len(glyph_order) <= 2:
        raise ValueError("没有成功提取到任何字，请检查照片与模板是否对应")

    fb = FontBuilder(UNITS_PER_EM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT, lineGap=0)
    fb.setupNameTable(
        {
            "familyName": family,
            "styleName": style,
            "uniqueFontIdentifier": f"{family} {style} {datetime.now():%Y%m%d%H%M%S}",
            "fullName": f"{family} {style}",
            "psName": _ps_name(family),
            "version": "Version 1.000",
            "copyright": f"Copyright (c) {datetime.now():%Y} {family}. Created with 本地字体工坊.",
            "licenseDescription": "本字体由你书写并本地生成，版权归你所有。",
            "typographicFamily": family,
            "typographicSubfamily": style,
            "sampleText": "永和九年，岁在癸丑。",
        }
    )
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in seen)
    has_ascii = any(ch.isascii() and ch.isalnum() for ch in seen)
    fb.setupOS2(
        sTypoAscender=ASCENT,
        sTypoDescender=DESCENT,
        sTypoLineGap=0,
        usWinAscent=WIN_ASCENT,
        usWinDescent=WIN_DESCENT,
        usWeightClass=400,
        usWidthClass=5,
        fsSelection=0x0040,
        sxHeight=500,
        sCapHeight=700,
        usDefaultChar=0,
        usBreakChar=32,
        usMaxContext=1,
        ulUnicodeRange1=1 if has_ascii else 0,
        ulUnicodeRange2=0x00010000 if has_cjk else 0,
        ulCodePageRange1=(0x00000001 | (0x00040000 if has_cjk else 0)),
    )
    fb.setupPost()
    fb.setupMaxp()
    now = timestampNow()
    fb.setupHead(created=now, modified=now)
    fb.save(out_path)

    font = TTFont(out_path)
    names = font["name"].getDebugName(1)
    info = {
        "path": str(out_path),
        "family": names or family,
        "glyphs": len(font.getGlyphOrder()),
        "mapped_chars": len(font.getBestCmap() or {}),
        "size": out_path.stat().st_size,
    }
    font.close()
    return info


def _specimen_line(chars: list[str], wanted: str, limit: int, fallback_start: int = 0) -> str:
    """优先用固定例句；字集不够时按字表顺序取一段。"""
    available = set(chars)
    text = "".join(ch for ch in wanted if ch in available)
    if len(text) >= min(12, limit):
        return text[:limit]
    start = min(fallback_start, max(0, len(chars) - 1))
    return "".join(chars[start : start + limit])


def _sys_font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in LABEL_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render_preview(
    font_path: str | Path,
    out_png: str | Path,
    *,
    family: str,
    chars: list[str],
    text: str | None = None,
) -> Path:
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    W, H = 1600, 1000
    img = Image.new("RGB", (W, H), (247, 243, 234))
    d = ImageDraw.Draw(img)
    ink = (31, 28, 25)
    muted = (120, 112, 102)
    accent = (170, 58, 43)

    # 边框与装饰
    d.rectangle([36, 36, W - 36, H - 36], outline=(210, 202, 190), width=2)
    d.rectangle([70, 70, 108, 108], fill=accent)
    d.text((82, 89), "字", font=_sys_font(24), fill=(255, 248, 240), anchor="mm")
    d.text((126, 78), "本地字体工坊 · 字体样张", font=_sys_font(22), fill=muted, anchor="lm")
    d.text((W - 70, 82), datetime.now().strftime("%Y-%m-%d"), font=_sys_font(20), fill=muted, anchor="rm")

    title_font = _sys_font(54)
    d.text((70, 132), family, font=title_font, fill=ink, anchor="lm")
    d.line([(70, 196), (W - 70, 196)], fill=(210, 202, 190), width=2)

    try:
        font_big = ImageFont.truetype(str(font_path), 96)
        font_mid = ImageFont.truetype(str(font_path), 52)
        font_small = ImageFont.truetype(str(font_path), 36)
    except OSError:
        font_big = font_mid = font_small = _sys_font(40)

    sample = text or _specimen_line(
        chars, "永和九年，岁在癸丑，暮春之初。会于会稽山阴之兰亭，群贤毕至，少长咸集。", 80
    )
    sample = sample.replace("\n", " ")[:80]
    first = sample[:14]
    rest = sample[14:40]
    d.text((70, 240), first, font=font_big, fill=ink)
    d.text((70, 386), rest, font=font_mid, fill=ink)
    third = _specimen_line(
        chars, "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏", 30, fallback_start=40
    )
    d.text((70, 480), third, font=font_small, fill=ink)
    d.text((70, 548), "落款 / 签名", font=_sys_font(22), fill=muted)

    # 字符样本格
    y = 610
    grid_chars = _specimen_line(chars, "春夏秋冬风花雪月山水云雨天地人和", 26)
    cell = 66
    try:
        grid_font = ImageFont.truetype(str(font_path), 42)
    except OSError:
        grid_font = _sys_font(42)
    for i, ch in enumerate(grid_chars[:26]):
        col, row = i % 13, i // 13
        x = 70 + col * (cell + 8)
        yy = y + row * (cell + 8)
        d.rectangle([x, yy, x + cell, yy + cell], outline=(222, 215, 204), width=1)
        d.text((x + cell / 2, yy + cell / 2 + 2), ch, font=grid_font, fill=ink, anchor="mm")

    d.text(
        (70, H - 62),
        "完全离线生成 · 字体版权归书写者所有",
        font=_sys_font(20),
        fill=muted,
        anchor="lm",
    )
    d.text(
        (W - 70, H - 62),
        f"{len(chars)} 字",
        font=_sys_font(20),
        fill=accent,
        anchor="rm",
    )
    img.save(out_png, optimize=True)
    return out_png


def render_specimen_html(font_path: str | Path, out_html: str | Path, *, family: str) -> Path:
    out_html = Path(out_html)
    font_name = Path(font_path).name
    html = f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>{family} · 字体样张</title>
<style>
  @font-face {{ font-family: "HandFontPreview"; src: url("{font_name}"); }}
  body {{ background:#f7f3ea; color:#1f1c19; margin:0; padding:48px;
         font-family:"HandFontPreview", "Noto Serif CJK SC", serif; }}
  .label {{ font-family:ui-sans-serif, sans-serif; color:#8a8075; letter-spacing:.12em;
            font-size:12px; text-transform:uppercase; }}
  h1 {{ font-family:"HandFontPreview", serif; font-size:72px; margin:12px 0 40px; font-weight:400; }}
  .big {{ font-size:96px; line-height:1.5; }}
  .mid {{ font-size:48px; line-height:1.7; }}
  textarea {{ width:100%; min-height:120px; font:inherit; font-size:28px; padding:16px;
              background:#fffdf8; border:1px solid #d8d0c3; border-radius:8px; }}
  .hint {{ font-family:ui-sans-serif, sans-serif; color:#8a8075; font-size:13px; margin-top:8px; }}
</style>
<p class="label">本地字体工坊 · Local Font Studio</p>
<h1>{family}</h1>
<div class="big">永和九年，岁在癸丑。</div>
<div class="mid">暮春之初，会于会稽山阴之兰亭，群贤毕至，少长咸集。</div>
<p class="label" style="margin-top:40px">试写 / Type here</p>
<textarea spellcheck="false">天地玄黄，宇宙洪荒。日月盈昃，辰宿列张。</textarea>
<p class="hint">字体文件与这个 HTML 放在同一目录即可离线预览。</p>
</html>
"""
    out_html.write_text(html, encoding="utf-8")
    return out_html
