#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const projectRoot = path.resolve(__dirname, '..', '..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const expectedStore = process.env.OZON_EXPECTED_STORE || 'Vital Shevron';
const expectedCdpPort = 9544;
const expectedProfileDir = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');

function parseArgs(argv) {
  const args = { chatId: '', fromMessageId: '', runDir: '' };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === '--chat-id') {
      args.chatId = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--from-message-id') {
      args.fromMessageId = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--run-dir') {
      args.runDir = argv[i + 1];
      i += 1;
    }
  }
  if (!args.chatId) throw new Error('Missing required --chat-id');
  if (!args.runDir) throw new Error('Missing required --run-dir');
  return args;
}

function assertInsideProject(filePath) {
  const resolved = path.resolve(filePath);
  const root = path.resolve(projectRoot);
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error(`Refusing to access outside project: ${resolved}`);
  }
  return resolved;
}

function ensureDir(dirPath) {
  fs.mkdirSync(assertInsideProject(dirPath), { recursive: true });
}

function writeJson(filePath, value) {
  const target = assertInsideProject(filePath);
  ensureDir(path.dirname(target));
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

async function summarizePage(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    text: (document.body?.innerText || '').slice(0, 3000),
  }));
}

function classifyPage(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const expectedRegex = new RegExp(String(expectedStore).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|сообщения|продавец|dashboard/i.test(all);
  return {
    expectedStoreFound: expectedRegex.test(all) || sellerShellOpen,
    blocked: /похоже, нет\s*соединения|доступ ограничен|инцидент|captcha|access denied/i.test(all),
    needsLogin: /registration\/signin|sso\.ozon|вход и регистрация|войти по почте|войти по номеру|введите код/i.test(all),
  };
}

async function verifyCabinet(page) {
  await page.goto('https://seller.ozon.ru/app/dashboard/main', {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await page.waitForTimeout(3000);
  const summary = await summarizePage(page);
  const status = classifyPage(summary);
  if (status.blocked) throw new Error('Ozon blocked/no-connection page detected');
  if (status.needsLogin) throw new Error('Login required in Ozon LK');
  if (!status.expectedStoreFound) throw new Error(`Expected store marker not found: ${expectedStore}`);
  return { status };
}

(async () => {
  const args = parseArgs(process.argv);
  const runDir = assertInsideProject(args.runDir);
  const rawDir = path.join(runDir, 'raw', 'ozon_messenger_lk_mark_read');
  ensureDir(rawDir);

  const result = {
    source: 'ozon_lk_cdp_messenger_open_chat',
    cdpUrl,
    expectedStore,
    chat_id: args.chatId,
    from_message_id: args.fromMessageId,
    ok: false,
    blocker: '',
    url: '',
    confirmation: 'not_started',
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
    page.setDefaultTimeout(20000);
    await verifyCabinet(page);

    const url = `https://seller.ozon.ru/app/messenger?id=${encodeURIComponent(args.chatId)}&group=customers_v2`;
    result.url = url;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(8000);
    const pageSummary = await summarizePage(page);
    const pageStatus = classifyPage(pageSummary);
    if (pageStatus.blocked) throw new Error('Ozon blocked/no-connection page detected after opening chat');
    if (pageStatus.needsLogin) throw new Error('Login required in Ozon LK after opening chat');
    result.page = {
      url: pageSummary.url,
      title: pageSummary.title,
      status: pageStatus,
    };
    result.confirmation = 'chat_opened_unread_state_not_verified';
    result.ok = true;
  } catch (error) {
    result.blocker = error && error.stack ? error.stack : String(error);
    process.exitCode = 1;
  } finally {
    if (page) await page.close().catch(() => null);
    writeJson(path.join(rawDir, 'mark_read_cdp_result.json'), result);
    console.log(JSON.stringify({
      ok: result.ok,
      chat_id: result.chat_id,
      blocker: result.blocker ? result.blocker.split('\n')[0] : '',
      artifact: path.relative(projectRoot, path.join(rawDir, 'mark_read_cdp_result.json')),
    }, null, 2));
    setTimeout(() => process.exit(process.exitCode || 0), 0);
  }
})();
