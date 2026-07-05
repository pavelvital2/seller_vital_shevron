#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const projectRoot = path.resolve(__dirname, '../..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const expectedCdpPort = 9544;
const expectedProfileDir = path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile');
const startedAt = new Date();
const day = startedAt.toISOString().slice(0, 10);
const runId = `ozon_actions_boost_probe_${startedAt.toISOString().replace(/[-:]/g, '').slice(0, 15)}`;
const runDir = path.join(projectRoot, 'data', 'runs', day, runId);
const rawDir = path.join(runDir, 'raw');
const processedDir = path.join(runDir, 'processed');
const optimizerActionsPath = path.join(
  projectRoot,
  'data',
  'runs',
  '2026-07-05',
  'ozon_actions_optimizer_plan_20260705T085900',
  'processed',
  'actions_safe_snapshot.json',
);

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function safeFileName(value) {
  return String(value).replace(/[^a-zA-Z0-9_.-]+/g, '_').slice(0, 120);
}

function redactUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return String(value).slice(0, 500);
  }
  for (const key of Array.from(parsed.searchParams.keys())) {
    if (/token|auth|key|secret|code|session|cookie|jwt/i.test(key)) {
      parsed.searchParams.set(key, '<redacted>');
    }
  }
  return parsed.toString();
}

function truncate(value, limit = 2000) {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function findBoostLike(value, pathParts = [], result = []) {
  if (result.length > 200) return result;
  if (value === null || value === undefined) return result;
  if (typeof value !== 'object') return result;
  if (Array.isArray(value)) {
    for (let index = 0; index < Math.min(value.length, 20); index += 1) {
      findBoostLike(value[index], pathParts.concat(String(index)), result);
    }
    return result;
  }
  for (const [key, item] of Object.entries(value)) {
    const nextPath = pathParts.concat(key);
    if (/boost|буст|elastic|action.?price|price.?max|price.?min|discount|promotion|stock.?discount/i.test(key)) {
      result.push({ path: nextPath.join('.'), value: truncate(item, 500) });
    }
    if (typeof item === 'object' && item !== null) {
      findBoostLike(item, nextPath, result);
    }
  }
  return result;
}

function classifyEndpoint(url) {
  const text = url.toLowerCase();
  return /action|highlight|discount|boost|promotion|price|promo|stock/.test(text);
}

function loadActionIds() {
  if (!fs.existsSync(optimizerActionsPath)) return [];
  try {
    const rows = JSON.parse(fs.readFileSync(optimizerActionsPath, 'utf8'));
    return rows
      .map((row) => String(row.action_id || '').trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

async function collect(page, label, navigateFn) {
  const responses = [];
  const handler = async (response) => {
    const request = response.request();
    const url = response.url();
    const contentType = response.headers()['content-type'] || '';
    if (!/json/i.test(contentType) && !classifyEndpoint(url)) return;
    if (!/seller\.ozon\.ru|api-seller\.ozon\.ru|ozon/i.test(url)) return;
    let body = null;
    let json = null;
    let error = '';
    try {
      body = await response.text();
      if (body && body.trim().startsWith('{') || body && body.trim().startsWith('[')) {
        json = JSON.parse(body);
      }
    } catch (err) {
      error = err.message || String(err);
    }
    const boostLike = json ? findBoostLike(json) : [];
    const keep = classifyEndpoint(url) || boostLike.length > 0;
    if (!keep) return;
    const item = {
      label,
      url: redactUrl(url),
      method: request.method(),
      status: response.status(),
      contentType,
      boostLikeCount: boostLike.length,
      boostLike,
      error,
    };
    responses.push(item);
    const index = String(responses.length).padStart(3, '0');
    const rawPath = path.join(rawDir, `${safeFileName(label)}_${index}.json`);
    fs.writeFileSync(
      rawPath,
      JSON.stringify(
        {
          meta: item,
          body: json || truncate(body || '', 20000),
        },
        null,
        2,
      ),
    );
  };

  page.on('response', handler);
  try {
    await navigateFn();
    await page.waitForTimeout(12000);
  } finally {
    page.off('response', handler);
  }
  return responses;
}

(async () => {
  ensureDir(rawDir);
  ensureDir(processedDir);
  const result = {
    runId,
    startedAt: startedAt.toISOString(),
    finishedAt: '',
    ok: false,
    valuesPrinted: false,
    contourGuard: null,
    pages: [],
    responses: [],
    error: '',
    artifacts: {
      runDir,
      rawDir,
      processedDir,
      summary: path.join(runDir, 'summary.json'),
    },
  };

  let page;
  try {
    result.contourGuard = assertOzonCdpContour({
      cdpUrl,
      expectedPort: expectedCdpPort,
      expectedProfileDir,
    });
    const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 10000 });
    const context = browser.contexts()[0];
    if (!context) throw new Error('No browser context found in CDP session');
    page = await context.newPage();
    page.setDefaultTimeout(30000);

    const listResponses = await collect(page, 'highlights_list', async () => {
      await page.goto('https://seller.ozon.ru/app/highlights/list', {
        waitUntil: 'domcontentloaded',
        timeout: 60000,
      });
    });
    result.responses.push(...listResponses);
    result.pages.push(
      await page.evaluate(() => {
        const anchors = Array.from(document.querySelectorAll('a')).map((link) => ({
          text: (link.innerText || link.textContent || '').trim().slice(0, 300),
          href: link.href,
        }));
        return {
          url: location.href,
          title: document.title,
          text: (document.body?.innerText || '').slice(0, 5000),
          anchors: anchors.filter((item) => /акци|буст|highlight|price|скид/i.test(`${item.text} ${item.href}`)).slice(0, 100),
        };
      }),
    );

    const actionIds = loadActionIds();
    for (const actionId of actionIds) {
      const detailResponses = await collect(page, `highlight_${actionId}`, async () => {
        await page.goto(`https://seller.ozon.ru/app/highlights/${actionId}`, {
          waitUntil: 'domcontentloaded',
          timeout: 60000,
        });
      });
      result.responses.push(...detailResponses);
      result.pages.push(
        await page.evaluate(() => ({
          url: location.href,
          title: document.title,
          text: (document.body?.innerText || '').slice(0, 5000),
        })),
      );
    }

    result.ok = true;
  } catch (error) {
    result.error = error.message || String(error);
    process.exitCode = 1;
  } finally {
    if (page) await page.close().catch(() => null);
    result.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(processedDir, 'responses_summary.json'), JSON.stringify(result.responses, null, 2));
    fs.writeFileSync(path.join(processedDir, 'pages_summary.json'), JSON.stringify(result.pages, null, 2));
    fs.writeFileSync(result.artifacts.summary, JSON.stringify(result, null, 2));
    console.log(JSON.stringify({
      runId,
      ok: result.ok,
      responseCount: result.responses.length,
      boostLikeResponses: result.responses.filter((item) => item.boostLikeCount > 0).length,
      pages: result.pages.map((item) => ({ url: item.url, title: item.title, textSample: item.text.slice(0, 300) })),
      artifacts: result.artifacts,
      error: result.error,
    }, null, 2));
    setImmediate(() => process.exit(process.exitCode || 0));
  }
})();
