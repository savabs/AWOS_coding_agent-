#!/usr/bin/env node
/** AWOS Screenshot — captures the agent console to PNG */
const { app, BrowserWindow } = require('electron');
const fs = require('fs');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280, height: 800,
    show: false,
    backgroundColor: '#1a1a1e',
    webPreferences: { nodeIntegration: true, contextIsolation: false }
  });

  await win.loadURL('http://127.0.0.1:8765/agent');
  // Wait for JS to render (SSE, events, etc.)
  await new Promise(r => setTimeout(r, 4000));

  const img = await win.webContents.capturePage();
  const outPath = '/tmp/awos-screenshot.png';
  fs.writeFileSync(outPath, img.toPNG());
  console.log(`Screenshot: ${img.getSize().width}x${img.getSize().height} → ${outPath}`);
  app.quit();
});
