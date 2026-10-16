"use strict";
const {contextBridge,ipcRenderer}=require("electron");
// Only semantic operations are exposed; renderer cannot access Node, Python, or raw SC2 methods.
contextBridge.exposeInMainWorld("commander",Object.freeze({status:()=>ipcRenderer.invoke("commander:status"),capabilities:()=>ipcRenderer.invoke("commander:capabilities"),registry:()=>ipcRenderer.invoke("commander:registry"),registerTargetPin:(pin)=>ipcRenderer.invoke("commander:target-pin",pin),events:(after=0)=>ipcRenderer.invoke("commander:events",{after}),sendCommand:(text,responseLanguage="ko")=>ipcRenderer.invoke("commander:command",{text,response_language:responseLanguage})}));
