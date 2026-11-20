'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const { PORT } = require('./server');   // 跟局域网服务共用一个端口定义，别写死两处

contextBridge.exposeInMainWorld('desk', {
  // 桌面端跑的是同一份 index.html。页面靠 base 认出「我跑在桌面里」，
  // 然后把同步地址默认指向本机那个服务 —— 用户不用手填 127.0.0.1:8730。
  base: 'http://127.0.0.1:' + PORT,
  openHub: function(){ return ipcRenderer.invoke('open-hub'); },
  status: function(){ return ipcRenderer.invoke('status'); },
  pickDir: function(){ return ipcRenderer.invoke('pick-dir'); },
  rescan: function(){ return ipcRenderer.invoke('rescan'); },
  exportData: function(){ return ipcRenderer.invoke('export'); },
  openData: function(){ return ipcRenderer.invoke('open-data'); },
  openPage: function(){ return ipcRenderer.invoke('open-page'); },
  apkUrl: function(){ return ipcRenderer.invoke('apk-url'); },
  onStatus: function(cb){ ipcRenderer.on('status', function(e, s){ cb(s); }); }
});
