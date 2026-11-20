'use strict';
/* 药品管家 · 局域网服务
 *
 * 干三件事：
 *   ① 当一个「数据主档」—— 手机、网页定时把整份 localStorage 原文推上来，存在一个 JSON 里；
 *   ② 当「页面与安装包的发布源」—— 直接读项目目录里的 index.html / apk，谁连上来就发给谁；
 *   ③ 记住谁来过 —— 设备名、最后同步时间、有几味药，桌面窗口里能看见。
 *
 * ★ 设计上刻意不懂业务数据结构：data / settings 都是**原样的字符串**，原封不动存取。
 *   哪天页面里的存档结构改了，这个服务一个字都不用改。
 * ★ 同步策略保守：客户端时间比主档新才收，否则把主档发回去让它覆盖自己；
 *   覆盖前把旧主档留一份 .bak —— 合错了一次还能捞回来。
 */
const http = require('http');
const dgram = require('dgram');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const os = require('os');

const PORT = 8730;
/* 手机输配对码后，在这个 UDP 端口上广播问「电脑在哪」；电脑回自己的局域网地址。
   UDP 端口固定比 HTTP 端口大 1，自检换端口时也不会跟真机撞。 */
const PAIR_PORT = 8731;
const APP_ID = 'drug-keeper';
const APP_NAME = '药品管家';
const MAX_BODY = 8 * 1024 * 1024;      // 存档再大也就几百 KB，8MB 兜底

function md5(buf){ return crypto.createHash('md5').update(buf).digest('hex'); }

function readJson(p, dft){
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch(e){ return dft; }
}
function writeJson(p, obj){
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), 'utf8');
  fs.renameSync(tmp, p);              // 原子替换：别让断电留下半个 JSON
}
function stamp(when){
  const d = when ? new Date(when) : new Date();
  const p = function(n){ return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
         p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

class Hub {
  constructor(getCfg){
    this.getCfg = getCfg;             // () => { projectDir, dataFile }
    this.state = readJson(getCfg().dataFile, null) || this.blank();
    /* 配对码：第一次启动时生成一次，之后固定不变 —— 手机输一次就记住，
       别每次开电脑都换码，那样等于每次都要重新配一遍。 */
    if(!this.state.pairCode){
      this.state.pairCode = String(crypto.randomInt(0, 1000000)).padStart(6, '0');
      try { this.save(); } catch(e){}
    }
    this._cache = null;               // 页面 md5 的小缓存，别每 2 秒算一次 370KB
  }

  blank(){
    return {
      updatedAt: 0, updatedBy: '', data: null, settings: null,
      devices: {}, page: null, log: [], pairCode: ''
    };
  }

  save(){ writeJson(this.getCfg().dataFile, this.state); }

  /* ---------- 页面 / APK ---------- */

  pageInfo(){
    const p = path.join(this.getCfg().projectDir, 'index.html');
    let st;
    try { st = fs.statSync(p); } catch(e){ return null; }
    if(this._cache && this._cache.mtime === st.mtimeMs && this._cache.size === st.size){
      return this._cache;
    }
    const buf = fs.readFileSync(p);
    const h = md5(buf);
    this._cache = { md5: h, size: buf.length, mtime: st.mtimeMs, ver: h.slice(0, 8), path: p };
    return this._cache;
  }

  apkInfo(){
    const p = path.join(this.getCfg().projectDir, 'android', 'drug-keeper.apk');
    let st;
    try { st = fs.statSync(p); } catch(e){ return null; }
    /* 版本号直接读清单 —— 同一个仓库里的文件，比解析 APK 稳得多（不依赖 aapt） */
    let ver = '';
    try {
      const mf = fs.readFileSync(path.join(this.getCfg().projectDir, 'android', 'AndroidManifest.xml'), 'utf8');
      const m = /versionName\s*=\s*"([^"]+)"/.exec(mf);
      if(m) ver = m[1];
    } catch(e){}
    return { path: p, size: st.size, mtime: st.mtimeMs, ver: ver };
  }

  /* ---------- 对外状态（不含原始存档，列表用） ---------- */

  devices(){
    const out = [];
    const keys = Object.keys(this.state.devices || {});
    for(let i = 0; i < keys.length; i++){
      const d = this.state.devices[keys[i]];
      if(!d) continue;
      out.push({
        id: keys[i], name: d.name, kind: d.kind,
        lastSync: d.lastSync, people: d.people || 0, meds: d.meds || 0,
        pageMd5: d.pageMd5 || '', online: (Date.now() - (d.lastSync || 0)) < 3 * 60 * 1000
      });
    }
    out.sort(function(a, b){ return (b.lastSync || 0) - (a.lastSync || 0); });
    return out;
  }

  summary(){
    let people = 0, meds = 0;
    try {
      const o = JSON.parse(this.state.data || 'null');
      if(o && Array.isArray(o.people)){
        people = o.people.length;
        o.people.forEach(function(p){ meds += (p && Array.isArray(p.meds)) ? p.meds.length : 0; });
      }
    } catch(e){}
    return { people: people, meds: meds, has: !!this.state.data };
  }

  publicState(extra){
    const pg = this.pageInfo(), apk = this.apkInfo();
    const s = {
      ok: true, app: APP_ID, name: APP_NAME,
      // 服务端时钟：两端系统时间可能差好几个小时（实测电脑 23:41 / 主档 17:36），
      // 客户端拿它算一个偏移，把本机改动时间换算到同一个坐标系再比较，否则同步判断全乱。
      serverNow: Date.now(),
      pairCode: this.state.pairCode,
      updatedAt: this.state.updatedAt,
      updatedBy: this.state.updatedBy,
      updatedAtText: this.state.updatedAt ? stamp(new Date(this.state.updatedAt)) : '',
      summary: this.summary(),
      devices: this.devices(),
      page: pg ? { md5: pg.md5, ver: pg.ver, size: pg.size, mtime: pg.mtime } : null,
      apk: apk ? { size: apk.size, mtime: apk.mtime, ver: apk.ver } : null,
      log: (this.state.log || []).slice(0, 20)
    };
    if(extra) for(const k in extra) s[k] = extra[k];
    return s;
  }

  /* ---------- 收一份数据 ---------- */

  sync(body){
    const now = Date.now();
    const id = String(body.deviceId || 'unknown').slice(0, 64);
    const name = String(body.deviceName || '未命名设备').slice(0, 40);
    const kind = (body.kind === 'phone' || body.kind === 'web' || body.kind === 'desktop') ? body.kind : 'web';
    this.state.devices = this.state.devices || {};
    this.state.devices[id] = {
      name: name, kind: kind, lastSync: now,
      people: Number(body.people) || 0, meds: Number(body.meds) || 0,
      pageMd5: String(body.pageMd5 || '').slice(0, 40)
    };

    const incoming = Number(body.clientTime) || now;
    let action = 'pulled';
    /* ★ force 是「以我这台为准」：用户手点「上传到电脑」就是这个意思。
       少了这一条，主档只要时间戳比手机新（哪怕只差几秒、或者是个错的时间），
       手机上点了上传也会被静默退回主档 —— 页面那边还以为存上去了，两份数据从此分叉。 */
    if(typeof body.data === 'string' && body.data.length &&
       (body.force || !this.state.data || incoming >= this.state.updatedAt)){
      // 覆盖前先留一份旧的，同步同步着把数据弄丢是最不可接受的失败
      try {
        if(this.state.data){
          const bak = this.getCfg().dataFile.replace(/\.json$/, '') + '.bak.json';
          writeJson(bak, this.state);
        }
      } catch(e){}
      this.state.data = body.data;
      if(typeof body.settings === 'string') this.state.settings = body.settings;
      this.state.updatedAt = incoming;
      this.state.updatedBy = name;
      action = 'pushed';
      this.state.log = (this.state.log || []).slice(0, 49);
      this.state.log.unshift({ t: now, text: name + ' 传上来 ' + (Number(body.meds) || 0) + ' 味药，已存为本机主档' });
    } else if(this.state.data){
      this.state.log = (this.state.log || []).slice(0, 49);
      this.state.log.unshift({ t: now, text: name + ' 来要数据，把主档发回去了' });
    }
    this.save();
    return this.publicState({ action: action, data: this.state.data || '', settings: this.state.settings || '' });
  }
}

function lanAddresses(){
  const out = [];
  const nets = os.networkInterfaces();
  for(const k in nets){
    (nets[k] || []).forEach(function(a){
      if(a && a.family === 'IPv4' && !a.internal) out.push(a.address);
    });
  }
  return out;
}

function createHub(getCfg){ return new Hub(getCfg); }

function startServer(hub, onLog, port){
  const usePort = Number(port) || PORT;
  const server = http.createServer(function(req, res){
    const cors = {
      'Access-Control-Allow-Origin': '*',        // file:// 页面的 Origin 是 null，只能全开
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
      'Cache-Control': 'no-store'
    };
    function send(code, type, body){
      res.writeHead(code, Object.assign({ 'Content-Type': type }, cors));
      res.end(body);
    }
    function json(o){ send(200, 'application/json; charset=utf-8', JSON.stringify(o)); }

    const url = (req.url || '/').split('?')[0];
    if(url === '/api/page' || url === '/api/sync'){
      const ip = (req.socket && req.socket.remoteAddress) || '?';
      console.log('[' + stamp() + '] ' + req.method + ' ' + url + '  ← ' + ip);
    }

    if(req.method === 'OPTIONS'){ res.writeHead(204, cors); res.end(); return; }

    if(req.method === 'GET' && (url === '/api/ping' || url === '/api/state')){
      json(hub.publicState());
      return;
    }
    if(req.method === 'GET' && url === '/api/page/meta'){
      const pg = hub.pageInfo();
      json(pg ? { ok: true, md5: pg.md5, ver: pg.ver, size: pg.size, mtime: pg.mtime } : { ok: false });
      return;
    }
    if(req.method === 'GET' && url === '/api/page'){
      const pg = hub.pageInfo();
      if(!pg){ send(404, 'text/plain; charset=utf-8', '没找到 index.html'); return; }
      send(200, 'text/html; charset=utf-8', fs.readFileSync(pg.path));
      return;
    }
    if(req.method === 'GET' && url === '/api/apk'){
      const apk = hub.apkInfo();
      if(!apk){ send(404, 'text/plain; charset=utf-8', '没找到 apk'); return; }
      res.writeHead(200, Object.assign({
        'Content-Type': 'application/vnd.android.package-archive',
        'Content-Length': apk.size,
        'Content-Disposition': 'attachment; filename="drug-keeper.apk"'
      }, cors));
      fs.createReadStream(apk.path).pipe(res);
      return;
    }
    if(req.method === 'POST' && url === '/api/sync'){
      let len = 0;
      const chunks = [];
      req.on('data', function(c){
        len += c.length;
        if(len > MAX_BODY){ req.destroy(); return; }
        chunks.push(c);
      });
      req.on('end', function(){
        let body = {};
        try { body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'); } catch(e){}
        if(onLog) onLog(body.deviceName || '设备', '上传');
        json(hub.sync(body));
      });
      return;
    }
    if(req.method === 'GET' && (url === '/' || url === '/index.html')){
      send(200, 'text/html; charset=utf-8',
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
        '<title>药品管家 · 数据台</title>' +
        '<body style="font:15px/1.8 -apple-system,Segoe UI,sans-serif;padding:28px;color:#111827">' +
        '<h3 style="margin:0 0 6px">药品管家 · 数据台在跑</h3>' +
        '<p style="color:#4b5563;margin:0">手机或网页连这个地址就能同步。</p>' +
        '<p style="color:#4b5563">页面版本：<b>' + ((hub.pageInfo() || {}).ver || '—') + '</b></p>' +
        '<p><a href="/api/state">看状态 JSON</a> · <a href="/api/page">取最新页面</a> · <a href="/api/apk">取最新安装包</a></p>' +
        '</body>');
      return;
    }
    send(404, 'text/plain; charset=utf-8', '没有这个地址：' + url);
  });

  let running = false;
  let udp = null;
  function makeUdp(){
    const s = dgram.createSocket('udp4');
    s.on('error', function(){ /* 广播收不到就算了，不影响 HTTP 同步 */ });
    s.on('message', function(msg, rinfo){
      let code = '';
      try { code = msg.toString('utf8').trim().slice(0, 16); } catch(e){ return; }
      // 只有配对码对得上才回地址 —— 免得同一个 Wi-Fi 里别的机器乱连
      if(code !== hub.state.pairCode) return;
      // 按手机的来源 IP 挑同网段的那张网卡地址排在最前：电脑上常有虚拟网卡
      // （VMware / Docker / 模拟器），first address 未必是手机能到达的网段。
      let addrs = lanAddresses();
      const peer = String(rinfo.address || '');
      const p3 = peer.split('.').slice(0, 3).join('.');
      if(p3 && p3 !== peer){
        addrs = addrs.slice().sort(function(a, b){
          const ha = a.split('.').slice(0, 3).join('.') === p3 ? 0 : 1;
          const hb = b.split('.').slice(0, 3).join('.') === p3 ? 0 : 1;
          return ha - hb;
        });
      }
      const reply = Buffer.from(JSON.stringify({ addrs: addrs, port: usePort }));
      try { s.send(reply, rinfo.port, rinfo.address); } catch(e){}
    });
    return s;
  }
  return {
    start(){
      if(running) return Promise.resolve({ ok: true, port: usePort });
      return new Promise(function(resolve){
        server.once('error', function(e){ resolve({ ok: false, error: e.message }); });
        server.listen(usePort, '0.0.0.0', function(){
          running = true;
          if(!udp){ udp = makeUdp(); try { udp.bind(usePort + 1, '0.0.0.0'); } catch(e){} }
          resolve({ ok: true, port: usePort, addrs: lanAddresses() });
        });
      });
    },
    stop(){
      try { server.close(); } catch(e){}
      try { if(udp){ udp.close(); udp = null; } } catch(e){}
      running = false;
    },
    get running(){ return running; },
    port: usePort
  };
}

module.exports = { createHub, startServer, lanAddresses, PORT, PAIR_PORT, md5 };

/* 直接 `node server.js` 也能跑 —— 自检脚本就是这么起的（端口、目录、主档都能用环境变量改）。
   接 Electron 时不会走这里，那边自己 createHub + startServer。 */
if(require.main === module){
  const dir = process.env.DK_DIR || path.resolve(__dirname, '..');
  const dataFile = process.env.DK_DATA || path.join(__dirname, '.selftest-main.json');
  const hub = createHub(function(){ return { projectDir: dir, dataFile: dataFile }; });
  const s = startServer(hub, null, process.env.DK_PORT);
  s.start().then(function(r){
    process.stdout.write('READY ' + JSON.stringify(r) + '\n');
  });
}
