const { app, BrowserWindow, Menu, dialog, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const log = require("electron-log/main");
const { autoUpdater } = require("electron-updater");

const APP_ID = "com.fengshuoliu.SpatialScope";
const MANUAL_URL = "https://github.com/fengshuoliu/SpatialScope/blob/main/docs/SpatialScope_User_Manual.md";
const RELEASE_URL = "https://github.com/fengshuoliu/SpatialScope/releases/latest";

let mainWindow = null;
let backendProcess = null;
let backendPort = null;
let isQuitting = false;

log.initialize();
autoUpdater.logger = log;
autoUpdater.autoDownload = true;
autoUpdater.autoInstallOnAppQuit = true;

function preferredSystemLanguage() {
  const preferred = app.getPreferredSystemLanguages()[0] || "en";
  const normalized = preferred.toLowerCase();
  return normalized.startsWith("zh-hans") || normalized.startsWith("zh-cn") || normalized.startsWith("zh-sg")
    ? "zh-hans"
    : "en";
}

function allocatePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

function backendCommand(port) {
  const sessionRoot = path.join(app.getPath("userData"), "sessions");
  const settingsPath = path.join(app.getPath("userData"), "settings.json");
  const desktopPathsPath = path.join(app.getPath("userData"), "desktop-paths.json");
  const commonArgs = [
    "--port",
    String(port),
    "--session-root",
    sessionRoot,
    "--settings-path",
    settingsPath,
    "--desktop-paths-path",
    desktopPathsPath,
    "--system-language",
    preferredSystemLanguage(),
  ];

  if (app.isPackaged) {
    const backendRoot = path.join(process.resourcesPath, "backend", "SpatialScopeBackend");
    return {
      command: path.join(backendRoot, "SpatialScopeBackend.exe"),
      args: commonArgs,
      cwd: backendRoot,
    };
  }

  const backendRoot = path.resolve(__dirname, "..", "backend");
  const pythonCommand = process.env.SPATIALSCOPE_PYTHON || "python3";
  const launcherArgs = [path.join(backendRoot, "launcher.py"), ...commonArgs];
  if (process.platform === "darwin" && process.env.SPATIALSCOPE_PYTHON_ARCH) {
    return {
      command: "/usr/bin/arch",
      args: [`-${process.env.SPATIALSCOPE_PYTHON_ARCH}`, pythonCommand, ...launcherArgs],
      cwd: backendRoot,
    };
  }
  return { command: pythonCommand, args: launcherArgs, cwd: backendRoot };
}

function readDesktopPaths() {
  const desktopPathsPath = path.join(app.getPath("userData"), "desktop-paths.json");
  try {
    return JSON.parse(fs.readFileSync(desktopPathsPath, "utf8"));
  } catch (error) {
    return {};
  }
}

function writeDesktopPaths(paths) {
  const desktopPathsPath = path.join(app.getPath("userData"), "desktop-paths.json");
  fs.mkdirSync(path.dirname(desktopPathsPath), { recursive: true });
  fs.writeFileSync(desktopPathsPath, `${JSON.stringify(paths, null, 2)}\n`, "utf8");
}

async function chooseDirectory(kind) {
  if (!mainWindow) return;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: kind === "input_folder" ? "Choose SpatialScope input folder" : "Choose SpatialScope output folder",
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || result.filePaths.length !== 1) return;
  const paths = readDesktopPaths();
  paths[kind] = result.filePaths[0];
  writeDesktopPaths(paths);
  mainWindow.webContents.reload();
}

function startBackend(port) {
  const launch = backendCommand(port);
  log.info("Starting backend", launch.command, launch.args);
  backendProcess = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });

  backendProcess.stdout.on("data", (chunk) => log.info(`[backend] ${chunk.toString().trimEnd()}`));
  backendProcess.stderr.on("data", (chunk) => log.error(`[backend] ${chunk.toString().trimEnd()}`));
  backendProcess.once("error", (error) => {
    log.error("Backend launch failed", error);
    showStartupError(error);
  });
  backendProcess.once("exit", (code, signal) => {
    log.info("Backend exited", { code, signal });
    backendProcess = null;
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
      showStartupError(new Error(`The analysis runtime stopped unexpectedly (${code ?? signal ?? "unknown"}).`));
    }
  });
}

function healthCheck(port) {
  return new Promise((resolve, reject) => {
    const request = http.get(
      { host: "127.0.0.1", port, path: "/_stcore/health", timeout: 1500 },
      (response) => {
        response.resume();
        if (response.statusCode === 200) resolve();
        else reject(new Error(`Health check returned ${response.statusCode}`));
      },
    );
    request.once("timeout", () => request.destroy(new Error("Health check timed out")));
    request.once("error", reject);
  });
}

async function waitForBackend(port, timeoutMs = 120000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!backendProcess) throw new Error("The analysis runtime stopped during startup.");
    try {
      await healthCheck(port);
      return;
    } catch (error) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw new Error("SpatialScope took too long to start.");
}

function stopBackend() {
  if (!backendProcess) return;
  const processToStop = backendProcess;
  backendProcess = null;
  processToStop.kill();
}

function showStartupError(error) {
  const detail = error instanceof Error ? error.message : String(error);
  dialog.showMessageBox({
    type: "error",
    title: "SpatialScope could not start",
    message: "The bundled analysis runtime could not be started.",
    detail: `${detail}\n\nReinstall the latest SpatialScope Windows release.`,
    buttons: ["OK", "Open releases"],
    defaultId: 0,
  }).then(({ response }) => {
    if (response === 1) shell.openExternal(RELEASE_URL);
  });
}

function buildMenu() {
  const template = [
    {
      label: "File",
      submenu: [
        {
          label: "Choose Input Folder...",
          accelerator: "CmdOrCtrl+O",
          click: () => chooseDirectory("input_folder"),
        },
        {
          label: "Choose Output Folder...",
          accelerator: "CmdOrCtrl+Shift+O",
          click: () => chooseDirectory("output_folder"),
        },
        { type: "separator" },
        { role: "close" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Help",
      submenu: [
        {
          label: "Check for Updates...",
          click: () => {
            if (app.isPackaged) autoUpdater.checkForUpdates();
            else shell.openExternal(RELEASE_URL);
          },
        },
        { label: "User Manual", click: () => shell.openExternal(MANUAL_URL) },
        { label: "GitHub Releases", click: () => shell.openExternal(RELEASE_URL) },
        { type: "separator" },
        {
          label: "About SpatialScope",
          click: () => dialog.showMessageBox({
            type: "info",
            title: "About SpatialScope",
            message: `SpatialScope ${app.getVersion()}`,
            detail: "Spatial image analysis for Windows.\n\nMIT License",
            buttons: ["OK"],
          }),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function configureUpdates() {
  autoUpdater.on("checking-for-update", () => log.info("Checking for updates"));
  autoUpdater.on("update-available", (info) => log.info("Update available", info.version));
  autoUpdater.on("update-not-available", () => log.info("No update available"));
  autoUpdater.on("error", (error) => log.error("Updater error", error));
  autoUpdater.on("update-downloaded", (info) => {
    dialog.showMessageBox({
      type: "info",
      title: "SpatialScope update ready",
      message: `SpatialScope ${info.version} is ready to install.`,
      detail: "Restart SpatialScope to complete the update.",
      buttons: ["Restart and install", "Later"],
      defaultId: 0,
      cancelId: 1,
    }).then(({ response }) => {
      if (response === 0) {
        isQuitting = true;
        stopBackend();
        autoUpdater.quitAndInstall(false, true);
      }
    });
  });
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    backgroundColor: "#f4f7f8",
    icon: path.join(__dirname, "assets", "SpatialScope.ico"),
    show: false,
    title: "SpatialScope",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://") || url.startsWith("http://")) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    const localOrigin = backendPort ? `http://127.0.0.1:${backendPort}` : "";
    if (localOrigin && !url.startsWith(localOrigin)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  await mainWindow.loadFile(path.join(__dirname, "loading.html"));
  backendPort = await allocatePort();
  startBackend(backendPort);
  await waitForBackend(backendPort);
  await mainWindow.loadURL(`http://127.0.0.1:${backendPort}`);
}

app.setAppUserModelId(APP_ID);

const singleInstanceLock = app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    fs.mkdirSync(app.getPath("logs"), { recursive: true });
    buildMenu();
    configureUpdates();
    try {
      await createWindow();
      if (app.isPackaged) setTimeout(() => autoUpdater.checkForUpdatesAndNotify(), 8000);
    } catch (error) {
      log.error("Application startup failed", error);
      showStartupError(error);
    }
  });
}

app.on("before-quit", () => {
  isQuitting = true;
  stopBackend();
});

app.on("window-all-closed", () => app.quit());
