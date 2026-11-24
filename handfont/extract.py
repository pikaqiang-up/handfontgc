"""从校正后的模板页中提取每个字格里的墨迹。"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .template import cell_rect, writing_box


@dataclass
class CellInk:
    mask: np.ndarray                 # 仅包含字的二值图（255=墨迹），已裁到 bbox
    bbox: tuple[int, int, int, int]  # 相对书写框的 bbox
    cell: tuple[int, int, int, int]  # 字格绝对坐标
    box: tuple[int, int, int, int]   # 书写框绝对坐标
    touches_edge: bool = False
    warnings: list[str] = field(default_factory=list)


def _clean_mask(mask: np.ndarray) -> np.ndarray:
    """去掉小噪点、格线残留和异常大块。"""
    h, w = mask.shape
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    keep = np.zeros_like(mask)
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if area < 6:
            continue
        if area > 0.85 * w * h:
            continue
        # 细长的横/竖线（格线残留）
        if bh <= 3 and bw > 0.75 * w:
            continue
        if bw <= 3 and bh > 0.75 * h:
            continue
        keep[labels == i] = 255
    return keep


def extract_cell(page_bgr: np.ndarray, meta: dict, index: int) -> CellInk | None:
    x0, y0, x1, y1 = writing_box(meta, index)
    crop = page_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < 5.0:
        return None

    # Otsu 阈值，限制在合理范围，避免空白格把纸纹当墨迹
    thr, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    thr = float(min(max(thr, 70.0), 195.0))
    _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY_INV)

    ratio = float(np.count_nonzero(mask)) / mask.size
    if ratio < 0.00035:
        return None
    if ratio > 0.55:
        mask = cv2.bitwise_not(mask)

    mask = _clean_mask(mask)
    ys, xs = np.where(mask > 0)
    if len(xs) < 12:
        return None

    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
    by0, by1 = int(ys.min()), int(ys.max()) + 1
    touches = bx0 <= 1 or by0 <= 1 or bx1 >= mask.shape[1] - 1 or by1 >= mask.shape[0] - 1
    warnings = ["字迹贴到书写框边缘，可能写出格"] if touches else []
    return CellInk(
        mask=mask[by0:by1, bx0:bx1],
        bbox=(bx0, by0, bx1, by1),
        cell=cell_rect(meta, index),
        box=(x0, y0, x1, y1),
        touches_edge=touches,
        warnings=warnings,
    )
