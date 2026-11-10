"use strict";
const { contextBridge, ipcRenderer } = require("electron");
// The sketch window receives ready-made layout/route events and can only send back its strokes, a cancel, and a calibration result.
const on = channel => callback => { ipcRenderer.on(channel, (_event, payload) => callback(payload)); };
contextBridge.exposeInMainWorld("sketchApi", Object.freeze({
  onLayout: on("sketch:layout"), onMode: on("sketch:mode"), onCollect: on("sketch:collect"), onNativeStep: on("sketch:native-step"), onRouteEvent: on("sketch:route-event"), onRouteReset: on("sketch:route-reset"),
  onSubmitted: on("sketch:submitted"), onSettle: on("sketch:settle"), onClear: on("sketch:clear"), onSkin: on("sketch:skin"),
  strokes: strokes => ipcRenderer.send("sketch:strokes", { strokes }),
  cancel: reason => ipcRenderer.send("sketch:cancel", { reason: reason === "tap" ? "tap" : "esc" }),
  calibration: (action, rect) => ipcRenderer.send("sketch:calibration", { action, rect }),
}));
