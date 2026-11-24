"""全局配置：A4 模板尺寸、版式与路径。"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"
PROJECTS_DIR = BASE_DIR / "projects"

# 300 DPI A4
DPI = 300
PAGE_W = 2480
PAGE_H = 3508

# 模板 PNG 输出倍率：2 = 600 DPI（4960×7016），PDF 也使用这一套高清页面
PNG_SCALE = 2
PNG_DPI = DPI * PNG_SCALE

# 四角定位黑块（中心点，单位：像素）
MARKER_SIZE = 64
MARKER_CENTERS = [(130, 130), (2350, 130), (2350, 3378), (130, 3378)]

# 字格区域
GRID_X0, GRID_Y0, GRID_X1, GRID_Y1 = 180, 320, 2300, 3280
LABEL_H = 36          # 每格顶部写示例字的条带高度
CELL_INSET = 6        # 裁剪时再向内收缩，避开格线

DEFAULT_COLS = 9
DEFAULT_ROWS = 12
MIN_COLS, MAX_COLS = 4, 12
MIN_ROWS, MAX_ROWS = 6, 16

# 字体度量
UNITS_PER_EM = 1000
ASCENT = 880
DESCENT = -120
WIN_ASCENT = 900
WIN_DESCENT = 200
GLYPH_BOX = 900       # 书写格映射到字身框的边长
GLYPH_BOTTOM = -70    # 书写格底边对应的字体 y 坐标

# 渲染模板时使用的字体（仅用于印刷示例字，不会嵌入生成字体）
LABEL_FONT_CANDIDATES = [
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", 2),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Light.ttc", 2),
    ("/usr/share/fonts/TTF/LXGWWenKai-Regular.ttf", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
]


def ensure_dirs() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
