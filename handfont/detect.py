"""页面检测：识别四角定位块并做透视校正。"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .config import MARKER_CENTERS, PAGE_H, PAGE_W

Point = tuple[float, float]


def load_image(path: str | Path) -> np.ndarray:
    """读取图片，处理 EXIF 方向，返回 BGR。"""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        rgb = np.array(im)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _order_points(pts: list[Point]) -> np.ndarray:
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = pts[:, 0] - pts[:, 1]
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmax(diff)]
    bl = pts[np.argmin(diff)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _valid_quad(quad: np.ndarray, shape: tuple[int, int]) -> bool:
    h, w = shape
    area = abs(cv2.contourArea(quad.astype(np.float32)))
    if area < 0.12 * w * h:
        return False
    if not cv2.isContourConvex(quad.astype(np.int32)):
        return False
    tl, tr, br, bl = quad
    if not (tl[0] < tr[0] and bl[0] < br[0] and tl[1] < bl[1] and tr[1] < br[1]):
        return False
    sides = [
        np.linalg.norm(tr - tl),
        np.linalg.norm(br - tr),
        np.linalg.norm(bl - br),
        np.linalg.norm(tl - bl),
    ]
    if min(sides) < 0.18 * min(w, h):
        return False
    if max(sides) / max(1.0, min(sides)) > 4.0:
        return False
    page_ratio = (sides[0] + sides[2]) / max(1.0, sides[1] + sides[3])
    if not 0.42 <= page_ratio <= 1.05:
        return False
    return True


def find_markers(bgr: np.ndarray) -> np.ndarray | None:
    """寻找四个实心黑方块，返回 TL,TR,BR,BL（原图坐标）。"""
    h, w = bgr.shape[:2]
    scale = min(1.0, 1800.0 / max(h, w))
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # 光照不均时用自适应阈值兜底
    _, th = cv2.threshold(gray, 105, 255, cv2.THRESH_BINARY_INV)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    img_area = small.shape[0] * small.shape[1]
    cands: list[Point] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < img_area * 0.00003 or area > img_area * 0.02:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bh == 0 or bw == 0:
            continue
        aspect = bw / bh
        if not 0.70 <= aspect <= 1.45:
            continue
        if area / (bw * bh) < 0.78:
            continue
        # 方块不能贴着图像边缘（被裁掉一角会导致定位偏差）
        if x <= 1 or y <= 1 or x + bw >= small.shape[1] - 1 or y + bh >= small.shape[0] - 1:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cands.append((m["m10"] / m["m00"] / scale, m["m01"] / m["m00"] / scale))

    if len(cands) < 4:
        return None

    # 取最靠近四个角的候选点
    pts = np.array(cands, dtype=np.float32)
    s = pts[:, 0] + pts[:, 1]
    d = pts[:, 0] - pts[:, 1]
    picks = [
        tuple(pts[np.argmin(s)]),
        tuple(pts[np.argmax(d)]),
        tuple(pts[np.argmax(s)]),
        tuple(pts[np.argmin(d)]),
    ]
    if len(set(picks)) < 4:
        return None
    quad = _order_points(picks)
    if not _valid_quad(quad, (h, w)):
        return None
    return quad


def find_page_quad(bgr: np.ndarray) -> np.ndarray | None:
    """兜底：在深色背景上找最大的浅色纸张四边形。"""
    h, w = bgr.shape[:2]
    scale = min(1.0, 1400.0 / max(h, w))
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 0)
    _, th = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.15 * small.shape[0] * small.shape[1]:
        return None
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in (0.02, 0.04, 0.06):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            quad = _order_points([tuple(p[0] / scale) for p in approx])
            if _valid_quad(quad, (h, w)):
                return quad
    return None


def warp_page(bgr: np.ndarray) -> tuple[np.ndarray, str, list[Point]]:
    """把照片校正为 2480×3508 的模板坐标。

    返回 (校正图, 方法, 检测到的四角)。
    """
    quad = find_markers(bgr)
    method = "markers"
    if quad is None:
        quad = find_page_quad(bgr)
        method = "page"
    if quad is None:
        # 最后兜底：按 A4 比例居中裁剪 / 缩放
        h, w = bgr.shape[:2]
        target_ratio = PAGE_W / PAGE_H
        if w / h > target_ratio:
            new_w = int(h * target_ratio)
            x0 = (w - new_w) // 2
            crop = bgr[:, x0 : x0 + new_w]
        else:
            new_h = int(w / target_ratio)
            y0 = (h - new_h) // 2
            crop = bgr[y0 : y0 + new_h]
        warped = cv2.resize(crop, (PAGE_W, PAGE_H), interpolation=cv2.INTER_CUBIC)
        return warped, "resize", []

    dst = np.array(MARKER_CENTERS, dtype=np.float32)
    if method == "page":
        # 纸张四角向内收，近似定位块中心（水平 5.24%、垂直 3.71%）
        cx = np.mean(quad[:, 0])
        cy = np.mean(quad[:, 1])
        quad = (quad - [cx, cy]) * [0.8952, 0.9258] + [cx, cy]
    m = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    warped = cv2.warpPerspective(
        bgr,
        m,
        (PAGE_W, PAGE_H),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return warped, method, [tuple(map(float, p)) for p in quad]
