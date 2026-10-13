#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/rx_local/rx_local.py 的自检 —— 只测纯函数：不要图片、不要 Windows OCR 语言包。

为什么单开一个：真识别得有中文 OCR 语言包 + 一张真处方才能跑，
但「归一化 / 表头定位 / 规格拆解 / 频次 / 行模型」这些是纯的，随时可验。
其中 `_match_keyword` 一条对应的是真实踩过的坑（见注释），别删。

    python _check/test_rx_local.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'rx_local'))

try:
    import rx_local as X
except Exception as e:                                    # pragma: no cover
    print('× 没法导入 rx_local（%r）。缺依赖的话：pip install Pillow' % e)
    sys.exit(2)

N = 0
BAD = []


def ck(name, cond, got=None):
    global N
    N += 1
    if cond:
        print('  ✓ %s' % name)
    else:
        print('  × %s   实际=%r' % (name, got))
        BAD.append(name)


def W(text, x, y, w=20.0, h=20.0):
    return X.Word(text, float(x), float(y), float(w), float(h))


print('== 1. 归一化 norm()（OCR 会往汉字之间塞空格、把标点弄成全角）==')
ck('全角 → 半角', X.norm('１２３ＡＢ') == '123AB', X.norm('１２３ＡＢ'))
ck('汉字之间的空格全部去掉', X.norm('阿 奇 霉 素 片') == '阿奇霉素片', X.norm('阿 奇 霉 素 片'))
ck('〔〕 统一成 []', X.norm('〔0.5g*60]') == '[0.5g*60]', X.norm('〔0.5g*60]'))
ck('数字之间的全角逗号 → 小数点', X.norm('0，5g') == '0.5g', X.norm('0，5g'))
ck('数字之间的 ASCII 逗号 → 小数点', X.norm('8,21') == '8.21', X.norm('8,21'))
ck('数字之间的全角句点 → 小数点', X.norm('6．12') == '6.12', X.norm('6．12'))
ck('衬线体 1 被认成 i → 掰回数字', X.norm('ig') == '1g', X.norm('ig'))
ck('不会把「一次性」这类字里正常字母掰坏', X.norm('每日2次') == '每日2次', X.norm('每日2次'))
ck('keep_space=True 时折成一个空格', ' ' in X.norm('每 日 2 次', keep_space=True), X.norm('每 日 2 次', keep_space=True))

print('== 2. 规格 / 药名拆解 split_name_spec() ==')
n, s = X.split_name_spec('阿奇霉素片[华康][0.5g*60]')
ck('从多个方括号里挑出真规格（带 *N 的那个）', (n, s) == ('阿奇霉素片', '0.5g*60'), (n, s))
n, s = X.split_name_spec('叶酸片[康贝][4mg*30]')
ck('第二位规格也挑得对', (n, s) == ('叶酸片', '4mg*30'), (n, s))
n, s = X.split_name_spec('阿莫西林胶囊(康贝)[0.25g*8]')
ck('圆括号里的商品名不影响药名', (n, s) == ('阿莫西林胶囊', '0.25g*8'), (n, s))
n, s = X.split_name_spec('，阿奇霉素片[华康][0.5g*60]')
ck('抠列带进来的前导标点要剪掉', n == '阿奇霉素片', n)

print('== 3. 频次 parse_freq() ==')
for txt, want in [('每日2次', (1, 2, True)), ('每日一次', (1, 1, True)), ('每日3次', (1, 3, True)),
                  ('隔日', (2, 1, True)), ('睡前', (1, 1, True)), ('每周2次', (7, 2, True)),
                  ('每2周一次', (14, 1, True))]:
    got = X.parse_freq(txt)
    ck('%s → %s' % (txt, want), got == want, got)
ck('认不出来时如实返回 False（不许瞎猜）', X.parse_freq('遵医嘱')[2] is False, X.parse_freq('遵医嘱'))

print('== 4. 表头定位 _match_keyword() —— 这条是踩过的坑 ==')
# 实测：OCR 会把左栏「诊断/高血压」和右栏「药品名称」串进同一条行，
# 而且 `R：` 正好插在「药」和「品」中间。直接拼词永远拼不出「药品名称」，
# 必须「允许跳过不含目标字的词、但不许跨行」才找得到。下面就是这条真实数据的缩影：
words = [W('诊', 321, 267), W('断', 343, 267), W('高', 322, 300), W('血', 344, 300), W('压', 366, 300),
         W('药', 523, 300), W('R：', 525, 267), W('品', 546, 300), W('名', 590, 300), W('称', 612, 300)]
got = X._match_keyword(words, '药品名称', None, 20.0)
ck('隔着 R： 也能拼出「药品名称」', got is not None and got[0] == 523, got)
bad = X._match_keyword([W('药', 523, 100), W('品', 546, 400), W('名', 590, 700), W('称', 612, 900)],
                       '药品名称', None, 20.0)
ck('跨行错配要拒绝（y 差太远不算）', bad is None, bad)
ck('找不到就返回 None', X._match_keyword([W('数量', 991, 300)], '药品名称', None, 20.0) is None)

print('== 5. 数字格的合理性检查 _cell_looks_ok() ==')
ck('数量 2盒 合格', X._cell_looks_ok('数量', '2盒'))
ck('单价 6.12 合格', X._cell_looks_ok('单价', '6.12'))
ck('用量 1g 合格', X._cell_looks_ok('用量', '1g'))
ck('用量 空 不合格', not X._cell_looks_ok('用量', ''))
ck('单价「&21」不合格（8 被认成 & ，认残了就不许当数用）', not X._cell_looks_ok('单价', '&21'))
ck('频次 每日2次 合格', X._cell_looks_ok('频次', '每日2次'))

print('== 6. 行模型 build_row() / 每天用量 / 够吃天数 ==')
warns = []
r = X.build_row('阿奇霉素片[华康][0.5g*60]', '2盒', '6.12', '1g', '每日2次', '口服', warns)
ck('药名', r.name == '阿奇霉素片', r.name)
ck('规格', r.spec == '0.5g*60', r.spec)
ck('每次用量 = 1g ÷ 0.5g = 2 片', r.per_dose == 2, r.per_dose)
ck('单位按药名推成「片」', r.unit == '片', r.unit)
ck('本次开到 = 2 盒 × 60 片', r.total == 120, r.total)
ck('每天 4 片、够吃 30 天', (round(r.daily, 3), r.days) == (4.0, 30), (r.daily, r.days))
ck('口服不写进备注', r.note == '', r.note)
ck('这一行没有告警', r.warn == '' and not warns, (r.warn, warns))

warns2 = []
r2 = X.build_row('叶酸片[康贝][4mg*30]', '1盒', '8.21', '4mg', '每日一次', '口服', warns2)
ck('同单位相除得 1 片', r2.per_dose == 1, r2.per_dose)
ck('1 盒 × 30 片 ÷ 每天 1 片 = 30 天', r2.days == 30, r2.days)

r3 = X.build_row('维生素B12注射液[甲钴胺][1ml:0.5mg*]', '1盒', '15.60', '', '每两周一次', '皮下注射', [])
ck('用法不是口服 → 写进备注', r3.note == '皮下注射', r3.note)
ck('规格里没有 *N → 本次开到按 1 份算（并会告警，让人核对）', r3.total == 1, r3.total)

warns4 = []
X.build_row('未知药片[10mg*30]', '2盒', '3.00', '空', '每日2次', '口服', warns4)
ck('用量格读空 → 明确告警，绝不假装读到了', any('用量' in w for w in warns4), warns4)

print('== 7. 非药品行的闸门 _looks_like_drug() ==')
ck('药名列下面的「金额」不是药', not X._looks_like_drug('金额:20.45元'))
ck('「审核/调配:核对/发药」不是药', not X._looks_like_drug('审核/调配:核对/发药'))
ck('「如有任何用药问题,请咨询我院药师」不是药', not X._looks_like_drug('如有任何用药问题,请咨询我院药师'))
ck('真药行是药', X._looks_like_drug('阿奇霉素片[华康][0.5g*60]'))
ck('空 → 不是', not X._looks_like_drug(''))

print('== 8. 方向打分 score_orientation() ==')
right = [W('药品名称', 523, 300), W('阿奇霉素片', 518, 333), W('数量', 991, 300), W('用量', 1171, 305)]
wrong = [W('乃毋唰牖', 347, 79), W('刳彳', 259, 1518)]
ck('正过来的处方分更高', X.score_orientation(right) > X.score_orientation(wrong),
   (X.score_orientation(right), X.score_orientation(wrong)))

print('== 9. 行带分家 row_yband() —— 这条也是踩过的坑 ==')
# 坐标照抄用户那张真底方。find_layout 给行带上下各留 ±0.5 字高，相邻两行因此重叠 8px，
# 落在重叠带里的字被上下两行**同时认领** —— 第 0 行「用法」格于是读出「口服服」，
# 多出来的那个「服」(cy=366.5) 其实是第 1 行的字。
lay = X.Layout(name_x0=500.0, hdr_y=310.0,
               cols=[('药品名称', 500.0, 960.0), ('用法', 1427.5, 1589.0)],
               rows=[(322.5, 368.5), (360.5, 406.5), (725.5, 772.5)],
               rowh=21.0, table_bottom=790.0, row_cy=[345.5, 383.5, 749.0])
b0, b1 = X.row_yband(lay, 0), X.row_yband(lay, 1)
ck('第 0 行只把下边界收到重叠段中点（368.5→364.5），上边界不动',
   (round(b0[0], 1), round(b0[1], 1)) == (322.5, 364.5), b0)
ck('第 1 行上边界收到同一处（364.5）', round(b1[0], 1) == 364.5, b1)
ck('第 1 行那个「服」(cy=366.5) 不再被第 0 行认领', not (b0[0] <= 366.5 <= b0[1]), b0)
ck('第 0 行**自己的**「服」(cy=325.5) 一根手指都没碰掉', b0[0] <= 325.5 <= b0[1], b0)
ck('不重叠的行原样不动（不是「与邻行中心线取中」那套）',
   X.row_yband(lay, 2) == lay.rows[2], X.row_yband(lay, 2))
ck('任何一行只会收缩、绝不外扩',
   all(X.row_yband(lay, i)[0] >= lay.rows[i][0] - 1e-9
       and X.row_yband(lay, i)[1] <= lay.rows[i][1] + 1e-9 for i in range(3)),
   [X.row_yband(lay, i) for i in range(3)])
# 单词高 20 → cy = y + 10，所以 y 传 cy−10（照抄真单上那四字的 cy）
route_words = [W('口', 1444, 318.0), W('服', 1468, 315.5),      # 第 0 行 (cy=328.0 / 325.5)
               W('口', 1449, 359.0), W('服', 1473, 356.5)]      # 第 1 行 (cy=369.0 / 366.5)
t0, _ = X.cell_text(route_words, 1427.5, 1589.0, *X.row_yband(lay, 0))
t1, _ = X.cell_text(route_words, 1427.5, 1589.0, *X.row_yband(lay, 1))
ck('整格结果 第 0 行「用法」= 口服（不是「口服服」）', t0 == '口服', t0)
ck('整格结果 第 1 行「用法」= 口服', t1 == '口服', t1)

print('== 10. 按张对账 sheets_of() / 归属 patient_of() ==')
# ★ 2026-10-13 事故后加的。当时工具**算出了**对账、也**读到了**抬头，可 to_link 只塞了 rows ——
#   两样都在拼链接那一步丢了（验证做了、没传出来，等于没做）。
#   而且 patient_of 第一版照抄了 meta 的**中文键**（姓名/性别），页面 patient.name 取不到，
#   弹窗顶上照样打印"底方上没写姓名"、把药记到当前人名下。这两条就钉住这两处。
import json as _json
import base64 as _b64

res1 = {'meta': {'姓名': '甲', '性别': '男', '年龄': '63', '金额': 20.45},
        'rows': [1, 2], 'check': {'sum': 20.45, 'amount': 20.45, 'ok': True}}
res2 = {'meta': {'金额': 6.58}, 'rows': [1], 'check': {'sum': 6.58, 'amount': 6.58, 'ok': True}}
sh = X.sheets_of([res1, res2])
ck('按张出对账：一张一条，带 i / n / amount / sum',
   sh == [{'i': 1, 'n': 2, 'amount': 20.45, 'sum': 20.45},
          {'i': 2, 'n': 1, 'amount': 6.58, 'sum': 6.58}], sh)
ck('金额没读到 → amount 写 None（不编一个数糊过去）',
   X.sheets_of([{'meta': {}, 'rows': [1], 'check': None}])
   == [{'i': 1, 'n': 1, 'amount': None, 'sum': None}], '')

p = X.patient_of([res1, res2])
ck('归属用英文键（页面 patient.name 才取得到）',
   p.get('name') == '甲' and p.get('sex') == '男' and p.get('age') == '63', p)
ck('只认第 1 张抬头（第 2 张没姓名，不许把两张混起来）', p.get('name') == '甲', p)
ck('patient 里不夹无关字段（金额不进 patient）', '金额' not in p and 'amount' not in p, p)


def _payload(link):
    b = link.split('#rx=')[1].replace('-', '+').replace('_', '/')
    b += '=' * (-len(b) % 4)
    return _json.loads(_b64.b64decode(b).decode('utf-8'))


ROW1 = X.RxRow(name='测试片', total=3)
pl = _payload(X.to_link([ROW1], 'X', p, sh))
ck('to_link 把 patient 一起编进去了', pl.get('patient', {}).get('name') == '甲', pl.get('patient'))
ck('to_link 把 sheets 一起编进去了（逐张对账不再被丢掉）',
   len(pl.get('sheets') or []) == 2 and pl['sheets'][0]['amount'] == 20.45, pl.get('sheets'))
ck('to_link 不传 patient / sheets 时也照旧能编（老调用姿势仍可用）',
   isinstance(_payload(X.to_link([ROW1], 'X')), dict), '')

print('== 11. 不联网、不额外依赖 ==')
src = open(os.path.join(ROOT, 'tools', 'rx_local', 'rx_local.py'), encoding='utf-8').read()
ck('没有 requests/urllib 之类的联网调用',
   all(k not in src for k in ('requests.', 'urllib.request', 'http.client')), '有联网调用')
ck('没有把图片上传到任何地方',
   'upload' not in src.lower(), '出现 upload')

print()
print('——— %d 项，失败 %d 项 ———' % (N, len(BAD)))
if BAD:
    for b in BAD:
        print('  × ' + b)
    sys.exit(1)
print('全部通过 ✓')
