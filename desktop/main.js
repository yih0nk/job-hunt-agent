// Electron shell: starts the local Python backend, then shows its UI in a window.
//
// Dev:       `npm start` runs ../.venv/bin/python -m jobhunt
// Packaged:  runs the PyInstaller-built backend from resources/backend/
const { app, BrowserWindow, shell, dialog } = require('electron')
const { spawn } = require('child_process')
const crypto = require('crypto')
const net = require('net')
const path = require('path')
const fs = require('fs')
const http = require('http')

let backend = null
let win = null
const token = crypto.randomBytes(24).toString('base64url')

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
    srv.on('error', reject)
  })
}

function backendCommand(port) {
  const args = ['--no-browser', '--port', String(port)]
  if (app.isPackaged) {
    const exe = process.platform === 'win32' ? 'jobhunt-backend.exe' : 'jobhunt-backend'
    return { cmd: path.join(process.resourcesPath, 'backend', exe), args, cwd: process.resourcesPath }
  }
  const repo = path.resolve(__dirname, '..')
  const py = process.platform === 'win32'
    ? path.join(repo, '.venv', 'Scripts', 'python.exe')
    : path.join(repo, '.venv', 'bin', 'python')
  return { cmd: fs.existsSync(py) ? py : 'python3', args: ['-m', 'jobhunt', ...args], cwd: repo }
}

function waitForHealth(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: 1000 }, res => {
        res.resume()
        if (res.statusCode === 200) return resolve()
        retry()
      })
      req.on('error', retry)
      req.on('timeout', () => { req.destroy(); retry() })
    }
    const retry = () => {
      if (Date.now() > deadline) return reject(new Error('The backend did not start in time.'))
      setTimeout(tryOnce, 300)
    }
    tryOnce()
  })
}

async function start() {
  const port = await freePort()
  const { cmd, args, cwd } = backendCommand(port)
  let stderr = ''
  backend = spawn(cmd, args, { cwd, env: { ...process.env, JOBHUNT_TOKEN: token } })
  backend.stderr.on('data', d => { stderr = (stderr + d).slice(-4000) })
  backend.on('exit', code => {
    if (code && !app.isQuitting) {
      dialog.showErrorBox('Job Hunt Agent stopped', `The backend exited (code ${code}).\n\n${stderr}`)
      app.quit()
    }
  })

  try {
    await waitForHealth(port)
  } catch (e) {
    dialog.showErrorBox('Job Hunt Agent could not start', `${e.message}\n\n${stderr}`)
    app.quit()
    return
  }

  const origin = `http://127.0.0.1:${port}`
  const icon = path.join(__dirname, 'resources', 'icon.png')
  if (process.platform === 'darwin' && !app.isPackaged && app.dock) app.dock.setIcon(icon)  // dev runs show the real icon
  win = new BrowserWindow({
    icon,
    width: 1320,
    height: 880,
    minWidth: 960,
    minHeight: 600,
    title: 'Job Hunt Agent',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true,
                      preload: path.join(__dirname, 'preload.js') },
  })

  // Job postings and docs open in the user's real browser; our own PDFs open in-app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(origin)) return { action: 'allow' }
    if (/^https?:\/\//.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(origin)) { e.preventDefault(); if (/^https?:\/\//.test(url)) shell.openExternal(url) }
  })

  await win.loadURL(origin + '/')
}

app.on('before-quit', () => {
  app.isQuitting = true
  if (backend && !backend.killed) backend.kill()
})
app.on('window-all-closed', () => app.quit())
app.whenReady().then(start)
