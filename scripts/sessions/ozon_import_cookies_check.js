#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');
const {
  exportNormalizedStorageState,
  normalizeOzonCookies,
} = require('../lib/ozon_cookie_state');

const projectRoot = path.resolve(__dirname, '../..');
const defaultProfile = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');
const defaultState = path.join(projectRoot, '.sessions', 'ozon', 'ozon_seller_storage_state.json');
const defaultCookieFile = path.join(projectRoot, 'tmp', 'auth', 'ozon_user_cookies.json');
const dashboardUrl = 'https://seller.ozon.ru/app/dashboard/main';
const desktopUserAgent =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

const opts = {
  cookieFile: process.env.OZON_COOKIE_FILE || defaultCookieFile,
  profile: process.env.OZON_SELLER_PROFILE || defaultProfile,
  state: process.env.OZON_SELLER_STATE || defaultState,
  expectedStore: process.env.OZON_EXPECTED_STORE || 'Vital Shevron',
  userAgent: process.env.OZON_USER_AGENT || desktopUserAgent,
  headless: process.env.OZON_HEADLESS !== '0',
  keepOpen: false,
  deleteCookieFile: true,
};

for (let i = 2; i < process.argv.length; i += 1) {
  const arg = process.argv[i];
  if (arg === '--cookie-file') opts.cookieFile = path.resolve(process.argv[++i]);
  else if (arg === '--profile') opts.profile = path.resolve(process.argv[++i]);
  else if (arg === '--state') opts.state = path.resolve(process.argv[++i]);
  else if (arg === '--expected-store') opts.expectedStore = process.argv[++i];
  else if (arg === '--user-agent') opts.userAgent = process.argv[++i];
  else if (arg === '--headful') opts.headless = false;
  else if (arg === '--headless') opts.headless = true;
  else if (arg === '--keep-open') opts.keepOpen = true;
  else if (arg === '--keep-cookie-file') opts.deleteCookieFile = false;
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/sessions/ozon_import_cookies_check.js [options]',
      '',
      'Options:',
      '  --cookie-file FILE      Cookie file to import',
      '  --profile DIR          Persistent Chrome profile directory',
      '  --state FILE           Playwright storageState export path',
      '  --expected-store VALUE Store marker expected after login',
      '  --headful              Open visible browser window',
      '  --headless             Run without visible browser window',
      '  --keep-open            Keep browser process open after check',
      '  --keep-cookie-file     Do not delete cookie file after success',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function extractCookieHeaderText(text) {
  const raw = String(text || '');
  const lines = raw.split(/\r?\n/);
  const cookieLines = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^cookie\s*:/i.test(trimmed)) {
      cookieLines.push(trimmed.replace(/^cookie\s*:\s*/i, ''));
    }
  }
  if (cookieLines.length) return cookieLines.join('; ');
  return raw.trim().replace(/^cookie\s*:\s*/i, '');
}

function parseCookieHeader(text) {
  return extractCookieHeaderText(text)
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const eq = part.indexOf('=');
      if (eq <= 0) return null;
      return { name: part.slice(0, eq).trim(), value: part.slice(eq + 1).trim() };
    })
    .filter((cookie) => cookie && cookie.name);
}

function jsonSourceToCookies(parsed) {
  const source = parsed['Куки запроса'] || parsed.cookies || parsed.cookie || parsed;

  if (typeof source === 'string') return parseCookieHeader(source);

  if (source && typeof source === 'object' && !Array.isArray(source) && source.name && typeof source.value !== 'undefined') {
    return [source];
  }

  if (Array.isArray(source)) {
    return source
      .map((item) => {
        if (!item || typeof item !== 'object') return null;
        if (!item.name || typeof item.value === 'undefined') return null;
        return {
          name: String(item.name),
          value: String(item.value),
          domain: item.domain ? String(item.domain) : '.ozon.ru',
          path: item.path ? String(item.path) : '/',
          secure: typeof item.secure === 'boolean' ? item.secure : true,
          httpOnly: typeof item.httpOnly === 'boolean' ? item.httpOnly : false,
          sameSite: item.sameSite || item.same_site || 'None',
          expires: item.expires || item.expirationDate || item.expiration_date,
        };
      })
      .filter(Boolean);
  }

  if (source && typeof source === 'object') {
    return Object.entries(source)
      .filter(([, value]) => typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean')
      .map(([name, value]) => ({ name, value: String(value) }));
  }

  return [];
}

function parseJsonDocuments(text) {
  const docs = [];
  let start = -1;
  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (start < 0) {
      if (/\s/.test(ch)) continue;
      if (ch !== '{' && ch !== '[') throw new Error('Unsupported cookie file format');
      start = i;
      depth = 0;
    }

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === '\\') {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }

    if (ch === '"') {
      inString = true;
    } else if (ch === '{' || ch === '[') {
      depth += 1;
    } else if (ch === '}' || ch === ']') {
      depth -= 1;
      if (depth === 0) {
        const chunk = text.slice(start, i + 1);
        docs.push(JSON.parse(chunk));
        start = -1;
      }
    }
  }

  if (start >= 0) throw new Error('Unclosed JSON document in cookie file');
  return docs;
}

function normalizeSameSite(value) {
  const normalized = String(value || '').toLowerCase();
  if (normalized === 'strict') return 'Strict';
  if (normalized === 'lax') return 'Lax';
  return 'None';
}

function normalizeCookie(cookie) {
  const normalized = {
    name: String(cookie.name || '').trim(),
    value: String(cookie.value || ''),
    domain: cookie.domain ? String(cookie.domain) : '.ozon.ru',
    path: cookie.path ? String(cookie.path) : '/',
    secure: typeof cookie.secure === 'boolean' ? cookie.secure : true,
    httpOnly: typeof cookie.httpOnly === 'boolean' ? cookie.httpOnly : /^__Secure-|^__Host-|access|refresh|token|sid/i.test(String(cookie.name || '')),
    sameSite: normalizeSameSite(cookie.sameSite),
  };

  if (typeof cookie.expires === 'number' && Number.isFinite(cookie.expires) && cookie.expires > 0) {
    normalized.expires = cookie.expires > 1e12 ? Math.floor(cookie.expires / 1000) : Math.floor(cookie.expires);
  }

  return normalized.name ? normalized : null;
}

function loadCookies(cookieFile) {
  const text = fs.readFileSync(cookieFile, 'utf8').trim();
  if (!text) return [];

  let cookies;
  if (text.startsWith('{') || text.startsWith('[')) {
    let parsedDocs;
    try {
      parsedDocs = [JSON.parse(text)];
    } catch {
      parsedDocs = parseJsonDocuments(text);
    }
    cookies = parsedDocs.flatMap(jsonSourceToCookies);
  } else {
    cookies = parseCookieHeader(text);
  }

  return normalizeOzonCookies(cookies.map(normalizeCookie).filter(Boolean)).cookies;
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

function classify(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const expected = String(opts.expectedStore || '').trim();
  const expectedStoreFound =
    Boolean(expected && new RegExp(escapeRegExp(expected), 'i').test(all));
  const rrMode = /[?&]__rr=1/i.test(summary.url);
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|продавец|dashboard/i.test(all);
  const blockedText = /похоже, нет\s*соединения|доступ ограничен|инцидент|captcha|access denied|выключите vpn/i.test(all);
  const blocked = blockedText || (rrMode && !sellerShellOpen);
  const needsLogin = /registration\/signin|sso\.ozon|вход и регистрация|войти по почте|войти по номеру|введите код|\/otp/i.test(all);
  const loggedIn =
    !blocked &&
    !needsLogin &&
    /seller\.ozon\.ru\/app\//i.test(summary.url) &&
    /продавец|товары|цены|аналитика|продвижение|заказы|dashboard/i.test(all);
  return { expectedStoreFound, rrMode, blocked, needsLogin, loggedIn };
}

async function exportState(context, statePath) {
  return exportNormalizedStorageState(context, statePath, { backup: true });
}

function maybeDeleteCookieFile(cookieFile) {
  if (!opts.deleteCookieFile) return false;
  const authDir = path.resolve(projectRoot, 'tmp', 'auth') + path.sep;
  const resolved = path.resolve(cookieFile);
  if (!resolved.startsWith(authDir)) return false;
  fs.rmSync(resolved, { force: true });
  return true;
}

(async () => {
  const cookieFile = path.resolve(opts.cookieFile);
  if (!fs.existsSync(cookieFile)) throw new Error(`Cookie file not found: ${cookieFile}`);

  const cookies = loadCookies(cookieFile);
  if (!cookies.length) throw new Error('No cookies parsed from cookie file');

  fs.mkdirSync(opts.profile, { recursive: true });
  fs.mkdirSync(path.dirname(opts.state), { recursive: true });

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

  await context.clearCookies();
  await context.addCookies(cookies);

  const page = context.pages()[0] || await context.newPage();
  await page.goto(dashboardUrl, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => null);
  await page.waitForTimeout(10000);

  const summary = await summarizePage(page);
  const status = classify(summary);
  const stateExported = status.loggedIn;
  const cookieState = stateExported ? await exportState(context, opts.state) : null;
  const cookieFileDeleted = stateExported ? maybeDeleteCookieFile(cookieFile) : false;

  const result = {
    status: status.loggedIn
      ? (status.expectedStoreFound ? 'COOKIE_IMPORT_SUCCESS' : 'COOKIE_IMPORT_SUCCESS_STORE_UNCONFIRMED')
      : 'COOKIE_IMPORT_FAILED',
    url: summary.url,
    title: summary.title,
    expectedStore: opts.expectedStore,
    expectedStoreFound: status.expectedStoreFound,
    rrMode: status.rrMode,
    blocked: status.blocked,
    needsLogin: status.needsLogin,
    loggedIn: status.loggedIn,
    userAgent: summary.userAgent,
    webdriver: summary.webdriver,
    statePath: opts.state,
    stateExported,
    cookieState,
    cookieFileDeleted,
    cookieValuesPrinted: false,
    keepOpen: opts.keepOpen,
  };
  console.log(JSON.stringify(result, null, 2));

  if (opts.keepOpen && status.loggedIn) {
    await new Promise(() => {});
  }

  await context.close();
  if (status.blocked) process.exit(20);
  if (!status.loggedIn) process.exit(status.needsLogin ? 10 : 11);
  if (!status.expectedStoreFound) process.exit(12);
})().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
