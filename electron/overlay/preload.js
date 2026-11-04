"use strict";
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("overlay", Object.freeze({
  onState: callback => { ipcRenderer.on("overlay:state", (_event, payload) => callback(payload)); },
  onLevel: callback => { ipcRenderer.on("overlay:level", (_event, level) => callback(level)); },
}));
