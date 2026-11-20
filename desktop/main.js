'use strict';
/* 药品管家 · 桌面版（Electron 主进程）
 *
 * ★ 2026-11-20 用户把定位说清楚了：「桌面版完全复刻网页版的内容，再加上和手机端同步。
 *   网页端只是展示，真正用起来是桌面端 + 手机端。」
 *   所以主窗口直接加载项目里的 index.html —— 跟网页版、手机壳里是**同一份文件**，
 *   不是另写一套界面。原来的「数据台」退成一个设置窗口，从应用里的「数据台设置」打开。
 *   局域网上服务随程序一起起，关掉程序 = 服务停（手机那时就连不上了，控制台里写了）。
 */
const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { createHub, startServer, lanAddresses, PORT } = require('./server');

const APP_DIR = __dirname;
const DATA_DIR = path.join(app.getPath('userData'), 'hub');
const CFG_FILE = path.join(DATA_DIR, 'config.json');
const DATA_FILE = path.join(DATA_DIR, 'main.json');

let win = null;       // 主窗口：完整应用（index.html）
let hubWin = null;    // 设置窗口：数据台控制台（ui/index.html）
let hub = null;
let srv = null;
let netOk = { ok: false, error: '还没启动' };

function readCfg(){
  let c = null;
  try { c = JSON.parse(fs.readFileSync(CFG_FILE, 'utf8')); } catch(e){ c = null; }
  if(!c || !c.projectDir) c = { projectDir: path.resolve(APP_DIR, '..') };
  return c;
}
function saveCfg(c){
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(CFG_FILE, JSON.stringify(c, null, 2), 'utf8');
}

let cfg = null;

function status(){
  const st = hub.publicState();
  st.server = {
    running: !!(srv && srv.running),
    port: PORT,
    addresses: lanAddresses(),
    error: netOk.ok ? '' : (netOk.error || '')
  };
  st.paths = { projectDir: cfg.projectDir, dataFile: DATA_FILE };
  return st;
}

function broadcast(){
  // 只有数据台控制台订阅 status；主窗口是完整应用，不需要这个消息。
  if(hubWin && !hubWin.isDestroyed()) hubWin.webContents.send('status', status());
}

async function boot(){
  cfg = readCfg();
  hub = createHub(function(){ return { projectDir: cfg.projectDir, dataFile: DATA_FILE }; });
  srv = startServer(hub, function(who){ broadcast(); });
  netOk = await srv.start();
  return netOk;
}

function prefs(){
  return {
    preload: path.join(APP_DIR, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: false
  };
}

/* 主窗口 = 网页版那一份，一字不改地加载。
   ★ 必须用 cfg.projectDir 下的 index.html（也就是手机和网页共用的那一份），
     不能拿 desktop/ui/index.html 顶替 —— 那就退回成只有同步面板的旧形态了。 */
function createWindow(){
  win = new BrowserWindow({
    width: 1280, height: 900, minWidth: 1000, minHeight: 640,
    backgroundColor: '#fff',
    title: '药品管家',
    autoHideMenuBar: true,
    webPreferences: prefs()
  });
  win.loadFile(path.join(cfg.projectDir, 'index.html'));
  win.on('closed', function(){ win = null; });
}

/* 数据台控制台：看设备、看主档、分发 APK、换项目目录。
   单独一个窗口，免得把完整应用的界面挤掉。 */
function createHubWindow(){
  if(hubWin && !hubWin.isDestroyed()){ hubWin.focus(); return; }
  hubWin = new BrowserWindow({
    width: 900, height: 850, minWidth: 720, minHeight: 560,
    parent: win,
    backgroundColor: '#f4f5f7',
    title: '药品管家 · 数据台',
    autoHideMenuBar: true,
    webPreferences: prefs()
  });
  hubWin.loadFile(path.join(APP_DIR, 'ui', 'index.html'));
  hubWin.on('closed', function(){ hubWin = null; });
}

app.whenReady().then(async function(){
  await boot();
  createWindow();

  // 控制台每 3 秒刷一次：设备是不是还在线、页面版本有没有变，都靠这个轮询
  setInterval(broadcast, 3000);

  app.on('activate', function(){
    if(BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', function(){
  if(srv) srv.stop();
  app.quit();
});

/* ---------------- 给界面用的几件事 ---------------- */

ipcMain.handle('status', function(){ return status(); });

ipcMain.handle('pick-dir', async function(){
  const r = await dialog.showOpenDialog(win, {
    title: '选「药品管家」项目文件夹（里面有 index.html）',
    defaultPath: cfg.projectDir,
    properties: ['openDirectory']
  });
  if(r.canceled || !r.filePaths.length) return status();
  const dir = r.filePaths[0];
  if(!fs.existsSync(path.join(dir, 'index.html'))){
    return Object.assign(status(), { error: '这个文件夹里没有 index.html' });
  }
  cfg.projectDir = dir;
  saveCfg(cfg);
  hub = createHub(function(){ return { projectDir: cfg.projectDir, dataFile: DATA_FILE }; });
  if(srv) srv.stop();
  srv = startServer(hub, function(){ broadcast(); });
  netOk = await srv.start();
  // 换了项目目录，主窗口也得跟着换成那目录里的 index.html
  if(win && !win.isDestroyed()) win.loadFile(path.join(cfg.projectDir, 'index.html'));
  broadcast();
  return status();
});

ipcMain.handle('open-hub', function(){ createHubWindow(); return true; });

ipcMain.handle('rescan', function(){
  hub._cache = null;                     // 丢掉 md5 缓存，强制重算（换了页面文件时用）
  broadcast();
  return status();
});

ipcMain.handle('export', async function(){
  const r = await dialog.showSaveDialog(win, {
    title: '把主档存一份',
    defaultPath: '药品管家-主档备份.json',
    filters: [{ name: 'JSON', extensions: ['json'] }]
  });
  if(r.canceled || !r.filePath) return { ok: false };
  const st = hub.state;
  const out = {
    exportedAt: new Date().toISOString(),
    app: 'drug-keeper',
    data: st.data || '',
    settings: st.settings || ''
  };
  fs.writeFileSync(r.filePath, JSON.stringify(out, null, 2), 'utf8');
  return { ok: true, path: r.filePath };
});

ipcMain.handle('open-data', function(){
  fs.mkdirSync(DATA_DIR, { recursive: true });
  shell.showItemInFolder(DATA_FILE);
  return true;
});

ipcMain.handle('open-page', function(){
  shell.showItemInFolder(path.join(cfg.projectDir, 'index.html'));
  return true;
});

/* 让手机去装 APK：服务已经在发 /api/apk，这里只把地址给界面显示 */
ipcMain.handle('apk-url', function(){
  const addrs = lanAddresses();
  return addrs.length ? 'http://' + addrs[0] + ':' + PORT + '/api/apk' : '';
});
