const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("spatialScopeDesktop", {
  platform: process.platform,
  versions: Object.freeze({ ...process.versions }),
});
