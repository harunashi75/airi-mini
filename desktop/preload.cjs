'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('airiSettings', {
    readTheme: () => ipcRenderer.invoke('settings:read-theme'),
    saveTheme: theme => ipcRenderer.invoke('settings:save-theme', theme),
    chooseModel: () => ipcRenderer.invoke('settings:choose-model'),
    openData: () => ipcRenderer.invoke('settings:open-data'),
    openLog: () => ipcRenderer.invoke('settings:open-log')
});

if (location.protocol === 'file:') {
    contextBridge.exposeInMainWorld('airiSetup', {
        chooseModel: () => ipcRenderer.invoke('setup:choose'),
        retry: () => ipcRenderer.invoke('setup:retry'),
        subscribe: callback => ipcRenderer.on('setup:state', (_event, state) => callback(state))
    });
} else if (location.hostname === '127.0.0.1') {
    contextBridge.exposeInMainWorld('airiDesktop', {
        readSession: () => ipcRenderer.invoke('session:read'),
        saveSession: id => ipcRenderer.invoke('session:save', id)
    });
}
