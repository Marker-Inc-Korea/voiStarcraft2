"use strict";
const {contextBridge,ipcRenderer}=require("electron");
// Only semantic operations are exposed; renderer cannot access Node, Python, or raw SC2 methods.
contextBridge.exposeInMainWorld("commander",Object.freeze({status:()=>ipcRenderer.invoke("commander:status"),capabilities:()=>ipcRenderer.invoke("commander:capabilities"),sendCommand:(text,responseLanguage="ko")=>ipcRenderer.invoke("commander:command",{text,response_language:responseLanguage})}));
