#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '../..');
const profilePath = process.env.WB_BROWSER_PROFILE || path.join(projectRoot, '.sessions', 'wb', 'browser-profile');
const statePath = process.env.WB_STORAGE_STATE || path.join(projectRoot, '.sessions', 'wb', 'wb_storage_state.json');
const expectedSeller = process.env.WB_EXPECTED_SELLER || '';
const logDir = path.join(projectRoot, '.sessions', 'wb', 'session_refresh_logs');
const lastStatusPath = path.join(projectRoot, '.sessions', 'wb', 'wb_session_keepalive_last.json');
const urls = [
  'https://seller.wildberries.ru/',
  'https://cmp.wildberries.ru/campaigns/list',
];

function todayLogPath() {
  const day = new Date().toISOString().slice(0, 10);
  return path.join(logDir, `wb-session-keepalive-${day}.ndjson`);
}

function appendStatus(status) {
  fs.mkdirSync(logDir, { recursive: true });
  fs.mkdirSync(path.dirname(lastStatusPath), { recursive: true });
  fs.appendFileSync(todayLogPath(), `${JSON.stringify(status)}\n`);
  fs.writeFileSync(lastStatusPath, `${JSON.stringify(status, null, 2)}\n`);
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function sellerPattern() {
  const exact = String(expectedSeller || '').trim();
  if (!exact) return null;
  const compact = exact.replace(/\s+/g, '\\s+');
  return new RegExp(compact, 'i');
}

function classify(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const blocked = /captcha|access denied|доступ ограничен|ошибка|что-то пошло не так/i.test(all);
  const needsLogin = /login|passport|signin|auth|войти|номер телефона|получить код/i.test(all);
  const pattern = sellerPattern();
  const sellerFound = pattern ? pattern.test(all) : true;
  const loggedIn = /(seller|cmp)\.wildberries\.ru/.test(summary.url) && !needsLogin;
  return { blocked, needsLogin, sellerFound, loggedIn };
}

async function summarize(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    text: (document.body?.innerText || '').slice(0, 3000),
    webdriver: navigator.webdriver,
  }));
}

async function exportState(context, targetPath) {
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  await context.storageState({ path: targetPath });
  fs.chmodSync(targetPath, 0o600);
}

(async () => {
  const result = {
    kind: 'wb_session_keepalive',
    startedAt: new Date().toISOString(),
    finishedAt: '',
    expectedSeller,
    ok: false,
    stateExported: false,
    checks: [],
    error: '',
    valuesPrinted: false,
  };

  let context;
  try {
    fs.mkdirSync(profilePath, { recursive: true });
    context = await chromium.launchPersistentContext(profilePath, browserLaunchOptions({
      headless: process.env.WB_HEADLESS !== '0',
      viewport: { width: 1440, height: 1000 },
      locale: 'ru-RU',
      timezoneId: 'Europe/Moscow',
    }));
    const page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(15000);

    for (const url of urls) {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch((error) => {
        result.checks.push({ requestedUrl: url, navigationError: error.message });
      });
      await page.waitForTimeout(6000);
      const summary = await summarize(page);
      const status = classify(summary);
      result.checks.push({
        requestedUrl: url,
        url: summary.url,
        title: summary.title,
        loggedIn: status.loggedIn,
        expectedSellerFound: status.sellerFound,
        blocked: status.blocked,
        needsLogin: status.needsLogin,
        webdriver: summary.webdriver,
      });
    }

    const bad = result.checks.find((check) => check.blocked || check.needsLogin || check.loggedIn === false);
    const sellerSeen = result.checks.some((check) => check.expectedSellerFound === true);
    if (!bad && sellerSeen) {
      await exportState(context, statePath);
      result.ok = true;
      result.stateExported = true;
    } else if (bad?.blocked) {
      process.exitCode = 20;
      result.error = 'WB blocked/error page detected';
    } else if (bad?.needsLogin || bad?.loggedIn === false) {
      process.exitCode = 10;
      result.error = 'Login required';
    } else {
      process.exitCode = 12;
      result.error = 'Expected seller marker not found';
    }
  } catch (error) {
    const message = error.message || String(error);
    process.exitCode = /singleton|lock|profile|browser/i.test(message) ? 30 : 1;
    result.error = message;
  } finally {
    if (context) await context.close().catch(() => null);
    result.finishedAt = new Date().toISOString();
    appendStatus(result);
    console.log(JSON.stringify(result, null, 2));
    setImmediate(() => process.exit(process.exitCode || 0));
  }
})();
