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
const companyType = 'seller';

function parseArgs(argv) {
  const args = { approvedPath: '', runDir: '' };
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

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const text = fs.readFileSync(filePath, 'utf8');
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    const key = match[1];
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

function readFirstLine(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return '';
  return fs.readFileSync(filePath, 'utf8').split(/\r?\n/).map((line) => line.trim()).find(Boolean) || '';
}

function firstEnv(...names) {
  for (const name of names) {
    if (process.env[name]) return process.env[name];
  }
  return '';
}

function loadCompanyId() {
  loadEnvFile(path.join(projectRoot, '.env'));
  if (process.env.OZON_REVIEW_COMPANY_ID) return process.env.OZON_REVIEW_COMPANY_ID;
  if (process.env.OZON_SELLER_CLIENT_ID) return process.env.OZON_SELLER_CLIENT_ID;
  return readFirstLine(firstEnv(
    'VITAL_SHEVRON_OZON_SELLER_CREDENTIALS_FILE',
    'SELLER_OZON_SELLER_CREDENTIALS_FILE',
  ));
}

function compactResponse(response) {
  return {
    ok: response.ok,
    status: response.status,
    jsonParseOk: response.jsonParseOk,
    json: response.json,
  };
}

function chunkList(items, size) {
  const chunks = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

async function post(page, url, body, pageType, companyId) {
  return page.evaluate(async ({ url, body, companyId, pageType }) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: {
        accept: 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'accept-language': 'ru',
        'x-o3-app-name': 'seller-ui',
        'x-o3-language': 'ru',
        'x-o3-company-id': companyId,
        'x-o3-page-type': pageType,
      },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    let json = null;
    let jsonParseOk = true;
    try {
      json = text ? JSON.parse(text) : {};
    } catch {
      jsonParseOk = false;
    }
    return { ok: response.ok, status: response.status, json, jsonParseOk };
  }, { url, body, companyId, pageType });
}

async function summarizePage(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.title,
    text: (document.body?.innerText || '').slice(0, 3000),
    webdriver: navigator.webdriver,
  }));
}

function classifyPage(summary) {
  const all = `${summary.url}\n${summary.title}\n${summary.text}`;
  const expectedRegex = new RegExp(String(expectedStore).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
  const sellerShellOpen = /главная|товары|цены|цены и акции|fbo|fbs|финансы|аналитика|продавец|dashboard/i.test(all);
  const rrMode = /[?&]__rr=1/i.test(summary.url);
  return {
    expectedStoreFound: expectedRegex.test(all),
    blocked: /похоже, нет\s*соединения|доступ ограничен|инцидент|captcha|access denied/i.test(all) || (rrMode && !sellerShellOpen),
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
  return { summary, status: classifyPage(summary) };
}

async function fetchCounter(page, companyId) {
  const counter = await post(page, '/api/review/counter', {
    company_id: companyId,
    company_type: companyType,
  }, 'review', companyId);
  return compactResponse(counter);
}

(async () => {
  const args = parseArgs(process.argv);
  const approvedPath = assertInsideProject(args.approvedPath);
  const runDir = assertInsideProject(args.runDir);
  const rawDir = path.join(runDir, 'raw', 'ozon_apply');
  ensureDir(rawDir);

  const approved = JSON.parse(fs.readFileSync(approvedPath, 'utf8'));
  const approvedActions = Array.isArray(approved.actions) ? approved.actions : [];
  const replyActions = approvedActions.filter((action) => (
    action.platform === 'ozon' &&
    action.action_type === 'public_review_reply' &&
    action.approved === true &&
    action.draft_text
  ));
  const questionActions = approvedActions.filter((action) => (
    action.platform === 'ozon' &&
    action.source_type === 'question' &&
    action.action_type === 'question_answer' &&
    action.approved === true &&
    action.source_id &&
    action.draft_text
  ));
  const markViewedActions = approvedActions.filter((action) => (
    action.platform === 'ozon' &&
    action.source_type === 'review' &&
    action.action_type === 'mark_review_viewed' &&
    action.approved === true &&
    action.source_id
  ));

  const result = {
    source: 'ozon_lk_cdp_internal_api',
    cdpUrl,
    expectedStore,
    ok: false,
    blocker: '',
    beforeCounter: null,
    beforeQuestionsCounter: null,
    afterCounter: null,
    afterQuestionsCounter: null,
    sent: [],
    questions_answered: [],
    marked_viewed: [],
    mark_viewed_batches: [],
    skipped: [],
    valuesPrinted: false,
  };

  let page;
  try {
    const companyId = loadCompanyId();
    if (!companyId) throw new Error('Missing Ozon company id. Set OZON_REVIEW_COMPANY_ID or Ozon seller credentials file.');
    if (!replyActions.length && !markViewedActions.length && !questionActions.length) {
      result.ok = true;
      result.skipped.push({ reason: 'no_approved_ozon_actions' });
      return;
    }

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

    const cabinet = await verifyCabinet(page);
    if (cabinet.status.blocked) throw new Error('Ozon blocked/no-connection page detected');
    if (cabinet.status.needsLogin) throw new Error('Login required in Ozon LK');
    if (!cabinet.status.expectedStoreFound) throw new Error('Expected Vital Shevron store marker not found');

    await page.goto('https://seller.ozon.ru/app/reviews', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(3000);
    result.beforeCounter = await fetchCounter(page, companyId);

    for (const action of replyActions) {
      const response = await post(page, '/api/review/comment/create', {
        company_id: companyId,
        company_type: companyType,
        review_uuid: action.source_id,
        text: action.draft_text,
      }, 'review', companyId);
      const row = {
        source_id: action.source_id,
        offer_id: action.offer_id,
        sku: action.sku,
        rating: action.rating,
        status: response.status,
        ok: response.ok,
        response,
      };
      result.sent.push(row);
      if (!response.ok) {
        throw new Error(`Ozon public reply failed for ${action.offer_id}/${action.source_id}: HTTP ${response.status}`);
      }
      await page.waitForTimeout(700);
    }

    if (questionActions.length) {
      await page.goto('https://seller.ozon.ru/app/reviews/questions', { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(3000);
      result.beforeQuestionsCounter = await post(page, '/api/v1/get-new-question-counter', {
        sc_company_id: companyId,
        company_type: companyType,
      }, 'questions', companyId);

      for (const action of questionActions) {
        const response = await post(page, '/api/v1/create-answer', {
          text: action.draft_text,
          question_id: String(action.source_id),
          questionId: String(action.source_id),
          sc_company_id: companyId,
          company_type: companyType,
        }, 'questions', companyId);
        const row = {
          source_id: action.source_id,
          offer_id: action.offer_id,
          sku: action.sku,
          status: response.status,
          ok: response.ok,
          response: compactResponse(response),
        };
        result.questions_answered.push(row);
        if (!response.ok) {
          throw new Error(`Ozon question answer failed for ${action.offer_id}/${action.source_id}: HTTP ${response.status}`);
        }
        await page.waitForTimeout(700);
      }
    }

    const markBatches = chunkList(markViewedActions, 50);
    for (let batchIndex = 0; batchIndex < markBatches.length; batchIndex += 1) {
      const batch = markBatches[batchIndex];
      const response = await post(page, '/api/v2/review/change-interaction-status', {
        company_id: companyId,
        company_type: companyType,
        review_status_list: batch.map((action) => ({
          review_uuid: action.source_id,
          interaction_status: 'VIEWED',
        })),
      }, 'review', companyId);
      const compacted = compactResponse(response);
      result.mark_viewed_batches.push({
        batch_index: batchIndex + 1,
        count: batch.length,
        status: response.status,
        ok: response.ok,
        response: compacted,
      });
      for (const action of batch) {
        result.marked_viewed.push({
          source_id: action.source_id,
          offer_id: action.offer_id,
          sku: action.sku,
          rating: action.rating,
          status: response.status,
          ok: response.ok,
        });
      }
      if (!response.ok) {
        throw new Error(`Ozon mark viewed failed for batch ${batchIndex + 1}: HTTP ${response.status}`);
      }
      await page.waitForTimeout(700);
    }

    await page.waitForTimeout(3000);
    result.afterCounter = await fetchCounter(page, companyId);
    if (questionActions.length) {
      result.afterQuestionsCounter = await post(page, '/api/v1/get-new-question-counter', {
        sc_company_id: companyId,
        company_type: companyType,
      }, 'questions', companyId);
    }
    result.ok = true;
  } catch (error) {
    result.blocker = error.message || String(error);
    process.exitCode = /Login required/.test(result.blocker) ? 10 : /blocked|no-connection|Vital Shevron store/.test(result.blocker) ? 20 : 1;
  } finally {
    if (page) await page.close().catch(() => null);
    const resultPath = path.join(rawDir, 'ozon_apply_result.json');
    writeJson(resultPath, result);
    console.log(JSON.stringify(result, null, 2));
    setImmediate(() => process.exit(process.exitCode || 0));
  }
})();
