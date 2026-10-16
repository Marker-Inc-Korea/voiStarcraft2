"use strict";
const {app,BrowserWindow,ipcMain}=require("electron"); const path=require("node:path");
const BASE_URL=process.env.VOI_COMMANDER_URL||"http://127.0.0.1:8765"; const {validateCommand}=require("./protocol.js");
const CHANNELS=Object.freeze({status:"commander:status",command:"commander:command",capabilities:"commander:capabilities"});
async function requestJson(pathname,options={}){const response=await fetch(new URL(pathname,BASE_URL),{...options,headers:{accept:"application/json","content-type":"application/json",...(options.headers||{})}});const payload=await response.json();if(!response.ok)throw new Error(payload.error||`Commander HTTP ${response.status}`);return payload;}
function registerIpc(){ipcMain.handle(CHANNELS.status,()=>requestJson("/api/runtime/status"));ipcMain.handle(CHANNELS.capabilities,()=>requestJson("/api/direct/capabilities"));ipcMain.handle(CHANNELS.command,(_event,input)=>requestJson("/api/command",{method:"POST",body:JSON.stringify(validateCommand(input))}));}
function createWindow(){const win=new BrowserWindow({width:1100,height:760,minWidth:760,minHeight:520,backgroundColor:"#07171d",webPreferences:{contextIsolation:true,nodeIntegration:false,preload:path.join(__dirname,"preload.js")}});win.loadFile(path.join(__dirname,"renderer","index.html"));}
app.whenReady().then(()=>{registerIpc();createWindow();app.on("activate",()=>{if(BrowserWindow.getAllWindows().length===0)createWindow();});});app.on("window-all-closed",()=>{if(process.platform!=="darwin")app.quit();});
module.exports={CHANNELS,validateCommand,requestJson};
