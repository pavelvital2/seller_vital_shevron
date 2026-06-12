#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '../..');
const profile = process.env.OZON_SELLER_PROFILE || path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');
const statePath = process.env.OZON_SELLER_STATE || path.join(projectRoot, '.sessions', 'ozon', 'ozon_seller_storage_state.json');
const port = process.env.OZON_REMOTE_DEBUGGING_PORT || '9544';
const cdp = `http://127.0.0.1:${port}`;
const userAgent = process.env.OZON_USER_AGENT ||
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

async function pageSummary(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    text: (document.body?.innerText || '').slice(0, 5000),
    webdriver: navigator.webdriver,
  }));
}

function classify(summary) {
  const all = `${summary.title}\n${summary.url}\n${summary.text}`;
  const rrMode = /[?&]__rr=1/i.test(summary.url);
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|продавец|dashboard/i.test(all);
  const blockedText = /похоже, нет\s*соединения|доступ ограничен|инцидент|captcha|access denied/i.test(all);
  return {
    vitalShevron: /vital\s*shevron|vitalshevron|vital\s*chevron|vitalchevron/i.test(all),
    rrMode,
    blocked: blockedText || (rrMode && !sellerShellOpen),
    needsLogin: /registration\/signin|вход и регистрация|войти по номеру|войти по почте|введите код|sso\.ozon/i.test(all),
  };
}

async function seedProfileFromState(context, stateFile) {
  if (!fs.existsSync(stateFile)) return false;

  const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
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
  return true;
}

(async () => {
  fs.mkdirSync(profile, { recursive: true });
  fs.mkdirSync(path.dirname(statePath), { recursive: true });

  const context = await chromium.launchPersistentContext(profile, browserLaunchOptions({
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    userAgent,
    extraHTTPHeaders: { 'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7' },
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-blink-features=AutomationControlled',
      `--remote-debugging-port=${port}`,
    ],
  }));

  const seeded = await seedProfileFromState(context, statePath);
  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://seller.ozon.ru/app/dashboard/main', { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => null);
  await page.waitForTimeout(10000);

  const summary = await pageSummary(page);
  const status = classify(summary);
  const open = !status.blocked && !status.needsLogin && /seller\.ozon\.ru\/app\//i.test(summary.url);

  if (open) {
    await context.storageState({ path: statePath });
    fs.chmodSync(statePath, 0o600);
  }

  console.log(JSON.stringify({
    status: open ? 'OPEN' : 'NOT_OPEN',
    url: summary.url,
    title: summary.title,
    vitalShevron: status.vitalShevron,
    rrMode: status.rrMode,
    blocked: status.blocked,
    needsLogin: status.needsLogin,
    cdp,
    statePath,
    seeded,
    stateExported: open,
    valuesPrinted: false,
    keptOpen: true,
  }, null, 2));

  await new Promise(() => {});
})().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
