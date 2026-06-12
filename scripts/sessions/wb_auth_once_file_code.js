#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium, browserLaunchOptions } = require('../lib/playwright');

const phone = process.argv[2];
if (!/^\d{10}$/.test(phone || '')) {
  console.error('Usage: node scripts/wb_auth_once_file_code.js 9010518209');
  process.exit(2);
}

const projectRoot = path.resolve(__dirname, '../..');
const profile = process.env.WB_BROWSER_PROFILE || path.join(projectRoot, '.sessions', 'wb', 'browser-profile');
const outDir = path.join(projectRoot, 'tmp', 'auth', 'wb-auth-once');
const codePath = path.join(outDir, 'sms-code.txt');
const emailCodePath = path.join(outDir, 'email-code.txt');
const stopPath = path.join(outDir, 'STOP');
const statePath = path.join(projectRoot, '.sessions', 'wb', 'wb_storage_state.json');

fs.mkdirSync(outDir, { recursive: true });
fs.rmSync(codePath, { force: true });
fs.rmSync(emailCodePath, { force: true });
fs.rmSync(stopPath, { force: true });

function print(obj) {
  console.log(JSON.stringify(obj, null, 2));
}

async function bodyText(page, limit = 12000) {
  return (await page.locator('body').innerText().catch((error) => String(error))).slice(0, limit);
}

async function snapshot(label, page) {
  const data = {
    label,
    at: new Date().toISOString(),
    url: page.url(),
    title: await page.title().catch(() => ''),
    text: await bodyText(page),
  };
  fs.writeFileSync(path.join(outDir, `${label}.json`), JSON.stringify(data, null, 2));
  await page.screenshot({ path: path.join(outDir, `${label}.png`), fullPage: true }).catch(() => {});
  return data;
}

async function visibleInputs(page) {
  const result = [];
  const count = await page.locator('input').count();
  for (let i = 0; i < count; i += 1) {
    const input = page.locator('input').nth(i);
    if (!(await input.isVisible().catch(() => false))) continue;
    const type = await input.getAttribute('type').catch(() => null);
    const value = await input.inputValue().catch(() => '');
    const placeholder = await input.getAttribute('placeholder').catch(() => '');
    result.push({ input, type, value, placeholder });
  }
  return result;
}

async function fillPhoneOnce(page) {
  const inputs = await visibleInputs(page);
  const input = inputs.find(({ type }) => ['tel', 'text', 'number', null].includes(type));
  if (!input) throw new Error('Phone input not found');
  await input.input.focus();
  await input.input.fill(phone);
  await page.waitForTimeout(800);

  const selectors = [
    'button[type="submit"]',
    'button:has-text("Продолжить")',
    'button:has-text("Получить код")',
    'button:has-text("Войти")',
    'button:has-text("Далее")',
  ];
  for (const selector of selectors) {
    const button = page.locator(selector).first();
    if (!(await button.count().catch(() => 0))) continue;
    if (await button.isDisabled().catch(() => false)) continue;
    await button.click();
    return selector;
  }
  throw new Error('Submit button not found or disabled');
}

function normalizeCode(value) {
  const code = String(value || '').replace(/\D/g, '');
  if (!/^\d{4,8}$/.test(code)) return null;
  return code;
}

async function enterCodeOnce(page, code) {
  const inputs = await visibleInputs(page);
  const codeInputs = inputs.filter(({ type, value }) => ['tel', 'text', 'number', 'password', null].includes(type) && (!value || value.length < code.length));
  if (codeInputs.length >= code.length && code.length >= 4) {
    for (let i = 0; i < code.length; i += 1) {
      await codeInputs[i].input.focus();
      await codeInputs[i].input.fill(code[i]);
    }
  } else if (codeInputs.length) {
    await codeInputs[0].input.focus();
    await codeInputs[0].input.fill(code);
  } else if (inputs.length) {
    await inputs[inputs.length - 1].input.focus();
    await inputs[inputs.length - 1].input.fill(code);
  } else {
    throw new Error('Code input not found');
  }

  await page.waitForTimeout(1500);
  const button = page.locator('button[type="submit"], button:has-text("Подтвердить"), button:has-text("Войти"), button:has-text("Продолжить")').first();
  if (await button.count().catch(() => 0)) {
    if (!(await button.isDisabled().catch(() => false))) await button.click().catch(() => {});
  }
}

function isLoggedIn(url) {
  return /seller\.wildberries\.ru/.test(url) && !/seller-auth|login|passport|signin|auth/i.test(url);
}

function isAdditionalEmailCodeScreen(text) {
  return /sent to|выслан|почт|email|e-mail/i.test(text || '') && /код|code|6/i.test(text || '');
}

async function waitForSecondCode(page) {
  print({
    stage: 'email_code_required_waiting_for_file',
    emailCodePath,
    stopPath,
    url: page.url(),
    title: await page.title().catch(() => ''),
  });

  let lastSnapshotAt = 0;
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    if (fs.existsSync(stopPath)) {
      await snapshot('stopped-by-file-email-code', page);
      return { stopped: true };
    }

    if (fs.existsSync(emailCodePath)) {
      const code = normalizeCode(fs.readFileSync(emailCodePath, 'utf8'));
      if (!code) {
        print({ stage: 'bad_email_code_file_waiting', emailCodePath });
        fs.rmSync(emailCodePath, { force: true });
      } else {
        await snapshot('05-before-email-code', page);
        await enterCodeOnce(page, code);
        fs.rmSync(emailCodePath, { force: true });
        await page.waitForTimeout(15000);
        const afterEmailCode = await snapshot('06-after-email-code', page);
        return { afterEmailCode };
      }
    }

    if (Date.now() - lastSnapshotAt > 30000) {
      await snapshot('waiting-email-code-current', page);
      lastSnapshotAt = Date.now();
    }
    await page.waitForTimeout(2000);
  }

  const timeout = await snapshot('timeout-waiting-email-code', page);
  return { timeout };
}

(async () => {
  const context = await chromium.launchPersistentContext(profile, browserLaunchOptions({
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  }));

  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://seller.wildberries.ru/dp-promo-calendar?', { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(6000);
  const before = await snapshot('01-before-phone', page);

if (isLoggedIn(page.url())) {
    fs.mkdirSync(path.dirname(statePath), { recursive: true });
    await context.storageState({ path: statePath });
    fs.chmodSync(statePath, 0o600);
    print({ stage: 'already_logged_in', stateExported: true, outDir, url: page.url() });
    await context.close();
    return;
  }

  const clicked = await fillPhoneOnce(page);
  await page.waitForTimeout(10000);
  const afterPhone = await snapshot('02-after-phone-submit', page);
  print({
    stage: 'phone_submitted_once_waiting_for_sms_file',
    phone,
    clicked,
    codePath,
    stopPath,
    url: afterPhone.url,
    title: afterPhone.title,
    text: afterPhone.text,
  });

  let lastSnapshotAt = 0;
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    if (fs.existsSync(stopPath)) {
      await snapshot('stopped-by-file', page);
      print({ stage: 'stopped_by_file', outDir });
      await context.close();
      return;
    }

    const currentText = await bodyText(page, 3000);
    if (/Запрос кода возможен через|попробуйте позже|слишком много/i.test(currentText)) {
      const blocked = await snapshot('blocked-or-rate-limited', page);
      print({ stage: 'blocked_or_rate_limited', url: blocked.url, text: blocked.text, outDir });
      await context.close();
      return;
    }

    if (fs.existsSync(codePath)) {
      const code = normalizeCode(fs.readFileSync(codePath, 'utf8'));
      if (!code) {
        print({ stage: 'bad_code_file_waiting', codePath });
        fs.rmSync(codePath, { force: true });
      } else {
        await snapshot('03-before-code', page);
        await enterCodeOnce(page, code);
        fs.rmSync(codePath, { force: true });
        await page.waitForTimeout(15000);
        const afterCode = await snapshot('04-after-code', page);
        let loggedIn = isLoggedIn(page.url());
        let finalSnapshot = afterCode;
        if (!loggedIn && isAdditionalEmailCodeScreen(afterCode.text)) {
          const second = await waitForSecondCode(page);
          if (second.stopped) {
            print({ stage: 'stopped_by_file_waiting_email_code', outDir });
            await context.close();
            return;
          }
          if (second.timeout) {
            print({ stage: 'timeout_waiting_email_code', url: second.timeout.url, text: second.timeout.text, outDir });
            await context.close();
            return;
          }
          finalSnapshot = second.afterEmailCode;
          loggedIn = isLoggedIn(page.url());
        }
        if (loggedIn) {
          fs.mkdirSync(path.dirname(statePath), { recursive: true });
          await context.storageState({ path: statePath });
          fs.chmodSync(statePath, 0o600);
        }
        print({
          stage: 'code_entered_once',
          loggedIn,
          stateExported: loggedIn,
          url: finalSnapshot.url,
          title: finalSnapshot.title,
          text: finalSnapshot.text,
          outDir,
        });
        await context.close();
        return;
      }
    }

    if (Date.now() - lastSnapshotAt > 30000) {
      await snapshot('waiting-current', page);
      lastSnapshotAt = Date.now();
    }
    await page.waitForTimeout(2000);
  }

  const timeout = await snapshot('timeout-waiting-code', page);
  print({ stage: 'timeout_waiting_code', url: timeout.url, text: timeout.text, outDir });
  await context.close();
})().catch(async (error) => {
  fs.writeFileSync(path.join(outDir, 'error.json'), JSON.stringify({
    at: new Date().toISOString(),
    message: error.message,
    stack: error.stack,
  }, null, 2));
  console.error(error);
  process.exit(1);
});
