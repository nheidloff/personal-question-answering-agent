import { app, BrowserWindow, Menu, ipcMain, shell } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';
import fs from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let backendProcess = null;

function startBackend() {
    const isDev = !app.isPackaged;
    let executablePath;

    if (isDev) {
        // In development, we assume the backend is run separately or we run it via python
        console.log('Skipping backend spawn in dev mode');
        return;
    } else {
        // In production, the executable is bundled in the Resources folder
        // For macOS, it's typically in Contents/Resources/backend/run_app/run_app
        executablePath = path.join(process.resourcesPath, 'backend', 'run_app', 'run_app');
    }

    console.log(`Starting backend at: ${executablePath}`);

    if (!fs.existsSync(executablePath)) {
        console.error(`Backend executable not found at: ${executablePath}`);
        return;
    }

    const env = {
        ...process.env,
        BACKEND_PORT: '8800',
        BACKEND_HOST: '127.0.0.1'
    };

    backendProcess = spawn(executablePath, [], { env });

    backendProcess.stdout.on('data', (data) => {
        console.log(`Backend: ${data}`);
    });

    backendProcess.stderr.on('data', (data) => {
        console.error(`Backend Error: ${data}`);
    });

    backendProcess.on('close', (code) => {
        console.log(`Backend process exited with code ${code}`);
    });
}

function setMainMenu() {
    const template = [
        ...(process.platform === 'darwin'
            ? [
                {
                    label: app.name,
                    submenu: [
                        { role: 'about' },
                        { type: 'separator' },
                        { role: 'services' },
                        { type: 'separator' },
                        { role: 'hide' },
                        { role: 'hideOthers' },
                        { role: 'unhide' },
                        { type: 'separator' },
                        { role: 'quit' },
                    ],
                },
            ]
            : []),
        {
            label: 'View',
            submenu: [
                { role: 'reload' },
                { role: 'forceReload' },
                { role: 'toggleDevTools' },
                { type: 'separator' },
                { role: 'resetZoom' },
                { role: 'zoomIn' },
                { role: 'zoomOut' },
                { type: 'separator' },
                { role: 'togglefullscreen' },
            ],
        },
        {
            label: 'Window',
            submenu: [
                { role: 'minimize' },
                { role: 'zoom' },
                ...(process.platform === 'darwin'
                    ? [
                        { type: 'separator' },
                        { role: 'front' },
                        { type: 'separator' },
                        { role: 'window' },
                    ]
                    : [{ role: 'close' }]),
            ],
        },
    ];

    const menu = Menu.buildFromTemplate(template);
    Menu.setApplicationMenu(menu);
}

function createWindow() {
    const mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        titleBarStyle: 'hiddenInset',
        trafficLightPosition: { x: 18, y: 18 }, // Adjusted for more space
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true,
        },
    });

    if (!app.isPackaged) {
        mainWindow.loadURL('http://localhost:5173');
    } else {
        mainWindow.loadFile(path.join(__dirname, 'dist', 'index.html'));
    }

    // Handle target="_blank" links by opening them in the system browser
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        shell.openExternal(url);
        return { action: 'deny' };
    });
}

app.whenReady().then(() => {
    startBackend();
    setMainMenu();
    createWindow();

    app.on('activate', function () {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
});

app.on('window-all-closed', function () {
    if (backendProcess) {
        backendProcess.kill();
    }
    if (process.platform !== 'darwin') app.quit();
});

app.on('quit', () => {
    if (backendProcess) {
        backendProcess.kill();
    }
});

ipcMain.on('open-file', (event, filePath) => {
    if (filePath) {
        shell.openPath(filePath);
    }
});

