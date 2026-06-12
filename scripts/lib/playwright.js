'use strict';

const fs = require('fs');

function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_NODE_MODULE,
    '/home/pavel/.local/share/codex-tools/playwright/node_modules/playwright',
    '/home/Codex/.local/share/codex-tools/playwright/node_modules/playwright',
    '/home/Codex/agent-tools/node/node_modules/@playwright/mcp/node_modules/playwright',
    'playwright',
  ].filter(Boolean);

  const errors = [];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (error) {
      errors.push(`${candidate}: ${error.code || error.message}`);
    }
  }

  throw new Error(`Playwright is not available. Checked: ${errors.join('; ')}`);
}

function chromiumExecutablePath() {
  const candidates = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    process.env.CHROME_EXECUTABLE,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/opt/google/chrome/google-chrome',
    '/opt/google/chrome/chrome',
    '/home/Codex/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
    '/home/Codex/.cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell',
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return '';
}

function browserLaunchOptions(options = {}) {
  const merged = { ...options };
  const executablePath = chromiumExecutablePath();
  if (executablePath) {
    merged.executablePath = executablePath;
    delete merged.channel;
  }
  return merged;
}

const playwright = loadPlaywright();
playwright.chromiumExecutablePath = chromiumExecutablePath;
playwright.browserLaunchOptions = browserLaunchOptions;

module.exports = playwright;
