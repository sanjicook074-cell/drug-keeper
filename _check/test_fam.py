# -*- coding: utf-8 -*-
"""
drug-keeper：全家偏少（跨人聚合页）—— 真实 DOM 驱动测试。

用户 2026-10-23 的要求，原话：
    「再加一个聚合页 里面显示的是全家人偏少的药都加载这一页」

这一页要回答的只有一个问题：**这一趟该买（开）哪些药**。所以本脚本钉这几条：
  ① 跨人：把每个人药箱里偏少的药都摊出来，按人分组；
  ② 只挑对的：只要「已用完 / 偏少」（out / soon）；
     充足（ok）、备用药（prn）、没填用法算不出天数的（na）都不许混进来；
  ③ 顺序有用：已用完排在同组最前，同档里先吃完的排前面；
  ④ 只读：不在这一页改数，但能一键切到那个人（切过去 + 弹窗关掉）；
  ⑤ 空的时候说人话：全家都不偏少 → 说清楚"都还够吃"，不是一片空白。

做法同 test_box.py：复制 index.html，在主 IIFE 末尾注入探针，无头 Chrome --dump-dom 取回。
★ 这里**必须**注入探针：familyLow() 要读 people 全局、调 level/daysLeft，
  这些都在主 IIFE 内部，外部脚本看不见。
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # = drug-keeper/
SRC = os.path.join(ROOT, "index.html")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

PROBE = r"""
/* ===== 注入的测试代码（必须插在主 IIFE 内部，否则 people / familyLow 都看不见）===== */
(function(){
  var R = { cases: [] };
  window.__T = R;
  function q(s){ return document.querySelector(s); }
  function qa(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function ck(name, cond, got){ R.cases.push({ name: name, pass: !!cond, got: got }); }
  function agoD(n){ var d = new Date(); d.setDate(d.getDate() - n); return fmtDate(d); }

  // 造一味药：默认「每天 1 片」，stock 直接给现有片数（stockDate 是今天 → 剩余天数 = stock）
  function mk(name, o){
    o = o || {};
    return {
      id: uid(), name: name, spec: '',
      perDose: (o.pd == null ? 1 : o.pd),
      unit: o.unit || '片',
      cycleDays: o.cd || 1,
      timesPerCycle: (o.tp == null ? 1 : o.tp),
      stockBase: (o.stock == null ? 30 : o.stock),
      stockDate: agoD(o.ago || 0),
      rxMax: 30,
      kind: o.kind || 'daily'
    };
  }
  function who(name, meds){
    return { id: uid(), name: name, note: '', box: { slots: defaultSlots(3) }, meds: meds };
  }

  // 两个人：甲有 用完 / 偏少 / 充足 / 备用药 / 没填用法 五种各一味，乙有 用完 / 1 天 / 10 天 / 充足
  // ★ 药名故意不含「偏少」「充足」这类档位词 —— 早先按 textContent 去掉档位字样来取药名，
  //   结果「甲三日片」被剥成「甲片」，断言就假过了。现在药名单独放在 .fam-mn 里，直接取节点。
  function fixture(){
    var jia = who('甲', [
      mk('甲空瓶片',   { stock: 0 }),
      mk('甲三日片',   { stock: 3 }),
      mk('甲六十片',   { stock: 60 }),
      mk('甲备用药',   { stock: 2, kind: 'prn', tp: 0 }),
      mk('甲没用法片', { stock: 20, tp: 0 })          // 每天 0 次 → 算不出天数 → na
    ]);
    var yi = who('乙', [
      mk('乙空瓶片', { stock: 0 }),
      mk('乙一日片', { stock: 1 }),
      mk('乙十日片', { stock: 10 }),
      mk('乙五十片', { stock: 50 })
    ]);
    people.length = 0;
    people.push(jia, yi);
    currentId = jia.id;
    syncMeds(); save(); render();
    return { jia: jia, yi: yi };
  }
  function groups(){ return qa('#famList .fam-grp'); }
  function gName(i){ return groups()[i].querySelector('.fam-n').textContent.trim(); }
  function gNames(i){
    return Array.prototype.slice.call(groups()[i].querySelectorAll('.fam-mn'))
      .map(function(e){ return e.textContent.trim(); });
  }
  function gDays(i){
    return Array.prototype.slice.call(groups()[i].querySelectorAll('.fam-day'))
      .map(function(e){ return e.textContent.trim(); });
  }
  function allNames(){
    return qa('#famList .fam-mn').map(function(e){ return e.textContent.trim(); });
  }
  function famText(){ return q('#famList').textContent.replace(/\s+/g, ' ').trim(); }

  /* ---------------- 1. 入口 ---------------- */
  var F = fixture();
  ck('顶栏有「全家偏少」这个入口', !!q('#btnFam') && /全家偏少/.test(q('#btnFam').textContent),
     q('#btnFam') ? q('#btnFam').textContent : '(没这个按钮)');
  ck('一开始弹窗是关着的', !q('#dlgFam').open, q('#dlgFam').open);
  q('#btnFam').click();
  ck('点一下打开了聚合页', q('#dlgFam').open === true, q('#dlgFam').open);
  ck('副标题说清了是几个人的药', /2 个人药箱/.test(q('#famSub').textContent),
     q('#famSub').textContent);

  /* ---------------- 2. 跨人 + 分组 ---------------- */
  ck('两个人各一组', groups().length === 2, groups().length);
  ck('组名是各自的人名（按 people 顺序）',
     gName(0) === '甲' && gName(1) === '乙', gName(0) + ' / ' + gName(1));
  ck('甲那组 2 味、乙那组 3 味',
     groups()[0].querySelector('.fam-c').textContent === '2 味' &&
     groups()[1].querySelector('.fam-c').textContent === '3 味',
     groups()[0].querySelector('.fam-c').textContent + ' / ' +
     groups()[1].querySelector('.fam-c').textContent);

  /* ---------------- 3. 只挑 out / soon ---------------- */
  var names = allNames();
  ck('一共列出 5 味（甲 2 + 乙 3）', names.length === 5, names.join('、'));
  ck('已用完的进了（甲空瓶片 / 乙空瓶片）',
     names.indexOf('甲空瓶片') >= 0 && names.indexOf('乙空瓶片') >= 0, names.join('、'));
  ck('偏少的进了', names.indexOf('甲三日片') >= 0 && names.indexOf('乙一日片') >= 0,
     names.join('、'));
  ck('充足的不进（甲六十片 / 乙五十片）',
     names.indexOf('甲六十片') < 0 && names.indexOf('乙五十片') < 0, names.join('、'));
  ck('备用药不进（它不按天吃，没有「偏少」这回事）',
     names.indexOf('甲备用药') < 0, names.join('、'));
  ck('没填用法的也不进（算不出天数，硬塞进来会是个假数）',
     names.indexOf('甲没用法片') < 0, names.join('、'));

  /* ---------------- 4. 顺序：已用完在前，同档先吃完的在前 ---------------- */
  ck('甲那组：已用完的排第一、偏少的排第二',
     gNames(0).join('、') === '甲空瓶片、甲三日片', gNames(0).join('、'));
  ck('乙那组：已用完 → 剩 1 天 → 剩 10 天',
     gNames(1).join('、') === '乙空瓶片、乙一日片、乙十日片', gNames(1).join('、'));
  ck('同一档里按剩余天数从少到多',
     gDays(1)[1].indexOf('1 天') >= 0 && gDays(1)[2].indexOf('10 天') >= 0,
     gDays(1).join(' / '));

  /* ---------------- 5. 每行该有的信息 ---------------- */
  ck('已用完那行写「已用完」，不写「还剩 0 天」', gDays(0)[0] === '已用完', gDays(0)[0]);
  ck('偏少那行写「还剩 N 天」', /^还剩 3 天$/.test(gDays(0)[1]), gDays(0)[1]);
  ck('每行带档位标签', /已用完/.test(groups()[0].querySelectorAll('.disp-name')[0].textContent) &&
     /偏少/.test(groups()[0].querySelectorAll('.disp-name')[1].textContent), '');
  ck('每行带余量', /余量/.test(groups()[0].textContent), '');
  ck('每行带用法（每天 1 片）', /每日/.test(groups()[0].textContent), '');

  /* ---------------- 6. 「当前」标记 ---------------- */
  ck('当前那个人身上有「当前」标签',
     !!groups()[0].querySelector('.fam-tag'), '甲这组有');
  ck('别人身上没有', !groups()[1].querySelector('.fam-tag'), '乙这组没有');

  /* ---------------- 7. 切人：点「去这个药箱」 ---------------- */
  ck('每组都有「去这个药箱」按钮',
     !!groups()[0].querySelector('[data-goto]') && !!groups()[1].querySelector('[data-goto]'),
     '');
  groups()[1].querySelector('[data-goto]').click();
  ck('点了之后切到乙', currentId === F.yi.id, currentId === F.yi.id ? '乙' : '没切过去');
  ck('点了之后弹窗自己关掉', q('#dlgFam').open === false, q('#dlgFam').open);
  ck('药箱列表也跟着换成乙的药了',
     /乙五十片/.test(q('#list').textContent) && !/甲六十片/.test(q('#list').textContent), '');

  /* ---------------- 8. 副标题：一个人时不写「1 个人」 ---------------- */
  people.length = 0;
  people.push(who('只有我', [ mk('孤单三日片', { stock: 2 }) ]));
  currentId = people[0].id;
  syncMeds(); save(); render(); openFam();
  ck('只有一个人时副标题写「你药箱里」，不写「1 个人药箱」',
     /你药箱里/.test(q('#famSub').textContent) && !/1 个人/.test(q('#famSub').textContent),
     q('#famSub').textContent);
  ck('一个人的偏少药照样列出来', allNames().join('、') === '孤单三日片', allNames().join('、'));

  /* ---------------- 9. 全都不偏少 → 说清楚，不是空白 ---------------- */
  people.length = 0;
  people.push(who('甲', [ mk('都够吃片', { stock: 90 }) ]));
  people.push(who('乙', [ mk('也够吃片', { stock: 90 }) ]));
  currentId = people[0].id;
  syncMeds(); save(); render(); openFam();
  ck('全家都不偏少时给出明确文案', /都还够吃/.test(famText()), famText());
  ck('这时候一组都不渲染', groups().length === 0, groups().length);
  ck('这时候也不留那条「备用药不列」的脚注（没什么需要解释的）',
     q('#famNote').textContent === '', q('#famNote').textContent);

  /* ---------------- 10. 有偏少时脚注说清为什么漏了备用药 ---------------- */
  fixture(); openFam();
  ck('有内容时脚注解释备用药和没填用法的为什么不在这页',
     /备用药不按天吃/.test(q('#famNote').textContent) &&
     /没填用法/.test(q('#famNote').textContent), q('#famNote').textContent);

  /* ---------------- 11. 关掉再打开：内容跟着数据变化刷新 ---------------- */
  q('#btnFamClose').click();
  ck('点「关闭」真的关掉', q('#dlgFam').open === false, q('#dlgFam').open);
  // 把甲的偏少药补足 → 重开后甲那组应该消失
  (function(){
    var p = people[0];
    p.meds.filter(function(m){ return m.name === '甲三日片'; })[0].stockBase = 60;
    save();
  })();
  openFam();
  ck('数据变了之后重新打开，甲那组只剩 1 味（用完那片）',
     groups()[0].querySelector('.fam-c').textContent === '1 味',
     groups()[0].querySelector('.fam-c').textContent);
  ck('补足的那味不见了', allNames().indexOf('甲三日片') < 0, allNames().join('、'));

  /* ---------------- 12. 一键复制药名（粘到微信里）----------------
     用户 2026-10-23：「下面还应该有一个一键复制 就能把缺少的药名复制出来 用户可以粘贴到微信里」
     钉三件事：① 复制出去的内容跟页面口径一致（同一份 familyLow()，不多不少）；
               ② 是给人读的纯文字（标题带日期、按人分段、段内编号），不是给机器吃的 JSON；
               ③ 真的交给剪贴板了，而且按钮有反馈；没东西可复制时按钮禁用。 */
  fixture(); openFam();
  var btn = q('#btnFamCopy');
  ck('聚合页底部有「复制药名」按钮', !!btn && /复制药名/.test(btn.textContent),
     btn ? btn.textContent : '(没有)');
  ck('有偏少时可点', !!btn && btn.disabled === false, btn ? btn.disabled : null);

  var T = famCopyText();
  var lines = T.split('\n');
  ck('首行带日期和总味数', /^全家偏少的药（\d{4}-\d{2}-\d{2}）：共 5 味$/.test(lines[0]), lines[0]);
  ck('多个人时按人分段（【甲】【乙】）',
     lines.indexOf('【甲】') >= 0 && lines.indexOf('【乙】') >= 0, lines.join(' | '));
  var iJia = lines.indexOf('【甲】');
  ck('段内编号从 1 起', /^1\. /.test(lines[iJia + 1]), lines[iJia + 1]);
  ck('药名后面带紧迫度（已用完 / 还剩 N 天）',
     /^1\. 甲空瓶片（已用完）$/.test(lines[iJia + 1]) &&
     /^2\. 甲三日片（还剩 3 天）$/.test(lines[iJia + 2]),
     lines[iJia + 1] + ' / ' + lines[iJia + 2]);
  // 口径一致：把「1. 」「（已用完）」这些包装剥掉，剩下的药名得和页面上那一列一模一样
  var txtNames = lines.filter(function(s){ return /^\d+\. /.test(s); }).map(function(s){
    return s.replace(/^\d+\. /, '').replace(/（[^）]*）$/, '');
  });
  ck('复制出去的和页面上列的是同一批药名（不多不少）',
     txtNames.slice().sort().join('、') === allNames().slice().sort().join('、'),
     txtNames.join('、') + ' vs ' + allNames().join('、'));
  ck('没把「充足 / 备用药 / 没填用法」的混进去',
     txtNames.indexOf('甲六十片') < 0 && txtNames.indexOf('甲备用药') < 0 &&
     txtNames.indexOf('甲没用法片') < 0, txtNames.join('、'));

  // 点一下：把 navigator.clipboard 换掉，看交给它的是不是同一段文字
  var copied = null, stubbed = false;
  try{
    Object.defineProperty(navigator, 'clipboard', { configurable: true,
      value: { writeText: function(t){ copied = t; return Promise.resolve(); } } });
    stubbed = true;
  } catch(e){}
  q('#btnFamCopy').click();
  ck('点了真的把文字交给剪贴板', stubbed && copied === T,
     stubbed ? (copied ? String(copied).split('\n')[0] : '(什么都没收到)') : '替换不了剪贴板');

  // 一个人时不写「【我】」（跟副标题那条同一个道理）
  people.length = 0;
  people.push(who('只有我', [ mk('孤单三日片', { stock: 2 }) ]));
  currentId = people[0].id;
  syncMeds(); save(); render(); openFam();
  var T1 = famCopyText();
  ck('只有一个人时不写「【…】」分段', T1.indexOf('【') < 0, T1.replace(/\n/g, ' | '));
  ck('一个人时也带编号', /^1\. 孤单三日片（还剩 2 天）$/.test(T1.split('\n')[1]),
     T1.split('\n')[1]);

  // 全家都不偏少 → 没有可复制的
  people.length = 0;
  people.push(who('甲', [ mk('都够吃片', { stock: 90 }) ]));
  currentId = people[0].id;
  syncMeds(); save(); render(); openFam();
  ck('全家都不偏少时没有可复制的内容', famCopyText() === '', JSON.stringify(famCopyText()));
  ck('这时候「复制药名」是禁用的', q('#btnFamCopy').disabled === true,
     q('#btnFamCopy').disabled);

  function finish(){
    var pre = document.createElement('pre');
    pre.id = '__T';
    pre.textContent = JSON.stringify(R);
    document.body.appendChild(pre);
  }
  // 「已复制 ✓」是 Promise 回调里改的（微任务之后），同步序列化抓不到 → 放到定时器里收尾
  setTimeout(function(){
    ck('复制成功后按钮短暂变成「已复制 ✓」', q('#btnFamCopy').textContent === '已复制 ✓',
       q('#btnFamCopy').textContent);
    finish();
  }, 80);
})();
"""


def main():
    src = open(SRC, encoding="utf-8").read()
    anchor = "})();\n</script>"
    if anchor not in src:
        print("找不到主 IIFE 结尾锚点")
        return 2
    i = src.rindex(anchor)
    tmpdir = tempfile.mkdtemp(prefix="dk_fam_")
    page = os.path.join(tmpdir, "t.html")
    open(page, "w", encoding="utf-8").write(src[:i] + PROBE + src[i:])

    url = "file:///" + page.replace("\\", "/")
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--allow-file-access-from-files", "--window-size=1280,1400",
           "--virtual-time-budget=8000",
           "--user-data-dir=" + os.path.join(tmpdir, "prof"), "--dump-dom", url]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    dom = r.stdout.decode("utf-8", "replace")
    dom_ns = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
    mm = re.search(r'<pre id="__T">(.*?)</pre>', dom_ns, flags=re.S)
    if not mm:
        print("!! 没拿到测试输出，DOM 长度", len(dom))
        print(dom_ns[:1500])
        return 3
    data = json.loads(html.unescape(mm.group(1)))

    fails = [c for c in data["cases"] if not c["pass"]]
    for c in data["cases"]:
        print(("  OK  " if c["pass"] else "  FAIL") + "  " + c["name"] + "   → " + str(c["got"]))
    print("\n" + "-" * 60)
    print("通过 %d / %d" % (len(data["cases"]) - len(fails), len(data["cases"])))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
