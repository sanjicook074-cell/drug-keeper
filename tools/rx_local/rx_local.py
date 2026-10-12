#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""处方单「本地解析」—— 用 Windows 自带的 OCR 引擎，不装任何额外服务、不联网。

用法：
    python rx_local.py 处方照片.jpg
    python rx_local.py 处方照片.jpg --json out.json --dump-ocr
    python rx_local.py 处方照片.jpg --url https://xxxx.app.workbuddy.host/   # 输出 #rx= 链接

依赖：
    Pillow
    winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections
    winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging
    winrt-Windows.Storage winrt-Windows.Storage.Streams winrt-Windows.Globalization
    （后四个是 Windows.Media.Ocr 的 Python 绑定，纯二进制轮子；要 Windows 10/11 + 中文识别语言包）

--------------------------------------------------------------------------------
为什么长这样 —— 每一条都是这台机器上实测出来的，别凭直觉改：

1) 方向不能靠 EXIF。手机拍完再用**微信传一遍，EXIF 会被整段抹掉**（实测这张
   1080x1919 的图 getexif() 是空的）。所以只能：四个方向各认一遍，按「像不像处方单」
   打分挑最好的那个。实测这张图 270°（顺时针 270 = 逆时针 90）才是正的，
   0° 只出 6 行乱码、180° 出 1 行、90° 出 23 行但全是错的。

2) 小字号数字格会被整格漏读。「用量」列第 1 行的 `1g`，整页 2x 读不出、表格块 2x/3x
   读不出、列条 4x 也读不出；只有**把那一格单独抠出来 8x** 才认出来（还认成 `ig`）。
   所以本脚本的策略是「多尺度粗读 + 只给可疑的格子单独补读」，而不是一味放大整张图。

3) 不要自己转灰度/二值化。实测灰度、自动对比度、阈值二值化、锐化四种预处理
   **全部返回空**；直接喂彩色原图反而认得最好。缩放用 LANCZOS 就行。

4) OCR 会往每个汉字之间插空格，把 `.` 认成全角 `．` 或 `，`，把 `[` 和 `〕` 混着用。
   不归一化的话药名会带一堆空格、规格里的 `0.5g` 会变成 `0，5g`。

5) 版面靠**逐词坐标**切。整行 OCR 会把左边「诊断」栏的文字和右边「药品名称」栏
   并在同一行（实测同一 y 上就是「诊断栏半个病名」+「药名+规格」粘在一起），
   按行读必然串味，只能按 x 坐标分列。

6) 行带之间会**重叠**，边界上的字被上下两行同时认领。`find_layout` 给每行左右各留了
   ±0.5 字高做余量，于是相邻两行在交界处叠出一段。实测第 0 行「用法」格读出「口服服」——
   多出来的那个「服」是第 1 行的字（cy=366.5，第 0 行带 [322.5,368.5] 正好盖住它）。
   数值列上同一个机制更危险（下一行的数字并进上一行的「用量/单价」）。
   取格子前先用 `row_yband()` 把行带**在重叠段的中点分家**。⚠️ 两种错法都试过：
   · 不动 → 多吃下一行的字；· 改成「与相邻行中心线取中」→ 把**本行自己的**字切掉
   （这张单第 0 行的「用法」字在 cy≈326，比行中心 345.5 高 20px，而它与表头中心线 310
   的中点正好 327.75 —— 一刀下去只剩个「口」）。只有「只在重叠段中点分家」两头都对。

7) 交叉校验：`Σ(数量 × 单价) == 金额`。这张单子 2×6.12 + 1×8.21 = 20.45，正好对上。
   对不上就说明数量/单价列读错了，得告诉人去核对 —— 这比任何置信度都实在。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field

# ------------------------------------------------------------------ 依赖
try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit('缺少 Pillow：python -m pip install Pillow')

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile
    _WINRT_OK = True
    _WINRT_ERR = ''
except Exception as e:  # pragma: no cover
    _WINRT_OK = False
    _WINRT_ERR = repr(e)

OCR_LANG = 'zh-Hans-CN'


# ================================================================== 1. OCR 底座
@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


_ENGINE = None
_TMP_DIR = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        if not _WINRT_OK:
            sys.exit('Windows OCR 绑定不可用（%s）。\n'
                     '装一下：pip install winrt-runtime winrt-Windows.Foundation '
                     'winrt-Windows.Foundation.Collections winrt-Windows.Media.Ocr '
                     'winrt-Windows.Graphics.Imaging winrt-Windows.Storage '
                     'winrt-Windows.Storage.Streams winrt-Windows.Globalization' % _WINRT_ERR)
        _ENGINE = OcrEngine.try_create_from_language(Language(OCR_LANG))
        if _ENGINE is None:
            _ENGINE = OcrEngine.try_create_from_user_profile_languages()
        if _ENGINE is None:
            sys.exit('系统里没有中文 OCR 语言包。设置 → 时间和语言 → 语言 → 中文(简体) → 选项 → '
                     '「光学字符识别」装上即可。')
    return _ENGINE


async def _ocr_file_async(path: str) -> list[Word]:
    f = await StorageFile.get_file_from_path_async(path)
    stream = await f.open_async(FileAccessMode.READ)
    dec = await BitmapDecoder.create_async(stream)
    bmp = await dec.get_software_bitmap_async()
    res = await _engine().recognize_async(bmp)
    out: list[Word] = []
    for ln in res.lines:
        for w in ln.words:
            r = w.bounding_rect
            out.append(Word(w.text, float(r.x), float(r.y), float(r.width), float(r.height)))
    return out


_TMP_SEQ = 0


def ocr(img: Image.Image, tag: str = 'x') -> list[Word]:
    """把 PIL 图落成临时 PNG 再交给 Windows OCR（彩色原样，别预处理）。"""
    global _TMP_DIR, _TMP_SEQ
    if _TMP_DIR is None:
        _TMP_DIR = tempfile.mkdtemp(prefix='rxlocal_')
    _TMP_SEQ += 1
    p = os.path.join(_TMP_DIR, 'c%04d_%s.png' % (_TMP_SEQ, tag))
    img.save(p, 'PNG')
    try:
        return asyncio.run(_ocr_file_async(p))
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def scaled(img: Image.Image, k: float) -> Image.Image:
    if abs(k - 1.0) < 1e-6:
        return img
    return img.resize((max(1, int(round(img.width * k))), max(1, int(round(img.height * k)))),
                      Image.LANCZOS)


# ================================================================== 2. 归一化
_BRACKET = {'〔': '[', '〖': '[', '【': '[', '《': '[', '「': '[', '『': '[',
            '〕': ']', '〗': ']', '】': ']', '》': ']', '」': ']', '』': ']'}
_DOTISH = '，．·、‧,'
# ↑ ASCII 的逗号也要在里面：全角转换那一步已经先把「，」变成「,」了，
#   只写全角逗号的话这里的「数字之间的点」这条替换永远匹配不上（实测漏过一次）


def norm(s: str, keep_space: bool = False) -> str:
    """处方单元格文本归一化。

    · 全角 → 半角
    · 花括号统一成 []
    · 数字之间的 ，．· → . （0，5g → 0.5g；6．12 → 6.12）
    · 默认把**所有空白删掉** —— OCR 会在每个汉字之间塞一个空格，
      而处方单元格里本来就没有需要保留的空格（保留会毁掉药名与规格的匹配）。
    """
    if not s:
        return ''
    out = []
    for ch in str(s):
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:          # 全角 ASCII
            ch = chr(o - 0xFEE0)
        elif o == 0x3000:                  # 全角空格
            ch = ' '
        elif 0x2460 <= o <= 0x2473:        # ① ② …
            ch = chr(ord('0') + (o - 0x2460) + 1) if o <= 0x2468 else ch
        out.append(_BRACKET.get(ch, ch))
    s = ''.join(out)
    # ★ 先收空格再判「数字之间的点」—— 反过来的话 OCR 的「0 ， 5g」里逗号两边是空格，
    #   规则匹配不上，规格就会留下「0,5g」这种脏东西（实测）。
    s = re.sub(r'\s+', ' ', s).strip() if keep_space else re.sub(r'\s+', '', s)
    s = re.sub('(?<=[0-9])[%s]+(?=[0-9])' % re.escape(_DOTISH), '.', s)
    s = s.replace('．', '.')
    # OCR 常把衬线体的 1 认成 i / l / I（实测「1g」→「ig」）：紧挨着剂量单位就掰回来
    s = re.sub(r'(?i)\b([il|])(?=(mg|g|ml|ug|iu|μg)\b)', '1', s)
    return s


# ================================================================== 3. 方向选择
DRUG_SUFFIX = ('片', '胶囊', '丸', '颗粒', '注射液', '注射剂', '膏', '贴', '散', '滴眼液',
               '口服液', '糖浆', '凝胶', '喷雾', '乳', '栓', '气雾剂', '软膏', '胶浆')
HDR_WORDS = ('药品名称', '品名', '数量', '单价', '用量', '频次', '用法', '用法用量')
CJK = re.compile(r'[\u4e00-\u9fff]')


def score_orientation(words: list[Word]) -> float:
    """猜「这堆词像不像一张正过来的处方单」。分数只用来挑方向。"""
    joined = ''.join(w.text for w in words)
    s = 0.0
    if '药品名称' in joined or '品名' in joined:
        s += 200
    if '用量' in joined:
        s += 60
    if '频次' in joined or '用法' in joined:
        s += 60
    if '数量' in joined and '单价' in joined:
        s += 60
    if '处方' in joined:
        s += 40
    if '医院' in joined:
        s += 40
    # 命中药名后缀的词 → 最有力的证据
    n_drug = sum(1 for w in words if any(w.text.endswith(sfx) for sfx in DRUG_SUFFIX))
    s += 90 * n_drug
    s += 0.06 * len(CJK.findall(joined))
    return s


def upright(img: Image.Image, debug: bool = False) -> tuple[Image.Image, dict]:
    """把照片转正：四个方向各认一遍，挑分最高的。"""
    cands = []
    for rot in (0, 90, 180, 270):
        im = img.rotate(-rot, expand=True) if rot else img      # rotate 正角是逆时针
        ws = ocr(im, 'rot%d' % rot)
        sc = score_orientation(ws)
        cands.append((sc, rot, im, ws))
        if debug:
            print('  [方向] %3d° 分数 %7.1f  %d 个词' % (rot, sc, len(ws)))
    cands.sort(key=lambda t: -t[0])
    sc, rot, im, ws = cands[0]
    return im, {'rot': rot, 'score': sc, 'all': [(c[1], round(c[0], 1)) for c in cands]}


# ================================================================== 4. 版面切分
@dataclass
class Layout:
    name_x0: float          # 「药品名称」栏左边界
    hdr_y: float            # 表头行中线
    cols: list[tuple]       # [(列名, x0, x1), ...]
    rows: list[tuple]       # [(y0, y1), ...]
    rowh: float
    table_bottom: float
    row_cy: list[float]     # 每行中线（算格子边界要用）


def _band_chars(words: list[Word], y0: float, y1: float, cjk_only: bool = True):
    """把 y 落在一段里的词按 x 拼成 [(字, x)]。

    ★ cjk_only=True 是必须的：OCR 会把左栏「诊断/高血压」和右栏「药品名称」串进同一条行，
      而且 `R：` 正好插在「药」和「品」中间 —— 直接把词拼起来永远拼不出「药品名称」。
      只留汉字、把标点和字母丢掉，才能还原出连续的表头文字。
    """
    chars = []
    for w in sorted([w for w in words if y0 <= w.cy <= y1], key=lambda w: w.x):
        for ch in w.text:
            if cjk_only and not CJK.match(ch):
                continue
            chars.append((ch, w.x))
    return chars


def _text_of(band) -> str:
    return ''.join(c for c, _ in band)


def _find_needle(band: list[tuple[str, float]], needle: str, start: int = 0):
    """在拼好的字串里找 needle，返回 (起始 x, 结束 x, 结束下标)。"""
    text = _text_of(band)
    i = text.find(needle, start)
    if i < 0:
        return None
    return band[i][1], _first_x(band, i + len(needle)), i + len(needle)


def _lines_of(words: list[Word], tol: float, max_span: float | None = None) -> list[list[Word]]:
    """按 y 把词聚成行。max_span 是「一簇最多占多高」——
    防止 OCR 把半张表格报成一行时，簇顺着 cy 一路串下去（实测栽过）。"""
    ws = sorted(words, key=lambda w: w.cy)
    out, cur = [], []
    for w in ws:
        if cur and (w.cy - cur[-1].cy > tol
                    or (max_span is not None and w.cy - cur[0].cy > max_span)):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


HDR_KEYS = ('药品名称', '数量', '单价', '用量', '频次', '用法')


def _match_keyword(words: list[Word], key: str, ref_cy: float | None, tol_y: float):
    """在词流里按 x 从左往右贪心匹配一串表头文字，返回 (起始 x, 结束 x, 行 cy)。

    为什么要这么麻烦：OCR 会把左栏「诊断/高血压」和右栏「药品名称」串进同一条行，
    而且 `R：` 正好插在「药」和「品」中间 —— 直接拼词永远拼不出「药品名称」。
    这里允许**跳过不含目标字的词**，但要求匹配到的字必须落在同一视觉行
    （与上一个命中字的 cy 相差不超过 tol_y），所以不会跨行错配。
    """
    pool = [w for w in words if ref_cy is None or abs(w.cy - ref_cy) <= tol_y]
    pool.sort(key=lambda w: w.x)
    j, xs, prev_cy = 0, [], None
    for w in pool:
        for ch in w.text:
            if ch == key[j]:
                if j > 0 and prev_cy is not None and abs(w.cy - prev_cy) > tol_y:
                    # 跳到别的行了 → 这一段匹配作废，看这个字能不能重新起头
                    j, xs, prev_cy = (1, [w.x], w.cy) if ch == key[0] else (0, [], None)
                    continue
                j += 1
                xs.append(w.x)
                prev_cy = w.cy
                if j == len(key):
                    return xs[0], xs[-1], w.cy
            elif j > 0 and ch == key[0]:
                j, xs, prev_cy = 1, [w.x], w.cy
    return None


def find_layout(words: list[Word], S: float) -> Layout | None:
    """按逐词坐标找表头、切列、切行。坐标单位是 OCR 那层的像素。"""
    hs = sorted(w.h for w in words if w.h > 0)
    unit_h = hs[len(hs) // 2] if hs else 20.0

    # ---- ① 先定位「药品名称（规格）」—— 它就是整张表的锚
    got_name = _match_keyword(words, '药品名称', None, unit_h * 1.0) \
        or _match_keyword(words, '品名', None, unit_h * 1.0)
    if got_name is None:
        return None
    hdr_x, hdr_cy = got_name[0], got_name[2]

    # ---- ② 以锚点那一行为准找五个列表头（各列表头的 y 并不齐平，实测能差 20px）
    found = []
    for key in ('数量', '单价', '用量', '频次', '用法'):
        g = _match_keyword(words, key, hdr_cy, unit_h * 1.4)
        if g is not None:
            found.append((key, g[0], g[1]))
    if len(found) < 3:
        return None
    found.sort(key=lambda t: t[1])          # 防 OCR 顺序错乱，按 x 重排

    # 列边界：表头字左对齐（实测「数量」表头 x=991、单元格 x=991/993 基本齐），中间留点垫
    cols = []
    for k, (nm, xi, xj) in enumerate(found):
        left = xi - unit_h * 0.5
        right = found[k + 1][1] - unit_h * 0.8 if k + 1 < len(found) else xj + unit_h * 6
        cols.append((nm, left, right))

    # 药名列的左边界先用一个**宽松**的估值去挑行，等行挑出来再用行里最左那个词回填。
    # 不能直接用表头的 x 当左边界：实测表头「药」字比药名的第一个字还靠右，
    # 照它切会把药名的第一个字啃掉（药名少了头一个字，还看不出来是错的）。
    name_x1 = cols[0][1] - unit_h * 0.2
    guess_x0 = hdr_x - unit_h * 2.2

    # ---- 行：药名列里、表头以下的词，按 y 聚类
    rowsrc = [w for w in words if guess_x0 <= w.cx < name_x1 and w.cy > hdr_cy + unit_h * 0.5]
    if not rowsrc:
        return None
    rowsrc.sort(key=lambda w: w.cy)
    tol = unit_h * 0.7
    groups, cur = [], [rowsrc[0]]
    for w in rowsrc[1:]:
        if w.cy - cur[-1].cy <= tol and w.cy - cur[0].cy <= unit_h * 1.6:
            cur.append(w)
        else:
            groups.append(cur)
            cur = [w]
    groups.append(cur)

    name_x0 = min(w.x for w in rowsrc) - unit_h * 0.3
    # 行带竖向留 ±0.5 字高：再窄会把「口服」这种字丢掉一半，再宽会吸到隔壁行
    rows = [(min(w.y for w in g) - unit_h * 0.5, max(w.y1 for w in g) + unit_h * 0.5) for g in groups]
    row_cy = [(a + b) / 2 for a, b in rows]
    # 表格底：只认前 12 行。药列下面还有「金额/审核/提示」一大片正文，
    # 不封顶的话整块裁切会一直延伸到页面底部（实测裁到过 y=967）。
    keep = min(len(rows), 12)
    table_bottom = rows[keep - 1][1] + unit_h * 0.8
    return Layout(name_x0, hdr_cy, cols, rows, unit_h, table_bottom, row_cy)


def row_yband(lay: Layout, ri: int) -> tuple[float, float]:
    """一行**真正的**竖向边界 = 它与相邻两行的中线（和 `_reread_cell` 同一套口径）。

    ★ 不能直接用 `lay.rows[ri]`：`find_layout` 给行带上下各留了 ±0.5 字高，
      相邻两行因此**会重叠**，落在重叠带里的字被上下两行**同时认领**。
      实测（用户那张真底方）第 0 行「用法」格读出「口服服」—— 多出来的那个「服」
      其实是第 1 行的字（cy=366.5，而第 0 行带 [322.5,368.5] 正好把它盖住）。
      数值列上同一个机制更危险：下一行的数字会被并进上一行的「用量/单价」。

    怎么分家：**只在两行真正重叠的那一小段的中点**切一刀，外边缘一根手指都不碰。
    ⚠️ 别改成「与相邻行中心线的中点」—— 试过，会把本行的字切掉：这张单第 0 行的
      「用法」字（口 cy=328、服 cy=325.5）比它的行中心（345.5）高 20px，而它与表头
      中心线（310）的中点正好是 327.75 —— 一刀下去只剩个「口」。
    这个函数只会**收缩**行带（结果是原行带的子集），绝不会往外扩。
    """
    top0, bot0 = lay.rows[ri]
    top, bot = top0, bot0
    if ri > 0:
        _, pbot = lay.rows[ri - 1]
        if pbot > top:                       # 和上一行重叠 → 在重叠段中点分家
            top = (top + pbot) / 2
    if ri + 1 < len(lay.rows):
        ntop, _ = lay.rows[ri + 1]
        if bot > ntop:                       # 和下一行重叠 → 同上
            bot = (ntop + bot) / 2
    # 行距异常小、被压没了 → 那还不如别乱动，退回原行带
    if bot - top < lay.rowh * 0.5:
        return top0, bot0
    return top, bot


def _first_x(band: list[tuple[str, float]], idx: int) -> float:
    if 0 <= idx < len(band):
        return band[idx][1]
    if band:
        return band[-1][1] if idx >= len(band) else band[0][1]
    return 0.0


def cell_text(words: list[Word], x0: float, x1: float, y0: float, y1: float,
              how: str = 'center') -> tuple[str, tuple | None]:
    """取一个矩形里的词，按 x 拼起来；返回 (文本, 这些词的包围盒)。"""
    hit = [w for w in words
           if (x0 <= w.cx < x1 if how == 'center' else x0 <= w.x < x1)
           and y0 <= w.cy <= y1]
    if not hit:
        return '', None
    hit.sort(key=lambda w: w.x)
    txt = ''.join(w.text for w in hit)          # ★ 不要用空格连：归一化里「数字之间的点」会被空格挡掉
    bb = (min(w.x for w in hit), min(w.y for w in hit),
          max(w.x1 for w in hit), max(w.y1 for w in hit))
    return txt, bb


# ================================================================== 5. 解析规则
# ★ 下面这些规则必须和 drug-keeper/index.html 里的 rxParseLine / rxDailyUse 保持一致。
RX_MASS = r'mg|g|ml|ug|iu'
RX_CNT = '片|粒|支|贴|袋|滴|喷|丸|包'
_QTY_UNIT = '盒|瓶|袋|板|包|支|罐'
CN_NUM = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7,
          '八': 8, '九': 9, '十': 10}


def cn2i(v):
    if v is None:
        return None
    if re.fullmatch(r'\d+', v):
        return int(v)
    return CN_NUM.get(v)


@dataclass
class RxRow:
    name: str = ''
    spec: str = ''
    unit: str = '片'
    per_dose: float = 1.0
    cycle_days: int = 1
    times_per_cycle: int = 1
    total: float = 0.0
    note: str = ''
    warn: str = ''
    # 只给报告看，不进网页
    qty: float = 0.0
    qty_unit: str = ''
    price: float = 0.0
    dose_raw: str = ''
    freq_raw: str = ''
    route_raw: str = ''
    raw_name_cell: str = ''
    sources: dict = field(default_factory=dict)

    @property
    def daily(self) -> float:
        return daily_use(self.per_dose, self.times_per_cycle, self.cycle_days)

    @property
    def days(self) -> int | None:
        d = self.daily
        if d <= 0:
            return None
        return max(0, int(self.total / d + 1e-9))

    def to_page_row(self) -> dict:
        return {'name': self.name, 'spec': self.spec, 'unit': self.unit,
                'perDose': self.per_dose, 'cycleDays': self.cycle_days,
                'timesPerCycle': self.times_per_cycle, 'total': self.total,
                'note': self.note, 'warn': self.warn, 'picked': True}


def daily_use(per_dose: float, times_per_cycle: float, cycle_days: float) -> float:
    if per_dose <= 0 or times_per_cycle <= 0 or cycle_days <= 0:
        return 0.0
    return per_dose * times_per_cycle / cycle_days


def split_name_spec(cell: str) -> tuple[str, str]:
    """把一个「药名[规格]」单元格拆成 药名 / 规格。"""
    s = norm(cell)
    if not s:
        return '', ''
    tags = re.findall(r'\[([^\]]*)\]', s)
    body = re.sub(r'\[[^\]]*\]', '', s)

    def pick(arr):
        for t in arr:
            if re.search(r'[*x×]\s*\d', t, re.I) and re.search(r'\d', t):
                return t
        for t in arr:
            if re.search(r'\d\s*(?:%s)' % RX_MASS, t, re.I):
                return t
        return ''

    spec = pick(tags)
    # 药名 = 去掉方括号后的中文主体（规格写在括号外的那种也照顾到）
    name = body
    tail = re.search(r'^(.*?)(\d+(?:\.\d+)?\s*(?:%s))$' % RX_MASS, name, re.I)
    if tail and tail.group(1):
        if not spec:
            spec = tail.group(2)
        name = tail.group(1)
    name = re.sub(r'[(（][^)）]*[)）]', '', name)
    name = re.sub(r'^[^\u4e00-\u9fffA-Za-z0-9]+', '', name)     # 抠列时容易带进来的前导标点
    name = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]+$', '', name)
    return name, spec


def parse_freq(f: str) -> tuple[int, int, bool]:
    """频次 → (cycleDays, timesPerCycle, 认出来了没)。"""
    f = norm(f)
    m = re.search(r'(?:每日|每天|一日)([一二两三四五六七八九十]|\d+)\s*次', f)
    if m:
        return 1, cn2i(m.group(1)) or 1, True
    if re.search(r'每日|每天|一日|\bqd\b', f):
        return 1, 1, True
    if re.search(r'每晚|每夜|睡前|\bqn\b', f):
        return 1, 1, True
    m = re.search(r'(\d+)\s*次\s*[/每]\s*(?:日|天)', f)
    if m:
        return 1, int(m.group(1)), True
    if re.search(r'隔[日天]', f):
        return 2, 1, True
    # 「每周2次」「每月1次」这种写法，量词前面可以不写数字 —— 所以前半段的数字是可选的。
    # 写成必选的话这两种会整条落到「每日 1 次」这个错值上（实测网页端同样有这个盲点，已一并修）。
    m = re.search(r'每\s*([一二两三四五六七八九十\d]*)\s*(?:周|星期)\s*([一二两三四五六七八九十\d]+)?\s*次?', f)
    if m:
        return 7 * (cn2i(m.group(1)) or 1), (cn2i(m.group(2)) or 1) if m.group(2) else 1, True
    m = re.search(r'每\s*([一二两三四五六七八九十\d]*)\s*个?月\s*([一二两三四五六七八九十\d]+)?\s*次?', f)
    if m:
        return 28 * (cn2i(m.group(1)) or 1), (cn2i(m.group(2)) or 1) if m.group(2) else 1, True
    m = re.search(r'每\s*([一二两三四五六七八九十\d]+)\s*天\s*([一二两三四五六七八九十\d]+)?\s*次?', f)
    if m:
        return max(1, cn2i(m.group(1)) or 1), (cn2i(m.group(2)) or 1) if m.group(2) else 1, True
    m = re.search(r'(\d+)\s*次\s*[/每]\s*(?:周|星期)', f)
    if m:
        return 7, int(m.group(1)), True
    return 1, 1, False


def infer_unit(name: str, spec: str, amt_unit: str = '') -> str:
    m = re.search(r'[*x×]\s*\d+\s*([^\d\s]*)', spec)
    if m and m.group(1):
        return m.group(1)
    if re.search(r'注射|针剂|单抗', name):
        return '支'
    if re.search(r'贴膏|贴剂|凝胶贴|巴布', name):
        return '贴'
    if re.search(r'胶囊|软胶囊', name):
        return '粒'
    if re.search(r'颗粒|散剂|冲剂', name):
        return '袋'
    if re.search(r'滴剂|滴丸', name):
        return '滴'
    if re.search(r'喷雾|气雾|鼻喷', name):
        return '喷'
    if re.search(r'片|丸', name):
        return '片'
    if amt_unit and re.fullmatch(RX_CNT, amt_unit):
        return amt_unit
    return '片'


def build_row(name_cell: str, qty_cell: str, price_cell: str, dose_cell: str,
              freq_cell: str, route_cell: str, warns: list[str]) -> RxRow | None:
    name, spec = split_name_spec(name_cell)
    if not name:
        return None
    r = RxRow(name=name, spec=spec, raw_name_cell=name_cell)

    per_pack = None
    mm = re.search(r'[*x×]\s*(\d+)\s*([^\d\s]*)', spec)
    if mm:
        per_pack = int(mm.group(1))
    dm = re.search(r'(\d+(?:\.\d+)?)\s*(%s)' % RX_MASS, spec, re.I)
    dose_per_u = float(dm.group(1)) if dm else 0.0
    dose_unit = dm.group(2).lower() if dm else ''

    qm = re.search(r'(\d+(?:\.\d+)?)\s*(%s)' % _QTY_UNIT, norm(qty_cell))
    r.qty = float(qm.group(1)) if qm else 0.0
    r.qty_unit = qm.group(2) if qm else ''
    if not qm and norm(qty_cell):
        w = '数量格「%s」没认出数量' % norm(qty_cell)
        warns.append(w)

    r.price = float(re.search(r'(\d+(?:\.\d+)?)', norm(price_cell)).group(1)) if re.search(
        r'(\d+(?:\.\d+)?)', norm(price_cell)) else 0.0

    # ---- 每次用量：和规格同单位才敢除
    d = norm(dose_cell)
    r.dose_raw = d
    per_dose = 1.0
    am = re.search(r'(\d+(?:\.\d+)?)\s*(%s|%s)' % (RX_MASS, RX_CNT), d, re.I)
    if am and dose_per_u > 0 and am.group(2).lower() == dose_unit:
        per_dose = round(float(am.group(1)) / dose_per_u, 4)
    elif am and re.fullmatch(RX_CNT, am.group(2)):
        per_dose = float(am.group(1))
    elif re.fullmatch(r'\d+(?:\.\d+)?', d):
        per_dose = float(d)
        warns.append('用量格只写了个数（%s）没写单位，按「每次 %s」算，请核对' % (d, d))
    else:
        warns.append('用量格没认出来（原文「%s」），暂按「每次 1」算，请核对' % (d or '空'))

    r.per_dose = per_dose
    r.unit = infer_unit(name, spec, am.group(2) if am else '')

    cd, tpc, ok = parse_freq(freq_cell)
    r.cycle_days, r.times_per_cycle = cd, tpc
    r.freq_raw = norm(freq_cell)
    if not ok:
        warns.append('频次没认出来（原文「%s」），暂按「每日 1 次」算，请核对' % (norm(freq_cell) or '空'))

    route = norm(route_cell)
    r.route_raw = route
    um = re.search(r'皮下注射|肌肉注射|静脉|外用|涂患处|含服|舌下|吸入|滴眼|滴鼻|口服|嚼服|冲服', route)
    if um and um.group(0) != '口服':
        r.note = um.group(0)

    r.total = (r.qty if r.qty > 0 else 1) * (per_pack if per_pack else 1)
    if per_pack is None:
        warns.append('规格里没认出「每盒多少%s」（规格「%s」），请核对「本次开到」' % (r.unit, spec or '空'))
    return r


# ================================================================== 6. 主流程
def _bbox_of(words, x0, x1, y0, y1):
    hit = [w for w in words if x0 <= w.cx < x1 and y0 <= w.cy <= y1]
    if not hit:
        return None
    return (min(w.x for w in hit), min(w.y for w in hit),
            max(w.x1 for w in hit), max(w.y1 for w in hit))


def parse_prescription(path: str, debug: bool = False) -> dict:
    src = Image.open(path)
    src.load()
    exif_rot = 0
    try:
        o = src.getexif().get(274)
        if o in (3, 4):
            exif_rot = 180
        elif o in (5, 6):
            exif_rot = 270
        elif o in (7, 8):
            exif_rot = 90
    except Exception:
        pass

    base = src.convert('RGB')
    if exif_rot:
        base = base.rotate(-exif_rot, expand=True)

    up, ori = upright(base, debug)

    # ---- 粗读 ①：整页 **1x**（表头坐标、药名、行位置都从这里来）
    #   ★ 别在这里放大：实测 2x 时 Windows OCR 会自己把整张表格并成「一条行」
    #     （诊断、药名、数量、单价、用量全挤进同一行），行就切不开了；
    #     1x 反而给出 51 条互不粘连的行。要提字准度靠后面按块/按格裁切放大。
    S = 1.0
    big = scaled(up, S)
    w_big = ocr(big, 'page1')
    if debug:
        print('  [粗读] 整页 %dx%d → %d 词' % (big.width, big.height, len(w_big)))
    lay = find_layout(w_big, S)
    if lay is None:
        return {'ok': False, 'reason': '没找到「药品名称 / 数量 / 单价 / 用量 / 频次」这类表头，'
                                       '这不像一张标准处方单（或者方向没转对）。',
                'orientation': ori}

    # ---- 粗读 ②：表格块 3x（括号、规格认得更准；实测 2x 会把 〔 和 ] 认错）
    u_scale = 1.0 / S                      # 从 OCR 坐标回到转正图坐标
    name_x0_u, name_x1_u = lay.name_x0 * u_scale, lay.cols[0][1] * u_scale
    last_col_right = lay.cols[-1][2] * u_scale
    tbl_box = (int(max(0, name_x0_u - 6)), int(max(0, lay.hdr_y * u_scale - 8)),
               int(min(up.width, last_col_right + 4)), int(min(up.height, lay.table_bottom * u_scale + 4)))
    w_tbl = []
    if tbl_box[2] > tbl_box[0] and tbl_box[3] > tbl_box[1]:
        crop = up.crop(tbl_box)
        w_tbl_raw = ocr(scaled(crop, 3.0), 'tbl3')
        w_tbl = [Word(w.text, w.x / 3.0 + tbl_box[0], w.y / 3.0 + tbl_box[1],
                      w.w / 3.0, w.h / 3.0) for w in w_tbl_raw]   # 换回转正图坐标
        if debug:
            print('  [粗读] 表格块 %s ×3 → %d 词' % (str(tbl_box), len(w_tbl_raw)))

    def pick_cell(col_i: int, row_i: int, y0: float, y1: float) -> tuple[str, dict]:
        """一个单元格：先看表格块 3x，再看整页 2x（都换回转正图坐标）。

        y0/y1 由调用方用 `row_yband()` 算好传进来 —— 不能用 `lay.rows[row_i]`，
        那个带子与邻行重叠，会把邻行边界上的字并进来（见 `row_yband` 的注释）。
        """
        nm, x0, x1 = lay.cols[col_i]
        px0, px1 = x0 * u_scale, x1 * u_scale
        py0, py1 = y0 * u_scale, y1 * u_scale
        cands = {}
        t1, _ = cell_text(w_tbl, px0, px1, py0, py1)
        if t1.strip():
            cands['tbl3'] = t1
        t2, _ = cell_text(w_big, x0, x1, y0, y1)
        if t2.strip():
            cands['big2'] = t2
        # 数值列：表格块优先；文本列：整页优先（整页那层的字通常更全）
        order = ['tbl3', 'big2'] if re.fullmatch(r'数量|单价|用量', nm) else ['big2', 'tbl3']
        for k in order:
            if k in cands:
                return cands[k], cands
        return '', cands

    rows: list[RxRow] = []
    report_rows = []
    for ri in range(len(lay.rows)):
        y0, y1 = row_yband(lay, ri)          # ← 不重叠的行带（别用 lay.rows[ri]）
        py0, py1 = y0 * u_scale, y1 * u_scale
        # 药名格横跨「药品名称」栏 —— 它不在 lay.cols 里，单独取
        tn1, _ = cell_text(w_tbl, name_x0_u, name_x1_u, py0, py1)
        tn2, _ = cell_text(w_big, name_x0_u, name_x1_u, py0, py1)
        name_cell = tn1 if tn1.strip() else tn2
        name_src = 'tbl3' if tn1.strip() else 'big2'

        # 药列只有「表头下面那几行」是药；再往下是金额/审核/提示，遇到就收工。
        # 不加这道闸，那些说明文字会一溜排成药（实测多出 5 行垃圾）。
        if not _looks_like_drug(name_cell):
            if rows:
                break
            continue

        cells, cell_src = {}, {}
        for ci in range(len(lay.cols)):
            t, allc = pick_cell(ci, ri, y0, y1)
            cells[lay.cols[ci][0]] = t
            cell_src[lay.cols[ci][0]] = allc

        # ---- 补漏：粗读「不像话」的格子，单独抠出来 8x 彩色重读
        #      实测这是唯一能救回「用量 1g」的办法（整页 2x / 表格块 3x / 列条 4x 全都漏）
        fixed = []
        for ci in range(len(lay.cols)):
            nm = lay.cols[ci][0]
            if _cell_looks_ok(nm, norm(cells[nm])):
                continue
            got = _reread_cell(up, lay, ci, ri, u_scale, debug)
            if got:
                fixed.append(nm)
                cells[nm] = got
                cell_src[nm]['tight8'] = got

        warns = []
        pre = []
        for nm in fixed:
            pre.append('%s 格粗读没认出来，已单独放大重读 → 「%s」' % (nm, norm(cells[nm])))
        row = build_row(name_cell, cells.get('数量', ''), cells.get('单价', ''),
                        cells.get('用量', ''), cells.get('频次', ''), cells.get('用法', ''),
                        warns)
        if row is None:
            continue
        row.warn = '；'.join(pre + warns)
        row.sources = {'name': name_src, **{k: '/'.join(v.keys()) for k, v in cell_src.items()}}
        rows.append(row)
        report_rows.append({'name_cell': name_cell, 'cells': dict(cells),
                            'fixed_by': fixed, 'warn': row.warn})

    # ---- 元信息（整页文字里捞）
    meta = _meta(''.join(w.text for w in w_big))
    amount = meta.get('金额')

    # ---- 交叉校验 Σ(数量×单价) == 金额
    chk = None
    if amount is not None and rows:
        s = sum(round(r.qty * r.price, 2) for r in rows)
        chk = {'sum': round(s, 2), 'amount': amount, 'ok': abs(s - amount) < 0.02}

    return {'ok': bool(rows), 'orientation': ori, 'meta': meta, 'rows': rows,
            'report_rows': report_rows, 'check': chk,
            'layout': {'header_y': lay.hdr_y, 'rows': [len(lay.rows)], 'cols': [c[0] for c in lay.cols]}}


# 明显不是药品的行（和 index.html 里的 RX_NOT_DRUG 同一套思路）
NOT_DRUG = re.compile(r'金额|合计|合计|审核|核对|发药|调配|取药|药房|打印|日期|提示|有效期|'
                      r'医师|药师|咨询|退换|处方|登记|医保|科室|姓名|性别|年龄|诊断|备注|'
                      r'数量|单价|用量|频次|用法|药品名称|规格|患者|支付|基金|条码|编号')


def _looks_like_drug(cell: str) -> bool:
    if not cell:
        return False
    t = norm(cell)
    if NOT_DRUG.search(t):
        return False
    return len(CJK.findall(t)) >= 2


def _cell_looks_ok(nm: str, v: str) -> bool:
    if not v:
        return False
    if nm == '数量':
        return bool(re.fullmatch(r'\d+(?:\.\d+)?(?:%s)?' % _QTY_UNIT, v))
    if nm == '单价':
        return bool(re.fullmatch(r'\d+(?:\.\d+)?', v))
    if nm == '用量':
        return bool(re.fullmatch(r'\d+(?:\.\d+)?(?:%s|%s)?' % (RX_MASS, RX_CNT), v, re.I))
    if nm == '频次':
        return bool(re.search(r'每日|每天|一日|隔日|每周|每\d|每[一二两三四五六七八九十]|睡前|每晚|次', v))
    if nm == '用法':
        return len(v) >= 1
    return True


def _reread_cell(up: Image.Image, lay: Layout, ci: int, ri: int, u_scale: float,
                 debug: bool) -> str:
    """把一格单独抠出来放大重读 —— 这是救回「用量＝1g」这类小字格的唯一办法。

    抠法（全是实测出来的，别改）：
    · 竖向边界取**相邻两行的中线** —— 这才是表格里一格真正的边界。
      上下都多给一点（±0.9 行高）反而会串到下一行：实测「用量 第1行」会读成下一行的 4mg，
      比读不出来更糟（错得看不出来）。
    · 倍率用 **4x**，不是越高越好：实测 8x 时「用量 1g」读成空、「单价 8.21」读成「&21」，
      4x 两个都对。所以顺序是 4 → 6 → 8，先取过了合理性检查的那个。
    """
    nm, x0, x1 = lay.cols[ci]
    padx = lay.rowh * 0.6
    cy = lay.row_cy[ri]
    prev_cy = lay.hdr_y if ri == 0 else lay.row_cy[ri - 1]
    next_cy = lay.row_cy[ri + 1] if ri + 1 < len(lay.row_cy) else lay.rows[ri][1] + lay.rowh * 0.9
    mid_top, mid_bot = (prev_cy + cy) / 2, (cy + next_cy) / 2
    cap = lay.rowh * 0.95

    # 两个框都试。为什么不是一个框：
    #  · 收紧框（±0.95 字高）清爽，但实测「用量 1g」只有它才能读出；
    #  · 纯中线框在药列底下挂着「金额」那类假行时会被拉得很高，可它多出来的上下文
    #    恰恰是「单价 8.21」读得出来的原因（收紧后三个倍率全读成「&21」）。
    #  所以：8 个组合挨个试，优先取过了合理性检查的那个读数；都不合格就取第一个非空。
    boxes = [(max(mid_top, cy - cap), min(mid_bot, cy + cap)), (mid_top, mid_bot)]
    ry0, ry1 = lay.rows[ri]

    got = []
    for top, bot in boxes:
        box = (int(max(0, x0 * u_scale - padx)), int(max(0, top * u_scale)),
               int(min(up.width, x1 * u_scale + padx)), int(min(up.height, bot * u_scale)))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        crop = up.crop(box)
        for k in (4.0, 6.0, 8.0):
            ws = ocr(scaled(crop, k), 'cell%d_%s%d' % (int(k), nm, ri))
            # 只留落在本行那一带里的词，防止裁进邻居行的字被算进来
            ws = [w for w in ws if ry0 - 2 <= box[1] + w.cy / k <= ry1 + 2]
            txt = ''.join(w.text for w in sorted(ws, key=lambda w: w.x))
            if debug:
                print('  [补漏] %s 第%d行 y=%d..%d ×%g → %s'
                      % (nm, ri + 1, box[1], box[3], k, txt or '(空)'))
            if txt:
                got.append(txt)
                if _cell_looks_ok(nm, norm(txt)):
                    return txt
    return got[0] if got else ''


def _meta(text: str) -> dict:
    t = norm(text)
    out = {}
    for key, pat in (('姓名', r'姓名[：:]\s*([\u4e00-\u9fff]{2,3})'),
                     ('年龄', r'年龄[：:]\s*(\d{1,3})'),
                     ('性别', r'性别[：:]\s*([男女])'),
                     ('处方号', r'处方号[：:]\s*([A-Za-z0-9]{6,20})'),
                     ('就诊日期', r'就诊日期[：:]\s*(\d{4})\D{0,2}(\d{1,2})\D{1,2}(\d{1,2})'),
                     ('金额', r'金额[：:]\s*(\d+(?:\.\d+)?)')):
        m = re.search(pat, t)
        if not m:
            continue
        if key == '就诊日期':
            y_, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= dd <= 31:      # 认残的日期（月/日为 0）宁可不报
                out[key] = '%04d-%02d-%02d' % (y_, mo, dd)
        elif key == '金额':
            out[key] = float(m.group(1))
        else:
            out[key] = m.group(1)
    return out


# ================================================================== 7. 输出
def to_link(rows: list[RxRow], url: str) -> str:
    payload = {'v': 1, 'rows': [r.to_page_row() for r in rows]}
    js = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    b64 = base64.urlsafe_b64encode(js.encode('utf-8')).decode('ascii').rstrip('=')
    return url + '#rx=' + b64


def to_paste_lines(rows: list[RxRow]) -> list[str]:
    """拼成 drug-keeper 粘贴框能吃的那种整行，方便人工复核 / 走网页解析。"""
    out = []
    for r in rows:
        spec = ('[%s]' % r.spec) if r.spec else ''
        qty = ('%g%s' % (r.qty, r.qty_unit)) if r.qty else ''
        bits = [r.name + spec, qty, ('%g' % r.price) if r.price else '',
                r.dose_raw, r.freq_raw, r.route_raw]
        out.append('  '.join(b for b in bits if b))
    return out


def fmt_num(v):
    return ('%g' % v)


def report(res: dict, path: str) -> None:
    print('=' * 78)
    print('处方单本地解析 —— %s' % os.path.basename(path))
    print('=' * 78)
    if not res.get('ok'):
        print('× 失败：%s' % res.get('reason'))
        if res.get('orientation'):
            print('  方向分数：%s' % res['orientation']['all'])
        return
    o = res['orientation']
    print('方向：顺时针 %d°（各方向得分 %s）' % (o['rot'], o['all']))
    m = res['meta']
    if m:
        print('抬头：' + '   '.join('%s=%s' % (k, v) for k, v in m.items()))
    print('-' * 78)
    print('%-22s %-8s %-6s %-8s %-10s %-7s %s' %
          ('药名', '规格', '数量', '每次', '频次', '够吃', '备注'))
    print('-' * 78)
    for r in res['rows']:
        freq = '每%d天%d次' % (r.cycle_days, r.times_per_cycle) if r.cycle_days > 1 \
            else '每日%d次' % r.times_per_cycle
        print('%-22s %-8s %-6s %-8s %-10s %-7s %s' % (
            r.name, r.spec or '-', ('%g%s' % (r.qty, r.qty_unit)) or '-',
            '%g%s' % (r.per_dose, r.unit), freq,
            (('%d天' % r.days) if r.days is not None else '-'),
            r.note or '口服'))
    print('-' * 78)
    for i, r in enumerate(res['rows'], 1):
        print('  %d) 本次开到 %g%s，每天 %s%s，够吃 %s 天%s' % (
            i, r.total, r.unit, fmt_num(round(r.daily, 3)), r.unit,
            ('%d' % r.days) if r.days is not None else '?',
            ('   ← %s' % r.warn) if r.warn else ''))
    c = res.get('check')
    if c:
        print('-' * 78)
        print('交叉校验：Σ(数量×单价) = %.2f 元，单据上写 %.2f 元 → %s'
              % (c['sum'], c['amount'], '对得上 ✓' if c['ok'] else '对不上 ✗（数量或单价列可能读错，去核对）'))
    print()
    print('可直接粘进 drug-keeper 粘贴框的整行文本：')
    for ln in to_paste_lines(res['rows']):
        print('  ' + ln)


def main():
    ap = argparse.ArgumentParser(description='处方单本地解析（Windows 内置 OCR）')
    ap.add_argument('image')
    ap.add_argument('--json', help='把结果写成 JSON')
    ap.add_argument('--url', default='', help='拼 #rx= 链接用的页面地址')
    ap.add_argument('--dump-ocr', action='store_true', help='打印每一步 OCR 的原始结果')
    a = ap.parse_args()

    res = parse_prescription(a.image, debug=a.dump_ocr)
    report(res, a.image)

    if a.url and res.get('ok'):
        print()
        print('#rx= 链接（点开就是填好的弹窗）：')
        print('  ' + to_link(res['rows'], a.url))

    if a.json:
        data = {'ok': res.get('ok'), 'orientation': res.get('orientation'),
                'meta': res.get('meta'), 'check': res.get('check'),
                'rows': [dict(r.to_page_row(), qty=r.qty, qtyUnit=r.qty_unit,
                              price=r.price, doseRaw=r.dose_raw, freqRaw=r.freq_raw,
                              routeRaw=r.route_raw, rawNameCell=r.raw_name_cell,
                              perPack=None, sources=r.sources) for r in res.get('rows', [])],
                'cells': res.get('report_rows')}
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print('\nJSON → %s' % a.json)


if __name__ == '__main__':
    main()
