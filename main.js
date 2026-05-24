const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const os = require('os');
const http = require('http');

let mainWindow;
let pythonProcess;

// Determine paths for Python virtual environment based on OS
const isWin = os.platform() === 'win32';
const resourcesDir = process.resourcesPath || __dirname;
const bundledBinDir = isWin
  ? path.join(resourcesDir, 'bin', 'win')
  : path.join(resourcesDir, 'bin', 'mac');
const bundledBackend = isWin
  ? path.join(resourcesDir, 'backend', 'backend.exe')
  : path.join(resourcesDir, 'backend', 'backend');
const devPythonBin = isWin
  ? path.join(__dirname, '.venv', 'Scripts', 'python.exe')
  : path.join(__dirname, '.venv', 'bin', 'python');
const devVenvBinDir = isWin
  ? path.join(__dirname, '.venv', 'Scripts')
  : path.join(__dirname, '.venv', 'bin');

function startPythonServer() {
  const useBundledBackend = app.isPackaged;
  const command = useBundledBackend ? bundledBackend : devPythonBin;
  const args = useBundledBackend ? [] : ['-m', 'app.server'];
  const cwd = useBundledBackend ? resourcesDir : __dirname;

  console.log(`Starting backend from: ${command}`);
  
  const env = { ...process.env };
  const pathSeparator = isWin ? ';' : ':';
  const pathParts = [];

  if (useBundledBackend) {
    pathParts.push(bundledBinDir);
    env.MELON_BIN_DIR = bundledBinDir;
    env.MELON_DATA_DIR = path.join(app.getPath('userData'), 'data');
    env.MELON_OUTPUT_DIR = path.join(app.getPath('downloads'), 'MelonMusicDownloader');
  } else {
    pathParts.push(devVenvBinDir);
  }

  // For macOS, make sure homebrew paths are also in PATH if not already present
  if (!isWin) {
    pathParts.push('/opt/homebrew/bin', '/usr/local/bin');
  }
  env.PATH = pathParts.join(pathSeparator) + pathSeparator + (env.PATH || '');

  pythonProcess = spawn(command, args, {
    cwd,
    env: env
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python STDOUT] ${data.toString().trim()}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python STDERR] ${data.toString().trim()}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`Python process exited with code ${code}`);
  });
}

function checkServerReady(callback, attempts = 50) {
  if (attempts <= 0) {
    callback(false);
    return;
  }

  const req = http.get('http://127.0.0.1:5173/api/health', (res) => {
    if (res.statusCode === 200) {
      callback(true);
    } else {
      setTimeout(() => checkServerReady(callback, attempts - 1), 200);
    }
  });

  req.on('error', () => {
    setTimeout(() => checkServerReady(callback, attempts - 1), 200);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1050,
    height: 720,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: 'default',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  // Load the Python server URL once it is ready
  checkServerReady((ready) => {
    if (ready) {
      mainWindow.loadURL('http://127.0.0.1:5173');
    } else {
      mainWindow.loadURL(`data:text/html,<html><body><h3 style="font-family: sans-serif; padding: 20px;">Lỗi: Không khởi động được máy chủ Python phía sau. Hãy thử khởi động lại ứng dụng.</h3></body></html>`);
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// IPC Handlers for native dialogues
ipcMain.handle('select-file', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openFile'],
    filters: [{ name: 'Excel Files', extensions: ['xlsx'] }]
  });
  return result.filePaths[0] || '';
});

ipcMain.handle('select-directory', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory']
  });
  return result.filePaths[0] || '';
});

app.on('ready', () => {
  startPythonServer();
  createWindow();
});

app.on('window-all-closed', () => {
  // Terminate Python backend when Electron window is closed
  if (pythonProcess) {
    console.log('Terminating Python server...');
    if (isWin) {
      spawn('taskkill', ['/pid', pythonProcess.pid, '/f', '/t']);
    } else {
      pythonProcess.kill('SIGINT');
    }
  }
  
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
