#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '../..');
const defaultProfile = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');
const defaultState = path.join(projectRoot, '.sessions', 'ozon', 'ozon_seller_storage_state.json');
const defaultUrl = 'https://seller.ozon.ru/app/dashboard/main';
const desktopUserAgent =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

const opts = {
  profile: process.env.OZON_SELLER_PROFILE || defaultProfile,
  state: process.env.OZON_SELLER_STATE || defaultState,
  url: process.env.OZON_SELLER_START_URL || defaultUrl,
  expectedStore: process.env.OZON_EXPECTED_STORE || 'Vital Shevron',
  headless: process.env.OZON_HEADLESS !== '0',
  keepOpen: false,
  forceExport: false,
  seed: process.env.OZON_SELLER_SEED === '1',
  userAgent: process.env.OZON_USER_AGENT || desktopUserAgent,
};

for (let i = 2; i < process.argv.length; i += 1) {
  const arg = process.argv[i];
  if (arg === '--profile') opts.profile = path.resolve(process.argv[++i]);
  else if (arg === '--state') opts.state = path.resolve(process.argv[++i]);
  else if (arg === '--url') opts.url = process.argv[++i];
  else if (arg === '--expected-store') opts.expectedStore = process.argv[++i];
  else if (arg === '--headful') opts.headless = false;
  else if (arg === '--headless') opts.headless = true;
  else if (arg === '--keep-open') opts.keepOpen = true;
  else if (arg === '--force-export') opts.forceExport = true;
  else if (arg === '--seed') opts.seed = true;
  else if (arg === '--user-agent') opts.userAgent = process.argv[++i];
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/sessions/ozon_seller_persistent_session.js [options]',
      '',
      'Options:',
      '  --profile DIR       Persistent Chrome profile directory',
      '  --state FILE        Exported Playwright storageState file',
      '  --url URL           Ozon Seller page to open',
      '  --expected-store X  Store marker expected after login',
      '  --headful           Open visible browser window',
      '  --headless          Run without visible browser window',
      '  --keep-open         Keep browser open after check/export',
      '  --force-export      Save storageState even when auth markers are not found',
      '  --seed              Import cookies/localStorage from --state into profile first',
      '  --user-agent VALUE  Browser user-agent',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function classify(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const expected = String(opts.expectedStore || '').trim();
  const expectedStoreFound =
    Boolean(expected && new RegExp(escapeRegExp(expected), 'i').test(all));
  const rrMode = /[?&]__rr=1/i.test(summary.url);
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|продавец|dashboard/i.test(all);
  const blockedText = /доступ ограничен|похоже, нет\s*соединения|выключите vpn|инцидент|access denied|captcha/i.test(all);
  const blocked = blockedText || (rrMode && !sellerShellOpen);
  const authUrl = /registration\/signin|auth|sso\.ozon|\/otp/i.test(summary.url);
  const loggedIn =
    !blocked &&
    /seller\.ozon\.ru\/app\//i.test(summary.url) &&
    !authUrl &&
    /продавец|цены|продвижение|аналитика|товары|заказы|dashboard/i.test(all);
  const needsLogin = authUrl || (/войти|введите код|войти по почте|почт/i.test(all) && !loggedIn);
  return { rrMode, blocked, loggedIn, needsLogin, expectedStoreFound };
}

async function summarizePage(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    userAgent: navigator.userAgent,
    webdriver: navigator.webdriver,
    text: (document.body?.innerText || '').slice(0, 3000),
  }));
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

async function seedProfileFromState(context, statePath) {
  if (!fs.existsSync(statePath)) return false;
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  if (Array.isArray(state.cookies) && state.cookies.length) await context.addCookies(state.cookies);

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
  return true;
}

(async () => {
  fs.mkdirSync(opts.profile, { recursive: true });

  const context = await chromium.launchPersistentContext(opts.profile, browserLaunchOptions({
    headless: opts.headless,
    channel: opts.headless ? undefined : 'chrome',
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    userAgent: opts.userAgent,
    extraHTTPHeaders: { 'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7' },
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  }));

  const seeded = opts.seed ? await seedProfileFromState(context, opts.state) : false;
  const page = context.pages()[0] || await context.newPage();
  await page.goto(opts.url, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(8000);

  const summary = await summarizePage(page);
  const status = classify(summary);
  const stateExported = status.loggedIn || opts.forceExport;
  if (stateExported) await exportState(context, opts.state);

  const result = {
    profile: opts.profile,
    state: opts.state,
    currentUrl: summary.url,
    title: summary.title,
    loggedIn: status.loggedIn,
    expectedStoreFound: status.expectedStoreFound,
    rrMode: status.rrMode,
    blocked: status.blocked,
    needsLogin: status.needsLogin,
    userAgent: summary.userAgent,
    webdriver: summary.webdriver,
    seeded,
    stateExported,
    exportedAt: stateExported ? new Date().toISOString() : '',
    keepOpen: opts.keepOpen,
  };
  console.log(JSON.stringify(result, null, 2));

  if (opts.keepOpen) {
    console.log('Browser is left open. Press Ctrl+C in this terminal when finished.');
    await new Promise(() => {});
  }

  await context.close();

  if (status.blocked) process.exit(20);
  if (!status.loggedIn && !opts.forceExport) process.exit(status.needsLogin ? 10 : 11);
  if (!status.expectedStoreFound && !opts.forceExport) process.exit(12);
})().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
