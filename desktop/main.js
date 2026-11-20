'use strict';
/* 药品管家 · 数据台（Electron 主进程）
 *
 * 窗口只负责看和点；真正干活的是 server.js 里那个局域网服务。
 * 关掉窗口 = 退出程序 = 服务停 —— 手机端那时就连不上了，界面上写了这句提醒。
 */
const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { createHub, startServer, lanAddresses, PORT } = require('./server');

const APP_DIR = __dirname;
const DATA_DIR = path.join(app.getPath('userData'), 'hub');
const CFG_FILE = path.join(DATA_DIR, 'config.json');
const DATA_FILE = path.join(DATA_DIR, 'main.json');

let win = null;
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
  if(win && !win.isDestroyed()) win.webContents.send('status', status());
}

async function boot(){
  cfg = readCfg();
  hub = createHub(function(){ return { projectDir: cfg.projectDir, dataFile: DATA_FILE }; });
  srv = startServer(hub, function(who){ broadcast(); });
  netOk = await srv.start();
  return netOk;
}

function createWindow(){
  win = new BrowserWindow({
    width: 960, height: 950, minWidth: 780, minHeight: 560,
    backgroundColor: '#f4f5f7',
    title: '药品管家 · 数据台',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(APP_DIR, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  });
  win.loadFile(path.join(APP_DIR, 'ui', 'index.html'));
  win.on('closed', function(){ win = null; });
}

app.whenReady().then(async function(){
  await boot();
  createWindow();

  // 界面上每 3 秒刷一次：设备是不是还在线、页面版本有没有变，都靠这个轮询
  setInterval(function(){ if(win) broadcast(); }, 3000);

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
  broadcast();
  return status();
});

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
