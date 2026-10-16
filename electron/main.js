"use strict";
const {app,BrowserWindow,ipcMain}=require("electron"); const path=require("node:path");
const {pathToFileURL}=require("node:url");
const {validateCommand,validateBaseUrl,validateCursor,validateTargetPin}=require("./protocol.js");
const BASE_URL=validateBaseUrl(process.env.VOI_COMMANDER_URL||"http://127.0.0.1:8765");
const rendererFile=path.join(__dirname,"renderer","index.html");
const CHANNELS=Object.freeze({status:"commander:status",command:"commander:command",capabilities:"commander:capabilities",registry:"commander:registry",targetPin:"commander:target-pin",events:"commander:events"});
async function requestJson(pathname, options = {}) {
  const response = await fetch(new URL(pathname, BASE_URL), {
    ...options, redirect: "error", signal: AbortSignal.timeout(15000),
    headers: {accept:"application/json", "content-type":"application/json", ...(process.env.VOI_COMMANDER_TOKEN ? {Authorization: `Bearer ${process.env.VOI_COMMANDER_TOKEN}`} : {})},
  });
  const payload=await response.json();
  if(!response.ok) throw new Error(payload.error || `Commander HTTP ${response.status}`);
  return payload;
}
function registerIpc() {
  const handle = (channel, callback) => ipcMain.handle(channel, (event, input) => {
    if (!event.senderFrame || event.senderFrame !== event.sender.mainFrame || event.senderFrame.url !== pathToFileURL(rendererFile).href) throw new Error("Untrusted IPC sender");
    return callback(input);
  });
  handle(CHANNELS.status, () => requestJson("/api/micromachine/status"));
  handle(CHANNELS.capabilities, () => requestJson("/api/direct/capabilities"));
  handle(CHANNELS.registry, () => requestJson("/api/direct/registry"));
  handle(CHANNELS.targetPin, input => requestJson("/api/direct/registry/target-pin", {method:"POST", body:JSON.stringify(validateTargetPin(input))}));
  handle(CHANNELS.events, input => {
    const after = validateCursor(input && input.after);
    return requestJson(`/api/history?after=${after}`);
  });
  handle(CHANNELS.command, input => requestJson("/api/micromachine/modulate", {method:"POST", body:JSON.stringify({...validateCommand(input), async_publish:true})}));
}
function createWindow() {
  const win = new BrowserWindow({width:1100,height:760,minWidth:760,minHeight:520,backgroundColor:"#07171d",webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,preload:path.join(__dirname,"preload.js")}});
  win.webContents.setWindowOpenHandler(() => ({action:"deny"}));
  win.webContents.on("will-navigate", event => event.preventDefault());
  win.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  win.webContents.session.webRequest.onHeadersReceived((details, callback) => callback({responseHeaders:{...details.responseHeaders,"Content-Security-Policy":["default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'"]}}));
  win.loadFile(rendererFile);
}
app.whenReady().then(()=>{registerIpc();createWindow();app.on("activate",()=>{if(BrowserWindow.getAllWindows().length===0)createWindow();});});app.on("window-all-closed",()=>{if(process.platform!=="darwin")app.quit();});
module.exports={CHANNELS,validateCommand,requestJson};
