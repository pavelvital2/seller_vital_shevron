#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');

const { chromium, browserLaunchOptions } = require('../lib/playwright');
const { exportNormalizedStorageState } = require('../lib/ozon_cookie_state');

const projectRoot = path.resolve(__dirname, '../..');
const defaultProfile = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');
const defaultState = path.join(projectRoot, '.sessions', 'ozon', 'ozon_seller_storage_state.json');
const dashboardUrl = 'https://seller.ozon.ru/app/dashboard/main';
const signinUrl = 'https://seller.ozon.ru/app/registration/signin';
const desktopUserAgent =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

const opts = {
  email: process.env.OZON_SELLER_EMAIL || '',
  expectedStore: process.env.OZON_EXPECTED_STORE || 'Vital Shevron',
  profile: process.env.OZON_SELLER_PROFILE || defaultProfile,
  state: process.env.OZON_SELLER_STATE || defaultState,
  userAgent: process.env.OZON_USER_AGENT || desktopUserAgent,
  maxCodes: Number(process.env.OZON_MAX_CODES || 3),
  precheckDashboard: process.env.OZON_PRECHECK_DASHBOARD === '1',
};

for (let i = 2; i < process.argv.length; i += 1) {
  const arg = process.argv[i];
  if (arg === '--email') opts.email = process.argv[++i];
  else if (arg === '--expected-store') opts.expectedStore = process.argv[++i];
  else if (arg === '--profile') opts.profile = path.resolve(process.argv[++i]);
  else if (arg === '--state') opts.state = path.resolve(process.argv[++i]);
  else if (arg === '--user-agent') opts.userAgent = process.argv[++i];
  else if (arg === '--max-codes') opts.maxCodes = Number(process.argv[++i]);
  else if (arg === '--precheck-dashboard') opts.precheckDashboard = true;
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/sessions/ozon_seller_interactive_login.js [options]',
      '',
      'Options:',
      '  --email VALUE             Ozon Seller login email',
      '  --expected-store VALUE    Store marker expected after login, default Vital Shevron',
      '  --profile DIR            Persistent Chrome profile directory',
      '  --state FILE             Playwright storageState export path',
      '  --user-agent VALUE       Browser user-agent',
      '  --max-codes N            Max sequential OTP/SMS/email codes to handle',
      '  --precheck-dashboard     Check dashboard before opening signin page',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

function askLine(prompt) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(prompt, (answer) => {
      rl.close();
      resolve(String(answer || '').trim());
    });
  });
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function expectedStoreFound(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const expected = String(opts.expectedStore || '').trim();
  if (expected && new RegExp(escapeRegExp(expected), 'i').test(all)) return true;
  return false;
}

function classify(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const rrMode = /[?&]__rr=1/i.test(summary.url);
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|продавец|dashboard/i.test(all);
  const blockedText = /похоже, нет\s*соединения|доступ ограничен|инцидент|captcha|access denied|выключите vpn/i.test(all);
  const blocked = blockedText || (rrMode && !sellerShellOpen);
  const codeScreen = /otp|введите\s+код|код.{0,80}(почт|телефон|sms|смс)|отправили.{0,80}код|проверочн.{0,40}код/i.test(all);
  const authUrl = /registration\/signin|sso\.ozon|\/otp|\/auth/i.test(summary.url);
  const loggedIn =
    !blocked &&
    /seller\.ozon\.ru\/app\//i.test(summary.url) &&
    !authUrl &&
    /продавец|товары|цены|аналитика|продвижение|заказы|dashboard/i.test(all);
  const needsLogin = authUrl || (/войти по почте|войти по номеру|вход и регистрация/i.test(all) && !loggedIn);
  return {
    rrMode,
    blocked,
    codeScreen,
    loggedIn,
    needsLogin,
    expectedStoreFound: expectedStoreFound(summary),
  };
}

async function summarizePage(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    userAgent: navigator.userAgent,
    webdriver: navigator.webdriver,
    text: (document.body?.innerText || '').slice(0, 5000),
  }));
}

async function visibleInputs(page) {
  return page.locator('input:visible, textarea:visible, [contenteditable="true"]:visible').evaluateAll((nodes) =>
    nodes.map((node, index) => ({
      index,
      tag: node.tagName,
      type: node.getAttribute('type') || '',
      name: node.getAttribute('name') || '',
      placeholder: node.getAttribute('placeholder') || '',
      aria: node.getAttribute('aria-label') || '',
      maxLength: node.maxLength || 0,
      valueLength: node.value?.length || 0,
    })),
  ).catch(() => []);
}

async function clickFirst(locator, timeout = 15000) {
  const count = await locator.count().catch(() => 0);
  for (let i = 0; i < count; i += 1) {
    const item = locator.nth(i);
    if (await item.isVisible().catch(() => false)) {
      await item.click({ timeout });
      return true;
    }
  }
  return false;
}

async function fillEmail(page) {
  if (!opts.email) {
    opts.email = await askLine('OZON_EMAIL> ');
  }

  await page.waitForTimeout(1500);
  const inputs = page.locator('input:visible');
  const count = await inputs.count();
  const editableCandidates = [];

  for (let i = 0; i < count; i += 1) {
    const input = inputs.nth(i);
    const meta = await input.evaluate((node) => ({
      type: node.getAttribute('type') || '',
      name: node.getAttribute('name') || '',
      placeholder: node.getAttribute('placeholder') || '',
      aria: node.getAttribute('aria-label') || '',
      disabled: node.disabled,
      readonly: node.readOnly,
    }));
    const haystack = `${meta.type} ${meta.name} ${meta.placeholder} ${meta.aria}`.toLowerCase();
    if (!meta.disabled && !meta.readonly && !/radio|checkbox|hidden|submit|button/.test(meta.type)) {
      editableCandidates.push(input);
    }
    if (!meta.disabled && /email|mail|почт/.test(haystack)) {
      await input.fill(opts.email);
      return;
    }
  }

  if (editableCandidates.length) {
    await editableCandidates[0].fill(opts.email);
    return;
  }
  const summary = await summarizePage(page).catch(() => ({ url: '', title: '', text: '' }));
  const status = classify(summary);
  if (status.blocked) {
    console.log(JSON.stringify(publicResult('BLOCKED_BEFORE_EMAIL', summary, false), null, 2));
    process.exit(20);
  }
  throw new Error(`No editable email input found; visibleInputs=${JSON.stringify(await visibleInputs(page))}`);
}

async function enterCode(page, code) {
  const normalized = String(code || '').replace(/\s+/g, '');
  const digits = normalized.replace(/\D/g, '');
  const value = digits || normalized;
  if (!/^[A-Za-z0-9]{4,10}$/.test(value)) throw new Error('Code should contain 4-10 letters/digits');

  const inputs = page.locator('input:visible');
  const count = await inputs.count();
  const candidates = [];

  for (let i = 0; i < count; i += 1) {
    const input = inputs.nth(i);
    const meta = await input.evaluate((node) => ({
      type: node.getAttribute('type') || '',
      name: node.getAttribute('name') || '',
      placeholder: node.getAttribute('placeholder') || '',
      aria: node.getAttribute('aria-label') || '',
      autocomplete: node.getAttribute('autocomplete') || '',
      maxLength: node.maxLength || 0,
      disabled: node.disabled,
      readonly: node.readOnly,
      valueLength: node.value?.length || 0,
    }));
    const haystack = `${meta.type} ${meta.name} ${meta.placeholder} ${meta.aria} ${meta.autocomplete}`.toLowerCase();
    if (meta.disabled || meta.readonly) continue;
    if (/email|mail|почт(?!.*код)/.test(haystack)) continue;
    if (/hidden|submit|button|checkbox|radio/.test(meta.type)) continue;
    if (meta.maxLength === 1 || meta.maxLength >= value.length || meta.maxLength <= 0 || meta.valueLength <= 1) {
      candidates.push({ input, meta });
    }
  }

  const oneCharInputs = candidates.filter(({ meta }) => meta.maxLength === 1);
  if (oneCharInputs.length >= value.length) {
    for (let i = 0; i < value.length; i += 1) {
      await oneCharInputs[i].input.fill(value[i]);
      await page.waitForTimeout(80);
    }
  } else {
    const target = candidates[0]?.input || inputs.first();
    await target.click();
    await target.fill('').catch(() => null);
    await target.type(value, { delay: 90 });
  }

  await page.waitForTimeout(1200);
  const submitClicked =
    (await clickFirst(page.getByRole('button', { name: /подтвердить|продолжить|войти|далее|готово/i }), 5000).catch(() => false)) ||
    false;
  if (!submitClicked) await page.keyboard.press('Enter').catch(() => null);
}

async function chooseCompanyIfNeeded(page) {
  await page.waitForTimeout(2500);
  const summary = await summarizePage(page);
  const status = classify(summary);
  if (status.loggedIn) return;

  const expected = String(opts.expectedStore || '').trim();
  const pattern = expected ? new RegExp(escapeRegExp(expected), 'i') : /vital\s*shevron/i;
  const storeText = page.getByText(pattern).first();
  if (await storeText.isVisible().catch(() => false)) {
    await storeText.click().catch(() => null);
    await clickFirst(page.getByRole('button', { name: /продолжить|далее|выбрать|войти/i }), 8000).catch(() => null);
  }
}

async function exportState(context) {
  return exportNormalizedStorageState(context, opts.state, { backup: true });
}

function publicResult(status, summary, stateExported) {
  const pageStatus = classify(summary);
  return {
    status,
    url: summary.url,
    title: summary.title,
    expectedStore: opts.expectedStore,
    expectedStoreFound: pageStatus.expectedStoreFound,
    rrMode: pageStatus.rrMode,
    userAgent: summary.userAgent,
    webdriver: summary.webdriver,
    statePath: opts.state,
    stateExported,
  };
}

(async () => {
  fs.mkdirSync(opts.profile, { recursive: true });
  fs.mkdirSync(path.dirname(opts.state), { recursive: true });

  const context = await chromium.launchPersistentContext(opts.profile, browserLaunchOptions({
    headless: false,
    channel: 'chrome',
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    userAgent: opts.userAgent,
    extraHTTPHeaders: { 'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7' },
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  }));

  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(20000);

  let summary;
  let status;

  if (opts.precheckDashboard) {
    await page.goto(dashboardUrl, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => null);
    await page.waitForTimeout(5000);
    summary = await summarizePage(page);
    status = classify(summary);

    if (status.blocked) {
      console.log(JSON.stringify(publicResult('BLOCKED_BEFORE_LOGIN', summary, false), null, 2));
      await context.close();
      process.exit(20);
    }

    if (status.loggedIn) {
      await exportState(context);
      console.log(JSON.stringify(publicResult(status.expectedStoreFound ? 'ALREADY_LOGGED_IN' : 'ALREADY_LOGGED_IN_STORE_UNCONFIRMED', summary, true), null, 2));
      await context.close();
      process.exit(status.expectedStoreFound ? 0 : 12);
    }
  }

  await page.goto(signinUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(3000);

  await clickFirst(page.getByRole('button', { name: /^Войти$/ }), 20000).catch(() => false);
  await page.waitForTimeout(2500);
  await clickFirst(page.getByText(/войти по почте/i), 20000).catch(() => false);
  summary = await summarizePage(page);
  status = classify(summary);
  if (status.blocked) {
    console.log(JSON.stringify(publicResult('BLOCKED_BEFORE_EMAIL', summary, false), null, 2));
    await context.close();
    process.exit(20);
  }
  await fillEmail(page);
  await clickFirst(page.getByRole('button', { name: /^Войти$/ }), 20000);

  await page.waitForFunction(
    () => /otp|код/i.test(location.href) || /введите\s+код|отправили.{0,80}код|код на почту|код из смс/i.test(document.body?.innerText || ''),
    null,
    { timeout: 90000 },
  );

  for (let stage = 1; stage <= opts.maxCodes; stage += 1) {
    await page.waitForTimeout(1500);
    summary = await summarizePage(page);
    status = classify(summary);

    if (status.blocked) {
      console.log(JSON.stringify(publicResult('BLOCKED_DURING_LOGIN', summary, false), null, 2));
      await context.close();
      process.exit(20);
    }

    if (!status.codeScreen && status.loggedIn) break;

    console.log(JSON.stringify({
      status: stage === 1 ? 'READY_FOR_CODE' : 'READY_FOR_ADDITIONAL_CODE',
      codeStage: stage,
      url: summary.url,
      title: summary.title,
      inputs: await visibleInputs(page),
    }, null, 2));

    const code = await askLine(`OZON_CODE_${stage}> `);
    await enterCode(page, code);
    await page.waitForTimeout(5000);
    await chooseCompanyIfNeeded(page);

    summary = await summarizePage(page);
    status = classify(summary);
    if (status.loggedIn) break;
    if (!status.codeScreen) {
      await page.goto(dashboardUrl, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => null);
      await page.waitForTimeout(6000);
      summary = await summarizePage(page);
      status = classify(summary);
      if (status.loggedIn) break;
    }
  }

  if (status.loggedIn) {
    await page.goto(dashboardUrl, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => null);
    await page.waitForTimeout(5000);
    summary = await summarizePage(page);
    status = classify(summary);
    await exportState(context);
    console.log(JSON.stringify(publicResult(status.expectedStoreFound ? 'LOGIN_SUCCESS' : 'LOGIN_SUCCESS_STORE_UNCONFIRMED', summary, true), null, 2));
    await context.close();
    process.exit(status.expectedStoreFound ? 0 : 12);
  }

  console.log(JSON.stringify(publicResult('LOGIN_FAILED', summary, false), null, 2));
  await context.close();
  process.exit(status.needsLogin ? 10 : 11);
})().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
