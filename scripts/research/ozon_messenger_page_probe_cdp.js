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
const targetUrl = 'https://seller.ozon.ru/app/messenger?group=customers_v2';

function parseArgs(argv) {
  const args = { runDir: '' };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === '--run-dir') {
      args.runDir = argv[i + 1];
      i += 1;
    }
  }
  if (!args.runDir) throw new Error('Missing required --run-dir');
  return args;
}

function assertInsideProject(filePath) {
  const resolved = path.resolve(filePath);
  const root = path.resolve(projectRoot);
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) {
    throw new Error(`Refusing to write outside project: ${resolved}`);
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

function sanitizeUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    const queryKeys = [...url.searchParams.keys()].sort();
    return {
      origin: url.origin,
      path: url.pathname,
      query_keys: queryKeys,
      full_without_values: `${url.origin}${url.pathname}${queryKeys.length ? `?${queryKeys.map((key) => `${key}=...`).join('&')}` : ''}`,
    };
  } catch {
    return { origin: '', path: rawUrl, query_keys: [], full_without_values: rawUrl };
  }
}

function shape(value, depth = 0) {
  if (depth > 5) return 'max_depth';
  if (value === null) return 'null';
  if (Array.isArray(value)) {
    return {
      type: 'array',
      length: value.length,
      item_shape: value.length ? shape(value[0], depth + 1) : 'empty',
    };
  }
  if (typeof value === 'object') {
    const result = {};
    for (const key of Object.keys(value).sort()) {
      result[key] = shape(value[key], depth + 1);
    }
    return result;
  }
  return typeof value;
}

function jsonKeys(postData) {
  if (!postData) return [];
  try {
    const parsed = JSON.parse(postData);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return Object.keys(parsed).sort();
    return [Array.isArray(parsed) ? 'array' : typeof parsed];
  } catch {
    return ['non_json'];
  }
}

function frameShape(payload) {
  const text = String(payload || '');
  try {
    const parsed = JSON.parse(text);
    return { json_parse_ok: true, length: text.length, shape: shape(parsed) };
  } catch {
    return { json_parse_ok: false, length: text.length, shape: null };
  }
}

function safeOutgoingFrame(payload) {
  try {
    const parsed = JSON.parse(String(payload || ''));
    const request = parsed.request || {};
    const params = request.params || {};
    const filter = params.filter || {};
    return {
      namespace: parsed.namespace || '',
      method: request.method || '',
      chat_type: filter.chatType || '',
      filters: Array.isArray(filter.filters) ? filter.filters : [],
      only_unread: typeof filter.onlyUnread === 'boolean' ? filter.onlyUnread : null,
      limit: typeof params.limit === 'number' ? params.limit : null,
      offset: typeof params.offset === 'number' ? params.offset : null,
      with_first_page_info: typeof params.withFirstPageInfo === 'boolean' ? params.withFirstPageInfo : null,
      with_theme: typeof params.withTheme === 'boolean' ? params.withTheme : null,
    };
  } catch {
    return null;
  }
}

function isInterestingUrl(rawUrl) {
  const lowered = rawUrl.toLowerCase();
  if (!lowered.includes('seller.ozon.ru')) return false;
  return lowered.includes('/api/') || lowered.includes('messenger') || lowered.includes('chat') || lowered.includes('customer');
}

async function summarizeVisibleUi(page) {
  return page.evaluate(() => {
    const texts = [];
    const selector = [
      'h1',
      'h2',
      'h3',
      'button',
      '[role="tab"]',
      '[role="button"]',
      'a[href*="messenger"]',
      'input[placeholder]',
      'textarea[placeholder]',
    ].join(',');
    for (const node of document.querySelectorAll(selector)) {
      const text = (node.innerText || node.getAttribute('aria-label') || node.getAttribute('placeholder') || '').trim();
      const href = node.getAttribute('href') || '';
      if (!text && !href) continue;
      texts.push({
        tag: node.tagName.toLowerCase(),
        role: node.getAttribute('role') || '',
        text: text.slice(0, 80),
        href: href.slice(0, 160),
      });
    }
    return {
      url: location.href,
      title: document.title,
      nodes: texts.slice(0, 120),
    };
  });
}

(async () => {
  const args = parseArgs(process.argv);
  const runDir = assertInsideProject(args.runDir);
  const rawDir = path.join(runDir, 'raw');
  ensureDir(rawDir);

  const requests = [];
  const responses = [];
  const websockets = [];
  const result = {
    source: 'ozon_lk_cdp_messenger_probe',
    target_url: targetUrl,
    cdp_url: cdpUrl,
    ok: false,
    blocker: '',
    values_printed: false,
    outputs: {},
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

    page.on('request', (request) => {
      const url = request.url();
      if (!isInterestingUrl(url)) return;
      requests.push({
        url: sanitizeUrl(url),
        method: request.method(),
        resource_type: request.resourceType(),
        post_data_keys: jsonKeys(request.postData()),
      });
    });

    page.on('response', async (response) => {
      const request = response.request();
      const url = response.url();
      if (!isInterestingUrl(url)) return;
      const item = {
        url: sanitizeUrl(url),
        method: request.method(),
        resource_type: request.resourceType(),
        status: response.status(),
        content_type: response.headers()['content-type'] || '',
        json_shape: null,
        body_note: '',
      };
      const contentType = item.content_type.toLowerCase();
      if (contentType.includes('application/json')) {
        try {
          const json = await response.json();
          item.json_shape = shape(json);
        } catch (error) {
          item.body_note = `json_unavailable:${error.message || String(error)}`;
        }
      }
      responses.push(item);
    });

    page.on('websocket', (ws) => {
      const item = {
        url: sanitizeUrl(ws.url()),
        sent_frames: 0,
        received_frames: 0,
        sent_frame_lengths: [],
        received_frame_lengths: [],
        sent_frame_shapes: [],
        received_frame_shapes: [],
        sent_safe_commands: [],
      };
      websockets.push(item);
      ws.on('framesent', (event) => {
        item.sent_frames += 1;
        const safeShape = frameShape(event.payload);
        item.sent_frame_lengths.push(safeShape.length);
        item.sent_frame_shapes.push(safeShape);
        const command = safeOutgoingFrame(event.payload);
        if (command) item.sent_safe_commands.push(command);
      });
      ws.on('framereceived', (event) => {
        item.received_frames += 1;
        const safeShape = frameShape(event.payload);
        item.received_frame_lengths.push(safeShape.length);
        item.received_frame_shapes.push(safeShape);
      });
    });

    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForTimeout(10000);
    for (const text of ['Только новые', 'По товару', 'По заказу', 'Без вашего ответа', 'Без ответа клиента']) {
      const button = page.getByText(text, { exact: true }).first();
      if (await button.count().catch(() => 0)) {
        await button.click().catch(() => null);
        await page.waitForTimeout(1500);
      }
    }
    await page.mouse.wheel(0, 1200).catch(() => null);
    await page.waitForTimeout(3000);
    const ui = await summarizeVisibleUi(page);

    const requestsPath = path.join(rawDir, 'requests_redacted.json');
    const responsesPath = path.join(rawDir, 'responses_shape_redacted.json');
    const websocketsPath = path.join(rawDir, 'websockets_redacted.json');
    const uiPath = path.join(rawDir, 'ui_summary_redacted.json');
    writeJson(requestsPath, requests);
    writeJson(responsesPath, responses);
    writeJson(websocketsPath, websockets);
    writeJson(uiPath, ui);

    result.ok = true;
    result.outputs = {
      requests: path.relative(projectRoot, requestsPath),
      responses: path.relative(projectRoot, responsesPath),
      websockets: path.relative(projectRoot, websocketsPath),
      ui_summary: path.relative(projectRoot, uiPath),
      request_count: requests.length,
      response_count: responses.length,
      websocket_count: websockets.length,
    };
  } catch (error) {
    result.blocker = error.message || String(error);
    process.exitCode = 1;
  } finally {
    if (page) await page.close().catch(() => null);
    writeJson(path.join(runDir, 'summary.json'), result);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    setTimeout(() => process.exit(process.exitCode || 0), 0);
  }
})();
