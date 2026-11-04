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
  micError: reason => ipcRenderer.send("voice:mic-error", { reason: String(reason || "mic error").slice(0, 160) }),
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
    setMode: mode => ipcRenderer.invoke("dock:mode", { mode }),
    setGlass: patch => ipcRenderer.invoke("dock:glass", patch),
    setPinned: pinned => ipcRenderer.invoke("dock:pin", { pinned }),
    focus: focus => ipcRenderer.invoke("dock:focus", { focus }),
    reportRect: rect => ipcRenderer.send("dock:rect", rect),
    attention: reason => ipcRenderer.send("dock:attention", { reason }),
  }),
  // Main-process pushes: {type:"voice-state"|"transcript"|"intent", ...}
  onEvent: callback => subscribe("commander:push", callback),
  voice,
  platform: process.platform,
}));
