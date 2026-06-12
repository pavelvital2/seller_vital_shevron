#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '..', '..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const expectedStore = process.env.OZON_EXPECTED_STORE || 'Vital Shevron';
const companyType = 'seller';

function parseArgs(argv) {
  const args = {
    runDir: '',
    limit: 100,
  };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === '--run-dir') {
      args.runDir = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--limit') {
      args.limit = Number(argv[i + 1] || 100);
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
  loadEnvFile(path.join(projectRoot, '.sessions', 'ozon', 'ozon_api_credentials.env'));
  if (process.env.OZON_REVIEW_COMPANY_ID) return process.env.OZON_REVIEW_COMPANY_ID;
  if (process.env.OZON_SELLER_CLIENT_ID) return process.env.OZON_SELLER_CLIENT_ID;
  return readFirstLine(firstEnv(
    'VITAL_SHEVRON_OZON_SELLER_CREDENTIALS_FILE',
    'SELLER_OZON_SELLER_CREDENTIALS_FILE',
    'TAKTERRA_OZON_SELLER_CREDENTIALS_FILE',
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

function pick(obj, camel, snake) {
  return obj?.[camel] ?? obj?.[snake] ?? '';
}

function normalizeReview(review) {
  return {
    platform: 'ozon',
    source_type: 'review',
    id: review.uuid || review.id || '',
    published_at: review.published_at || review.publishedAt || '',
    rating: review.rating || '',
    interaction_status: review.interaction_status || '',
    is_empty: review.is_empty ?? '',
    is_commentable: review.is_commentable ?? '',
    is_commentable_2: review.is_commentable_2 ?? '',
    comments_count: review.comments_count ?? '',
    offer_id: review.product?.offer_id || '',
    sku: review.product?.sku || '',
    product_title: review.product?.title || '',
    text: review.text || '',
    pros: review.pros || '',
    cons: review.cons || '',
    needs_public_reply: Boolean(review.text) && review.is_commentable_2 === true,
    can_mark_viewed: !review.text && review.interaction_status === 'NOT_VIEWED',
  };
}

function normalizeQuestion(question) {
  const product = question.product || {};
  const company = question.companyInfo || question.company_info || {};
  const brand = question.brandInfo || question.brand_info || {};
  const author = question.author || {};
  return {
    platform: 'ozon',
    source_type: 'question',
    id: String(question.id || question.question_id || ''),
    published_at: pick(question, 'publishedAt', 'published_at'),
    status: question.status || '',
    is_answerable: pick(question, 'isAnswerable', 'is_answerable'),
    answers_count: pick(question, 'answersCount', 'answers_count'),
    usefulness_count: pick(question, 'usefulnessCount', 'usefulness_count'),
    offer_id: pick(product, 'offerId', 'offer_id'),
    sku: product.sku || '',
    product_title: product.title || '',
    brand_name: brand.name || '',
    company_name: company.name || '',
    author_name_exists: Boolean(author.name),
    text: question.text || '',
  };
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

async function fetchReviews(page, companyId, limit) {
  await page.goto('https://seller.ozon.ru/app/reviews', {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await page.waitForTimeout(3000);

  const counter = await post(page, '/api/review/counter', {
    company_id: companyId,
    company_type: companyType,
  }, 'review', companyId);

  const pages = [];
  const seenNotViewed = new Set();
  let lastReview = null;
  for (let i = 0; i < 30; i += 1) {
    const body = {
      company_id: companyId,
      company_type: companyType,
      filter: { published_at: {}, interaction_status: ['ALL'] },
    };
    if (lastReview) body.last_review = lastReview;
    const result = await post(page, '/api/v4/review/list', body, 'review', companyId);
    pages.push(compactResponse(result));
    const items = Array.isArray(result.json?.result) ? result.json.result : [];
    for (const item of items) {
      if (item.interaction_status === 'NOT_VIEWED' && item.uuid) seenNotViewed.add(item.uuid);
    }
    lastReview = result.json?.last_review || null;
    const target = Number(counter.json?.items?.NOT_VIEWED || 0);
    if (!items.length || seenNotViewed.size >= limit || (target > 0 && seenNotViewed.size >= target) || !result.json?.hasNext || !lastReview) break;
  }

  const merged = [];
  const seen = new Set();
  for (const pageResult of pages) {
    const items = Array.isArray(pageResult.json?.result) ? pageResult.json.result : [];
    for (const item of items) {
      if (item.interaction_status === 'NOT_VIEWED' && item.uuid && !seen.has(item.uuid)) {
        seen.add(item.uuid);
        merged.push(item);
      }
    }
  }

  return { counter: compactResponse(counter), pages, result: merged.slice(0, limit) };
}

async function fetchQuestions(page, companyId, limit) {
  await page.goto('https://seller.ozon.ru/app/reviews/questions', {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await page.waitForTimeout(3000);

  const counter = await post(page, '/api/v1/get-new-question-counter', {
    sc_company_id: companyId,
    company_type: companyType,
  }, 'questions', companyId);

  const allCounters = await post(page, '/api/v1/question-counter', {
    sc_company_id: companyId,
    company_type: companyType,
  }, 'questions', companyId);

  const pages = [];
  let paginationLastId = '0';
  let lastPublishedAt = '';
  for (let i = 0; i < 10; i += 1) {
    const body = {
      sc_company_id: companyId,
      with_brands: i === 0,
      with_counters: i === 0,
      company_type: companyType,
      filter: { status: 'NEW' },
      pagination_last_id: paginationLastId,
      last_published_at: lastPublishedAt,
    };
    const result = await post(page, '/api/v1/question-list', body, 'questions', companyId);
    pages.push(compactResponse(result));
    const items = Array.isArray(result.json?.result) ? result.json.result : [];
    paginationLastId = result.json?.paginationLastId || result.json?.pagination_last_id || '0';
    lastPublishedAt = result.json?.lastPublishedAt || result.json?.last_published_at || lastPublishedAt;
    if (!items.length || paginationLastId === '0' || pages.flatMap((item) => item.json?.result || []).length >= limit) break;
  }

  const merged = [];
  const seen = new Set();
  for (const pageResult of pages) {
    const items = Array.isArray(pageResult.json?.result) ? pageResult.json.result : [];
    for (const item of items) {
      const id = String(item.id || item.question_id || '');
      if (id && !seen.has(id)) {
        seen.add(id);
        merged.push(item);
      }
    }
  }

  return { counter: compactResponse(counter), allCounters: compactResponse(allCounters), pages, result: merged.slice(0, limit) };
}

(async () => {
  const args = parseArgs(process.argv);
  const runDir = assertInsideProject(args.runDir);
  const rawDir = path.join(runDir, 'raw', 'ozon_lk');
  const processedDir = path.join(runDir, 'processed');
  ensureDir(rawDir);
  ensureDir(processedDir);

  const result = {
    source: 'ozon_lk_cdp_internal_api',
    cdpUrl,
    expectedStore,
    ok: false,
    blocker: '',
    outputs: {},
    valuesPrinted: false,
  };
  let page;

  try {
    const companyId = loadCompanyId();
    if (!companyId) throw new Error('Missing Ozon company id. Set OZON_REVIEW_COMPANY_ID or Ozon seller credentials file.');

    const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 10000 });
    const context = browser.contexts()[0];
    if (!context) throw new Error('No browser context found in CDP session');
    page = await context.newPage();
    page.setDefaultTimeout(15000);

    const cabinet = await verifyCabinet(page);
    if (cabinet.status.blocked) throw new Error('Ozon blocked/no-connection page detected');
    if (cabinet.status.needsLogin) throw new Error('Login required in Ozon LK');
    if (!cabinet.status.expectedStoreFound) throw new Error('Expected Vital Shevron store marker not found');

    const reviews = await fetchReviews(page, companyId, args.limit);
    const questions = await fetchQuestions(page, companyId, args.limit);
    const normalizedReviews = reviews.result.map(normalizeReview);
    const normalizedQuestions = questions.result.map(normalizeQuestion);

    const reviewsRawPath = path.join(rawDir, 'reviews_fetch.json');
    const questionsRawPath = path.join(rawDir, 'questions_fetch.json');
    const reviewsProcessedPath = path.join(processedDir, 'ozon_reviews.json');
    const questionsProcessedPath = path.join(processedDir, 'ozon_questions.json');
    writeJson(reviewsRawPath, reviews);
    writeJson(questionsRawPath, questions);
    writeJson(reviewsProcessedPath, normalizedReviews);
    writeJson(questionsProcessedPath, normalizedQuestions);

    result.ok = true;
    result.outputs = {
      reviews_raw: path.relative(projectRoot, reviewsRawPath),
      questions_raw: path.relative(projectRoot, questionsRawPath),
      reviews_processed: path.relative(projectRoot, reviewsProcessedPath),
      questions_processed: path.relative(projectRoot, questionsProcessedPath),
      reviews_count: normalizedReviews.length,
      questions_count: normalizedQuestions.length,
    };
  } catch (error) {
    result.blocker = error.message || String(error);
    process.exitCode = /Login required/.test(result.blocker) ? 10 : /blocked|no-connection|Vital Shevron store/.test(result.blocker) ? 20 : 1;
  } finally {
    if (page) await page.close().catch(() => null);
    const summaryPath = path.join(runDir, 'raw', 'ozon_lk_summary.json');
    writeJson(summaryPath, result);
    console.log(JSON.stringify(result, null, 2));
    setImmediate(() => process.exit(process.exitCode || 0));
  }
})();
