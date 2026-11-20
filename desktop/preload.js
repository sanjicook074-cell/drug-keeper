'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desk', {
  status: function(){ return ipcRenderer.invoke('status'); },
  pickDir: function(){ return ipcRenderer.invoke('pick-dir'); },
  rescan: function(){ return ipcRenderer.invoke('rescan'); },
  exportData: function(){ return ipcRenderer.invoke('export'); },
  openData: function(){ return ipcRenderer.invoke('open-data'); },
  openPage: function(){ return ipcRenderer.invoke('open-page'); },
  apkUrl: function(){ return ipcRenderer.invoke('apk-url'); },
  onStatus: function(cb){ ipcRenderer.on('status', function(e, s){ cb(s); }); }
});
