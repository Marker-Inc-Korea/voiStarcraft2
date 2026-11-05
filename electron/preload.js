"use strict";
const { contextBridge, ipcRenderer } = require("electron");
// Only semantic operations are exposed; renderer cannot access Node, Python, the network, or raw SC2 methods.
const subscribe = (channel, callback) => {
  if (typeof callback !== "function") throw new TypeError("callback required");
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};
const voice = Object.freeze({
  start: () => ipcRenderer.invoke("voice:start", {}),
  stop: () => ipcRenderer.invoke("voice:stop", {}),
  state: () => ipcRenderer.invoke("voice:state"),
  onState: callback => subscribe("voice:state", callback),
  // Renderer-local mic pipeline hands raw pcm16 (24 kHz mono) to Main; Main owns the socket + secret.
  sendAudio: chunk => ipcRenderer.send("voice:audio", chunk),
  sendLevel: level => ipcRenderer.send("voice:level", level),
  ptt: down => ipcRenderer.send("voice:ptt", { down: Boolean(down) }),
  micError: reason => ipcRenderer.send("voice:mic-error", { reason: String(reason || "mic error").slice(0, 160) }),
});
const settings = Object.freeze({
  get: () => ipcRenderer.invoke("settings:get"),
  set: patch => ipcRenderer.invoke("settings:set", patch),
  reset: group => ipcRenderer.invoke("settings:reset", group ? { group } : {}),
  open: open => ipcRenderer.invoke("settings:open", { open: Boolean(open) }),
  probeServer: url => ipcRenderer.invoke("server:probe", { url }),
  checkShortcut: accelerator => ipcRenderer.invoke("shortcut:check", { accelerator }),
  previewRainbow: () => ipcRenderer.invoke("rainbow:preview", {}),
});
contextBridge.exposeInMainWorld("commander", Object.freeze({
  status: () => ipcRenderer.invoke("commander:status"),
  runtimeStatus: () => ipcRenderer.invoke("commander:runtime-status"),
  capabilities: () => ipcRenderer.invoke("commander:capabilities"),
  registry: () => ipcRenderer.invoke("commander:registry"),
  registerTargetPin: pin => ipcRenderer.invoke("commander:target-pin", pin),
  events: (after = 0) => ipcRenderer.invoke("commander:events", { after }),
  sendCommand: (text, responseLanguage = "ko") => ipcRenderer.invoke("commander:command", { text, response_language: responseLanguage }),
  dock: Object.freeze({
    state: () => ipcRenderer.invoke("dock:state"),
    focus: focus => ipcRenderer.invoke("dock:focus", { focus }),
    reportRect: rect => ipcRenderer.send("dock:rect", rect),
    attention: reason => ipcRenderer.send("dock:attention", { reason }),
  }),
  settings,
  // Main-process pushes: {type:"voice-state"|"transcript"|"intent"|"dock-state"|"settings"|"session", ...}
  onEvent: callback => subscribe("commander:push", callback),
  voice,
  platform: process.platform,
}));
