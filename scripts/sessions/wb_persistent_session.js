#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '..', '..');
const defaultProfile = path.join(projectRoot, '.sessions', 'wb', 'browser-profile');
const defaultState = path.join(projectRoot, '.sessions', 'wb', 'wb_storage_state.json');

const args = process.argv.slice(2);
const opts = {
  profile: process.env.WB_BROWSER_PROFILE || defaultProfile,
  state: process.env.WB_STORAGE_STATE || defaultState,
  url: process.env.WB_START_URL || 'https://seller.wildberries.ru/',
  headless: process.env.WB_HEADLESS !== '0',
  seed: false,
  keepOpen: false,
  forceExport: false,
};

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === '--profile') opts.profile = path.resolve(args[++i]);
  else if (arg === '--state') opts.state = path.resolve(args[++i]);
  else if (arg === '--url') opts.url = args[++i];
  else if (arg === '--headful') opts.headless = false;
  else if (arg === '--headless') opts.headless = true;
  else if (arg === '--seed') opts.seed = true;
  else if (arg === '--keep-open') opts.keepOpen = true;
  else if (arg === '--force-export') opts.forceExport = true;
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/sessions/wb_persistent_session.js [options]',
      '',
      'Options:',
      '  --profile DIR   Persistent Chromium profile directory',
      '  --state FILE    Exported Playwright storageState file',
      '  --url URL       WB page to open',
      '  --seed          Import cookies/localStorage from --state into profile first',
      '  --headful       Open visible browser window',
      '  --headless      Run without visible browser window',
      '  --keep-open     Keep browser open after export; useful for manual login',
      '  --force-export  Save storageState even when the browser is on an auth page',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

function isProbablyLoggedIn(url) {
  return /(seller|cmp)\.wildberries\.ru/.test(url) && !/login|passport|signin|auth/i.test(url);
}

async function exportState(context, statePath) {
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  if (fs.existsSync(statePath)) {
    const backup = `${statePath}.bak-${new Date().toISOString().replace(/[:.]/g, '-')}`;
    fs.copyFileSync(statePath, backup);
    fs.chmodSync(backup, 0o600);
  }
  await context.storageState({ path: statePath });
  fs.chmodSync(statePath, 0o600);
}

async function waitForManualLogin(page, context, statePath) {
  const deadline = Date.now() + 30 * 60 * 1000;
  console.log('Waiting up to 30 minutes for manual WB login in the opened browser...');

  while (Date.now() < deadline) {
    await page.waitForTimeout(5000);
    const currentUrl = page.url();
    if (!isProbablyLoggedIn(currentUrl)) continue;
    await page.waitForTimeout(5000);
    await exportState(context, statePath);
    console.log(JSON.stringify({
      state: statePath,
      currentUrl,
      loggedIn: true,
      stateExported: true,
      exportedAt: new Date().toISOString(),
    }, null, 2));
    return true;
  }

  console.log('Manual login was not detected before timeout. State was not exported.');
  return false;
}

async function seedProfileFromState(context, statePath) {
  if (!fs.existsSync(statePath)) {
    console.log(`Seed skipped: state file not found at ${statePath}`);
    return;
  }

  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  if (Array.isArray(state.cookies) && state.cookies.length) {
    await context.addCookies(state.cookies);
  }

  const page = await context.newPage();
  for (const originState of state.origins || []) {
    const entries = originState.localStorage || [];
    if (!originState.origin || entries.length === 0) continue;
    await page.goto(originState.origin, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => null);
    await page.evaluate((items) => {
      for (const item of items) localStorage.setItem(item.name, item.value);
    }, entries).catch(() => null);
  }
  await page.close().catch(() => null);
  console.log(`Seeded persistent profile from ${statePath}`);
}

(async () => {
  fs.mkdirSync(opts.profile, { recursive: true });
  fs.mkdirSync(path.dirname(opts.state), { recursive: true });

  const context = await chromium.launchPersistentContext(opts.profile, browserLaunchOptions({
    headless: opts.headless,
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  }));

  if (opts.seed) {
    await seedProfileFromState(context, opts.state);
  }

  const page = context.pages()[0] || await context.newPage();
  await page.goto(opts.url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(8000);

  const currentUrl = page.url();
  const title = await page.title().catch(() => '');
  const loggedIn = isProbablyLoggedIn(currentUrl);

  if (loggedIn || opts.forceExport) await exportState(context, opts.state);

  console.log(JSON.stringify({
    profile: opts.profile,
    state: opts.state,
    currentUrl,
    title,
    loggedIn,
    stateExported: loggedIn || opts.forceExport,
    exportedAt: new Date().toISOString(),
    keepOpen: opts.keepOpen,
  }, null, 2));

  if (opts.keepOpen) {
    if (!loggedIn) await waitForManualLogin(page, context, opts.state);
    console.log('Browser is left open. Press Ctrl+C in this terminal when finished.');
    await new Promise(() => {});
  }

  await context.close();
})().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
