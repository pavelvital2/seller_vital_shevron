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
  const args = { approvedPath: '', runDir: '', skipBlockedApi: true };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === '--approved-path') {
      args.approvedPath = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--run-dir') {
      args.runDir = argv[i + 1];
      i += 1;
    }
  }
  if (!args.approvedPath) throw new Error('Missing required --approved-path');
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

async function visibleTextarea(page) {
  const locators = [
    page.locator('textarea').last(),
    page.locator('[contenteditable="true"]').last(),
    page.locator('[role="textbox"]').last(),
  ];
  for (const locator of locators) {
    const count = await locator.count().catch(() => 0);
    if (!count) continue;
    const visible = await locator.isVisible().catch(() => false);
    if (visible) return locator;
  }
  return null;
}

async function findSendButton(page) {
  return page.evaluateHandle(() => {
    const buttons = [...document.querySelectorAll('button')];
    const candidates = buttons
      .filter((button) => {
        const rect = button.getBoundingClientRect();
        if (rect.width < 20 || rect.height < 20) return false;
        if (button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
        return rect.y > window.innerHeight * 0.65 && rect.x > window.innerWidth * 0.65;
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.y - ar.y) || (br.x - ar.x);
      });
    return candidates[0] || null;
  });
}

async function sendOne(page, action) {
  const url = `https://seller.ozon.ru/app/messenger?id=${encodeURIComponent(action.chat_id)}&group=customers_v2`;
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(5000);

  const input = await visibleTextarea(page);
  if (!input) {
    return { chat_id: action.chat_id, ok: false, skipped: false, reason: 'message_input_not_found' };
  }

  await input.click();
  await input.fill(action.draft_reply);
  await page.waitForTimeout(500);

  const buttonHandle = await findSendButton(page);
  const buttonElement = buttonHandle.asElement();
  if (!buttonElement) {
    return { chat_id: action.chat_id, ok: false, skipped: true, reason: 'send_button_not_found_or_disabled' };
  }

  await buttonElement.click();
  await page.waitForTimeout(4000);

  const verify = await page.evaluate((expectedText) => {
    const body = document.body?.innerText || '';
    const textareas = [...document.querySelectorAll('textarea')].map((item) => item.value);
    return {
      bodyHasText: body.includes(expectedText),
      textareaEmpty: textareas.every((value) => !value),
    };
  }, action.draft_reply);

  return {
    chat_id: action.chat_id,
    ok: Boolean(verify.bodyHasText && verify.textareaEmpty),
    skipped: false,
    reason: verify.bodyHasText ? '' : 'sent_text_not_visible_after_click',
    verify,
    url,
  };
}

(async () => {
  const args = parseArgs(process.argv);
  const approvedPath = assertInsideProject(args.approvedPath);
  const runDir = assertInsideProject(args.runDir);
  const rawDir = path.join(runDir, 'raw', 'ozon_messenger_lk_send');
  ensureDir(rawDir);

  const approved = JSON.parse(fs.readFileSync(approvedPath, 'utf8'));
  const actions = (approved.actions || []).filter((action) => (
    action.action_type === 'send_chat_message' &&
    action.approved === true &&
    action.chat_id &&
    action.draft_reply
  ));

  const result = {
    source: 'ozon_lk_cdp_messenger',
    cdpUrl,
    expectedStore,
    ok: false,
    results: [],
    blocker: '',
  };

  let page;
  try {
    if (!actions.length) throw new Error('No approved send_chat_message actions');
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

    for (const action of actions) {
      const item = await sendOne(page, action);
      result.results.push(item);
      writeJson(path.join(rawDir, `${action.chat_id}.result.json`), item);
    }
    result.ok = result.results.every((item) => item.ok || item.skipped);
  } catch (error) {
    result.blocker = error && error.stack ? error.stack : String(error);
    process.exitCode = 1;
  } finally {
    if (page) await page.close().catch(() => null);
    writeJson(path.join(runDir, 'processed', 'lk_cdp_send_result.json'), result);
    console.log(JSON.stringify({
      ok: result.ok,
      sent_ok: result.results.filter((item) => item.ok).length,
      skipped: result.results.filter((item) => item.skipped).length,
      total: result.results.length,
      blocker: result.blocker ? result.blocker.split('\n')[0] : '',
    }, null, 2));
    setTimeout(() => process.exit(process.exitCode || 0), 0);
  }
})();
