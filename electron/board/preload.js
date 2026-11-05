"use strict";
const { contextBridge, ipcRenderer } = require("electron");
// The board window only receives a ready-made view model and can only ask for a page change.
contextBridge.exposeInMainWorld("boardApi", Object.freeze({
  onCards: callback => { ipcRenderer.on("board:cards", (_event, cards) => callback(cards)); },
  onView: callback => { ipcRenderer.on("board:view", (_event, model) => callback(model)); },
  page: delta => ipcRenderer.send("board:page", { delta: delta > 0 ? 1 : -1 }),
}));
