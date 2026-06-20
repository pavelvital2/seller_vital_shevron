#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const projectRoot = path.resolve(__dirname, '../..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const expectedStore = process.env.OZON_EXPECTED_STORE || 'Vital Shevron';
const expectedCdpPort = 9544;
const expectedProfileDir = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');
const statePath = process.env.OZON_SELLER_STATE || path.join(projectRoot, '.sessions', 'ozon', 'ozon_seller_storage_state.json');
const logDir = path.join(projectRoot, '.sessions', 'ozon', 'session_refresh_logs');
const lastStatusPath = path.join(projectRoot, '.sessions', 'ozon', 'ozon_session_keepalive_last.json');
const urls = [
  'https://seller.ozon.ru/app/dashboard/main',
  'https://seller.ozon.ru/app/analytics',
  'https://seller.ozon.ru/app/products',
  'https://seller.ozon.ru/app/prices/control',
];

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function todayLogPath() {
  const day = new Date().toISOString().slice(0, 10);
  return path.join(logDir, `ozon-session-keepalive-cdp-${day}.ndjson`);
}

function appendStatus(status) {
  fs.mkdirSync(logDir, { recursive: true });
  fs.mkdirSync(path.dirname(lastStatusPath), { recursive: true });
  fs.appendFileSync(todayLogPath(), `${JSON.stringify(status)}\n`);
  fs.writeFileSync(lastStatusPath, `${JSON.stringify(status, null, 2)}\n`);
}

function classify(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const expected = String(expectedStore || '').trim();
  const rrMode = /[?&]__rr=1/i.test(summary.url);
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|продавец|dashboard/i.test(all);
  const blockedText = /похоже, нет\s*соединения|доступ ограничен|инцидент|captcha|access denied/i.test(all);
  return {
    expectedStoreFound: Boolean(expected && new RegExp(escapeRegExp(expected), 'i').test(all)),
    rrMode,
    blocked: blockedText || (rrMode && !sellerShellOpen),
    needsLogin: /registration\/signin|sso\.ozon|вход и регистрация|войти по почте|войти по номеру|введите код/i.test(all),
  };
}

async function summarize(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    text: (document.body?.innerText || '').slice(0, 2600),
    webdriver: navigator.webdriver,
  }));
}

(async () => {
  const result = {
    kind: 'ozon_session_keepalive_cdp',
    startedAt: new Date().toISOString(),
    finishedAt: '',
    cdpUrl,
    expectedStore,
    ok: false,
    stateExported: false,
    checks: [],
    error: '',
    valuesPrinted: false,
  };

  let page;
  try {
    result.contourGuard = assertOzonCdpContour({
      cdpUrl,
      expectedPort: expectedCdpPort,
      expectedProfileDir,
      expectedStore,
    });
    const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 10000 });
    const context = browser.contexts()[0];
    if (!context) throw new Error('No browser context found in CDP session');
    page = await context.newPage();
    page.setDefaultTimeout(15000);

    for (const url of urls) {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch((error) => {
        result.checks.push({ requestedUrl: url, navigationError: error.message });
      });
      await page.waitForTimeout(5000);
      const summary = await summarize(page);
      const status = classify(summary);
      result.checks.push({
        requestedUrl: url,
        url: summary.url,
        title: summary.title,
        expectedStoreFound: status.expectedStoreFound,
        rrMode: status.rrMode,
        blocked: status.blocked,
        needsLogin: status.needsLogin,
        webdriver: summary.webdriver,
      });
    }

    const bad = result.checks.find((check) => check.blocked || check.needsLogin || check.expectedStoreFound === false);
    if (!bad) {
      fs.mkdirSync(path.dirname(statePath), { recursive: true });
      await context.storageState({ path: statePath });
      fs.chmodSync(statePath, 0o600);
      result.ok = true;
      result.stateExported = true;
    } else if (bad.blocked) {
      process.exitCode = 20;
      result.error = 'Ozon blocked/no-connection page detected';
    } else if (bad.needsLogin) {
      process.exitCode = 10;
      result.error = 'Login required';
    } else {
      process.exitCode = 12;
      result.error = 'Expected Vital Shevron store marker not found';
    }
  } catch (error) {
    process.exitCode = /ECONNREFUSED|connect|CDP|browser/i.test(error.message || '') ? 30 : 1;
    result.error = error.message || String(error);
  } finally {
    if (page) await page.close().catch(() => null);
    result.finishedAt = new Date().toISOString();
    appendStatus(result);
    console.log(JSON.stringify(result, null, 2));
    setImmediate(() => process.exit(process.exitCode || 0));
  }
})();
