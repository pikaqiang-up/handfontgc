"""把单个字的二值图转成 TrueType 轮廓。"""
from __future__ import annotations

import math
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import parse_path

from .chars import glyph_advance, is_ascii_alnum, is_cjk, is_fullwidth, is_punctuation
from .config import GLYPH_BOTTOM, GLYPH_BOX
from .extract import CellInk

PAD = 2
SVG_NS = "{http://www.w3.org/2000/svg}"


def _mat_mul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    """SVG 矩阵 (a,b,c,d,e,f) 相乘：先应用 b，再应用 a。"""
    a1, b1, c1, d1, e1, f1 = a
    a2, b2, c2, d2, e2, f2 = b
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _parse_transform(text: str | None) -> tuple[float, ...]:
    mat: tuple[float, ...] = (1, 0, 0, 1, 0, 0)
    if not text:
        return mat
    for name, raw in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", text):
        nums = [float(x) for x in re.split(r"[ ,]+", raw.strip()) if x]
        if name == "translate":
            t = (1, 0, 0, 1, nums[0], nums[1] if len(nums) > 1 else 0)
        elif name == "scale":
            sx = nums[0]
            sy = nums[1] if len(nums) > 1 else sx
            t = (sx, 0, 0, sy, 0, 0)
        elif name == "matrix" and len(nums) == 6:
            t = tuple(nums)  # type: ignore[assignment]
        elif name == "rotate":
            ang = math.radians(nums[0])
            t = (math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang), 0, 0)
        else:
            continue
        mat = _mat_mul(mat, t)  # type: ignore[arg-type]
    return mat


def _trace_with_potrace(mask: np.ndarray) -> tuple[str, tuple[float, ...]] | None:
    """调用 potrace，返回 (path d, SVG 变换矩阵)。"""
    h, w = mask.shape
    padded = cv2.copyMakeBorder(mask, PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=0)
    # potrace 把黑色当作前景；我们的 mask 是 255=墨迹
    pgm = 255 - padded
    with tempfile.TemporaryDirectory(prefix="hfglyph-") as td:
        inp = Path(td) / "g.pgm"
        out = Path(td) / "g.svg"
        cv2.imwrite(str(inp), pgm)
        cmd = [
            "potrace",
            str(inp),
            "-s",
            "-o",
            str(out),
            "--flat",
            "--turdsize",
            "2",
            "--alphamax",
            "1.0",
            "--opttolerance",
            "0.2",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        if not out.exists():
            return None
        root = ET.parse(out).getroot()
        g = root.find(f".//{SVG_NS}g")
        path = root.find(f".//{SVG_NS}path")
        if path is None or not path.get("d"):
            return None
        transform = _parse_transform(g.get("transform") if g is not None else None)
        return path.get("d", ""), transform


def _trace_with_contours(mask: np.ndarray) -> list[list[tuple[float, float]]]:
    """无 potrace 时的兜底：多边形轮廓（y 向上）。"""
    h, w = mask.shape
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    result: list[list[tuple[float, float]]] = []
    if hierarchy is None:
        return result
    for c in contours:
        if cv2.contourArea(c) < 2:
            continue
        approx = cv2.approxPolyDP(c, 0.7, True).reshape(-1, 2)
        if len(approx) < 3:
            continue
        # 图像坐标 y 向下 → 字体坐标 y 向上
        result.append([(float(x), float(h - y)) for x, y in approx])
    return result


def _bounds(d: str, transform: tuple[float, ...]) -> tuple[float, float, float, float] | None:
    bp = BoundsPen(None)
    parse_path(d, TransformPen(bp, transform))
    return bp.bounds


def _punct_metrics(ch: str) -> tuple[float, float, bool] | None:
    """常见标点的目标高度、底部位置、是否水平居中。"""
    if ch in "。.":
        return 190.0, -70.0, not is_fullwidth(ch)
    if ch in "，,":
        return 210.0, -80.0, not is_fullwidth(ch)
    if ch in "、":
        return 210.0, -60.0, False
    if ch in ";；:：":
        return 240.0, -50.0, not is_fullwidth(ch)
    if ch in "!！?？":
        return 650.0, -30.0, not is_fullwidth(ch)
    if ch in "“”‘’\"'「」『』":
        return 220.0, 430.0, not is_fullwidth(ch)
    if ch in "（）()[]【】{}〈〉《》<>":
        return 650.0, -50.0, not is_fullwidth(ch)
    if ch in "—–-－":
        return 50.0, 300.0, not is_fullwidth(ch)
    if ch in "…":
        return 120.0, -60.0, False
    if ch in "/\\|":
        return 600.0, -40.0, True
    return None


def _transform_for(
    ch: str,
    ink: CellInk,
    normalize: bool,
) -> tuple[tuple[float, ...], int]:
    """计算 potrace 像素坐标 → 字体坐标的矩阵与字身宽度。"""
    bx0, by0, bx1, by1 = ink.bbox
    bbox_w, bbox_h = bx1 - bx0, by1 - by0
    box_w = ink.box[2] - ink.box[0]
    box_h = ink.box[3] - ink.box[1]
    advance = glyph_advance(ch)
    cell_scale = GLYPH_BOX / max(box_w, box_h)

    if normalize and is_cjk(ch):
        target_h = 815.0
        target_w = min(advance - 100, 860.0)
        scale = min(target_h / max(1, bbox_h), target_w / max(1, bbox_w), cell_scale * 1.35)
        tx = advance / 2 - (bbox_w / 2 + PAD) * scale
        ty = GLYPH_BOTTOM - PAD * scale
        return (scale, 0, 0, scale, tx, ty), advance

    if normalize and is_ascii_alnum(ch):
        # 拉丁字母按字形类别给不同的高度，避免小写字母被放大到和汉字一样高
        if ch.isupper() or ch.isdigit():
            target_h, bottom = 680.0, 0.0
        elif ch in "bdfhklt":
            target_h, bottom = 680.0, 0.0
        elif ch in "gjpqy":
            target_h, bottom = 680.0, -140.0
        else:
            target_h, bottom = 500.0, 0.0
        scale = min(
            target_h / max(1, bbox_h),
            (760.0 - 150.0) / max(1, bbox_w),
            cell_scale * 1.35,
        )
        advance = int(min(760, max(280, bbox_w * scale + 170)))
        tx = advance / 2 - (bbox_w / 2 + PAD) * scale
        ty = bottom - PAD * scale
        return (scale, 0, 0, scale, tx, ty), advance

    if normalize and is_punctuation(ch):
        metrics = _punct_metrics(ch)
        if metrics:
            target_h, bottom, center_x = metrics
            scale = min(target_h / max(1, bbox_h), cell_scale * 1.35)
            if center_x:
                tx = advance / 2 - (bbox_w / 2 + PAD) * scale
            else:
                tx = (advance - box_w * scale) / 2 + (bx0 - PAD) * scale
            ty = bottom - PAD * scale
            return (scale, 0, 0, scale, tx, ty), advance

    if is_punctuation(ch) and not normalize:
        scale = cell_scale
        tx = (advance - box_w * scale) / 2 + (bx0 - PAD) * scale
        ty = (box_h - by0 - bbox_h - PAD) * scale + GLYPH_BOTTOM
        return (scale, 0, 0, scale, tx, ty), advance

    # 保持书写位置与大小
    scale = cell_scale
    tx = (advance - box_w * scale) / 2 + (bx0 - PAD) * scale
    ty = (box_h - by0 - bbox_h - PAD) * scale + GLYPH_BOTTOM
    return (scale, 0, 0, scale, tx, ty), advance


def build_glyph(ch: str, ink: CellInk, normalize: bool = True):
    """返回 (glyph, advance, bbox)。失败时返回空字形。"""
    traced = _trace_with_potrace(ink.mask)
    matrix, advance = _transform_for(ch, ink, normalize)

    if traced is not None:
        d, svg_transform = traced
        # potrace 的 SVG 变换把路径翻到 y 向下的屏幕坐标；
        # 字体需要 y 向上，所以再翻回来。
        mask_h = ink.mask.shape[0] + 2 * PAD
        flip = (1, 0, 0, -1, 0, float(mask_h))
        combined = _mat_mul(matrix, _mat_mul(flip, svg_transform))
        bounds = _bounds(d, combined)
        if bounds is None:
            return TTGlyphPen(None).glyph(), advance, None
        glyph_pen = TTGlyphPen(None)
        cu2qu = Cu2QuPen(glyph_pen, max_err=0.6)
        parse_path(d, TransformPen(cu2qu, combined))
        return glyph_pen.glyph(), advance, bounds

    # 兜底：多边形轮廓
    glyph_pen = TTGlyphPen(None)
    for contour in _trace_with_contours(ink.mask):
        if not contour:
            continue
        pts = [((x + PAD), (y + PAD)) for x, y in contour]
        # 应用与 potrace 相同的字体矩阵（potrace 坐标原点在左下角）
        pts = [
            (matrix[0] * x + matrix[4], matrix[3] * y + matrix[5])
            for x, y in pts
        ]
        glyph_pen.moveTo(pts[0])
        for p in pts[1:]:
            glyph_pen.lineTo(p)
        glyph_pen.closePath()
    return glyph_pen.glyph(), advance, None
