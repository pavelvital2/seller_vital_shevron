#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const projectRoot = path.resolve(__dirname, '..', '..');

const DEFAULT_SEEDS = [
  'шеврон',
  'шеврон на липучке',
  'нашивка',
  'патч',
  'петлица',
  'комплект шевронов',
  'комплект нашивок',
  'комплект петлиц',
  'шеврон фсб',
  'шеврон мвд',
  'шеврон росгвардия',
  'шеврон фсин',
  'шеврон фсо',
  'шеврон фссп',
  'шеврон вдв',
  'шеврон бпла',
  'шеврон сво',
  'шеврон шторм',
  'шеврон прикол',
  'шеврон позывной',
  'шеврон полиция',
  'шеврон охрана',
  'шеврон security',
  'шеврон press',
  'шеврон дпс',
  'шеврон омон',
  'шеврон собр',
  'шеврон чвк',
  'шеврон кадет',
  'шеврон на рукав',
  'шеврон на кепку',
  'шеврон на спину',
  'нагрудный шеврон',
];

const args = process.argv.slice(2);
const opts = {
  outputDir: '',
  seeds: DEFAULT_SEEDS,
  limit: 50,
  ozonCdpUrl: process.env.OZON_CDP_URL || 'http://127.0.0.1:9544',
  ozonExpectedPort: Number(process.env.OZON_CDP_PORT || '9544'),
  ozonProfile: process.env.OZON_BROWSER_PROFILE || path.join(projectRoot, '.sessions', 'ozon', 'chrome-profile'),
  ozonPeriod: 'days_7',
  wbProfile: process.env.WB_BROWSER_PROFILE || path.join(projectRoot, '.sessions', 'wb', 'browser-profile'),
  wbInterval: 'week',
  skipOzon: false,
  skipWb: false,
};

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === '--output-dir') opts.outputDir = path.resolve(args[++i]);
  else if (arg === '--seeds') opts.seeds = parseSeeds(args[++i]);
  else if (arg === '--limit') opts.limit = Number(args[++i]);
  else if (arg === '--ozon-period') opts.ozonPeriod = args[++i];
  else if (arg === '--wb-interval') opts.wbInterval = args[++i];
  else if (arg === '--skip-ozon') opts.skipOzon = true;
  else if (arg === '--skip-wb') opts.skipWb = true;
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/search_queries/collect_seo_query_pack_sources.js [options]',
      '',
      'Read-only collector for Vital Shevron SEO query pack source tables.',
      '',
      'Options:',
      '  --output-dir DIR   Required output directory',
      '  --seeds CSV        Comma-separated seed queries',
      '  --limit N          Top rows per seed, default 50',
      '  --ozon-period P    Ozon period, default days_7',
      '  --wb-interval I    WB interval, default week',
      '  --skip-ozon        Do not collect Ozon',
      '  --skip-wb          Do not collect WB',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

if (!opts.outputDir) {
  throw new Error('--output-dir is required');
}

function parseSeeds(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function writeJson(filePath, value) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function csvValue(value) {
  const text = value === null || value === undefined ? '' : String(value);
  if (!/[",\n\r]/.test(text)) return text;
  return `"${text.replace(/"/g, '""')}"`;
}

function writeCsv(filePath, rows, fields) {
  ensureDir(path.dirname(filePath));
  const lines = [fields.join(',')];
  for (const row of rows) {
    lines.push(fields.map((field) => csvValue(row[field])).join(','));
  }
  fs.writeFileSync(filePath, `${lines.join('\n')}\n`, 'utf8');
}

function cleanNumber(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'number') return value;
  const text = String(value).replace(/\s+/g, '').replace(',', '.').replace(/[₽%]/g, '');
  const number = Number(text);
  return Number.isFinite(number) ? number : String(value);
}

function normalizeOzonRow(row, seed, index, collectedAt) {
  return {
    marketplace: 'ozon',
    seed_query: seed,
    rank: index + 1,
    query: String(row.query || '').trim(),
    frequency: cleanNumber(row.count),
    popularity: cleanNumber(row.count),
    add_to_cart: cleanNumber(row.uniqQueriesWCa),
    cart_conversion_pct: cleanNumber(row.ca),
    ordered_items: cleanNumber(row.ord),
    order_conversion_pct: cleanNumber(row.searchUsersToOrdUsers),
    ordered_sum_rub: cleanNumber(row.gmv),
    avg_buyer_price_rub: cleanNumber(row.avgCaRub),
    shown_items: cleanNumber(row.itemsViews),
    competitors: cleanNumber(row.uniqSellers),
    period: opts.ozonPeriod,
    source: 'ozon_lk_ui_response',
    collected_at: collectedAt,
  };
}

function normalizeWbRow(row, seed, index, collectedAt) {
  return {
    marketplace: 'wb',
    seed_query: seed,
    rank: index + 1,
    query: String(row.text || row.query || '').trim(),
    frequency: cleanNumber(row.frequency),
    popularity: cleanNumber(row.frequency),
    add_to_cart: '',
    cart_conversion_pct: '',
    ordered_items: '',
    order_conversion_pct: '',
    ordered_sum_rub: '',
    avg_buyer_price_rub: '',
    shown_items: '',
    competitors: '',
    avg_frequency_current: cleanNumber(row.avgFrequency?.current),
    avg_frequency_dynamic: cleanNumber(row.avgFrequency?.dynamics),
    frequency_dynamic: cleanNumber(row.frequencyDynamic),
    priority_item: String(row.priorityItem || '').trim(),
    period: opts.wbInterval,
    source: 'wb_lk_endpoint',
    collected_at: collectedAt,
  };
}

async function collectOzon() {
  assertOzonCdpContour({
    cdpUrl: opts.ozonCdpUrl,
    expectedPort: opts.ozonExpectedPort,
    expectedProfileDir: path.resolve(opts.ozonProfile),
  });

  const browser = await chromium.connectOverCDP(opts.ozonCdpUrl);
  const context = browser.contexts()[0];
  if (!context) throw new Error('No Ozon CDP context found');
  const page = context.pages()[0] || await context.newPage();
  const rows = [];
  const errors = [];

  try {
    await page.goto('https://seller.ozon.ru/app/analytics/what-to-sell/all-queries', {
      waitUntil: 'domcontentloaded',
      timeout: 45000,
    });
    await page.waitForTimeout(4000);

    const input = page.locator('input[placeholder="Поисковый запрос"]').first();
    for (const seed of opts.seeds) {
      const collectedAt = new Date().toISOString();
      try {
        const responsePromise = page
          .waitForResponse(
            (response) =>
              response.url().includes('/api/site/searchteam/Stats/queries/search/v2') &&
              response.request().method() === 'POST',
            { timeout: 20000 },
          )
          .catch(() => null);
        await input.click({ timeout: 10000 });
        await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
        await page.keyboard.type(seed);
        await page.keyboard.press('Enter');
        const response = await responsePromise;
        if (!response) {
          errors.push({ marketplace: 'ozon', seed_query: seed, error: 'no response from search endpoint' });
          continue;
        }
        const status = response.status();
        if (status < 200 || status >= 300) {
          const text = await response.text().catch(() => '');
          errors.push({ marketplace: 'ozon', seed_query: seed, status, error: text.slice(0, 300) });
          continue;
        }
        const payload = await response.json();
        const data = Array.isArray(payload.data) ? payload.data.slice(0, opts.limit) : [];
        data.forEach((row, index) => rows.push(normalizeOzonRow(row, seed, index, collectedAt)));
      } catch (error) {
        errors.push({ marketplace: 'ozon', seed_query: seed, error: error.message || String(error) });
      }
    }
  } finally {
    await browser.close().catch(() => null);
  }

  return { rows, errors };
}

async function collectWb() {
  const context = await chromium.launchPersistentContext(
    path.resolve(opts.wbProfile),
    browserLaunchOptions({
      headless: true,
      viewport: { width: 1440, height: 1000 },
      locale: 'ru-RU',
      timezoneId: 'Europe/Moscow',
    }),
  );
  const page = context.pages()[0] || await context.newPage();
  const rows = [];
  const errors = [];

  try {
    await page.goto('https://seller.wildberries.ru/search-analytics/popular-search-queries', {
      waitUntil: 'domcontentloaded',
      timeout: 90000,
    });
    await page.waitForTimeout(10000);

    const result = await page.evaluate(
      async ({ seeds, limit, interval }) => {
        const headers = {
          'wb-seller-lk': localStorage.getItem('wb-eu-portal.seller-token'),
          authorizev3: localStorage.getItem('wb-eu-passport-v2.access-token'),
          'root-version': localStorage.getItem('@root/latest-app-version') || 'v1.99.2',
          'content-type': 'application/json',
        };
        const tokenFlags = {
          hasSellerToken: Boolean(headers['wb-seller-lk']),
          hasAuthorizeV3: Boolean(headers.authorizev3),
        };
        const results = [];
        const failures = [];
        for (const seed of seeds) {
          const collectedAt = new Date().toISOString();
          try {
            const response = await fetch(
              'https://seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v2/search-analysis/search-texts',
              {
                method: 'POST',
                headers,
                credentials: 'include',
                body: JSON.stringify({
                  limit,
                  offset: 0,
                  subjectIDs: [],
                  itemIDs: [],
                  searchText: seed,
                  interval,
                  orderBy: { field: 'frequency', mode: 'desc' },
                }),
              },
            );
            const text = await response.text();
            if (!response.ok) {
              failures.push({ seed_query: seed, status: response.status, error: text.slice(0, 300) });
              continue;
            }
            const payload = text ? JSON.parse(text) : {};
            const data = Array.isArray(payload.data) ? payload.data.slice(0, limit) : [];
            results.push({ seed_query: seed, collected_at: collectedAt, data });
          } catch (error) {
            failures.push({ seed_query: seed, error: error.message || String(error) });
          }
        }
        return { tokenFlags, results, failures };
      },
      { seeds: opts.seeds, limit: opts.limit, interval: opts.wbInterval },
    );

    if (!result.tokenFlags?.hasSellerToken || !result.tokenFlags?.hasAuthorizeV3) {
      errors.push({ marketplace: 'wb', error: 'WB seller tokens were not found in browser localStorage' });
    }
    for (const item of result.results || []) {
      (item.data || []).forEach((row, index) => rows.push(normalizeWbRow(row, item.seed_query, index, item.collected_at)));
    }
    for (const error of result.failures || []) {
      errors.push({ marketplace: 'wb', ...error });
    }
  } finally {
    await context.close().catch(() => null);
  }

  return { rows, errors };
}

(async () => {
  ensureDir(opts.outputDir);
  const startedAt = new Date().toISOString();
  const allErrors = [];
  let ozonRows = [];
  let wbRows = [];

  if (!opts.skipOzon) {
    const result = await collectOzon();
    ozonRows = result.rows;
    allErrors.push(...result.errors);
  }
  if (!opts.skipWb) {
    const result = await collectWb();
    wbRows = result.rows;
    allErrors.push(...result.errors);
  }

  const commonFields = [
    'marketplace',
    'seed_query',
    'rank',
    'query',
    'frequency',
    'popularity',
    'add_to_cart',
    'cart_conversion_pct',
    'ordered_items',
    'order_conversion_pct',
    'ordered_sum_rub',
    'avg_buyer_price_rub',
    'shown_items',
    'competitors',
    'period',
    'source',
    'collected_at',
  ];
  const wbFields = [...commonFields, 'avg_frequency_current', 'avg_frequency_dynamic', 'frequency_dynamic', 'priority_item'];
  writeJson(path.join(opts.outputDir, 'ozon_top_queries.json'), ozonRows);
  writeCsv(path.join(opts.outputDir, 'ozon_top_queries.csv'), ozonRows, commonFields);
  writeJson(path.join(opts.outputDir, 'wb_top_queries.json'), wbRows);
  writeCsv(path.join(opts.outputDir, 'wb_top_queries.csv'), wbRows, wbFields);

  const summary = {
    run_id: path.basename(opts.outputDir),
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    overall_status: allErrors.length ? 'warning' : 'ok',
    mode: 'read_only',
    marketplaces: [
      ...(opts.skipOzon ? [] : ['ozon']),
      ...(opts.skipWb ? [] : ['wb']),
    ],
    seeds: opts.seeds,
    limit: opts.limit,
    ozon_period: opts.ozonPeriod,
    wb_interval: opts.wbInterval,
    ozon_rows: ozonRows.length,
    wb_rows: wbRows.length,
    errors: allErrors,
    artifacts: {
      ozon_top_queries_json: path.join(opts.outputDir, 'ozon_top_queries.json'),
      ozon_top_queries_csv: path.join(opts.outputDir, 'ozon_top_queries.csv'),
      wb_top_queries_json: path.join(opts.outputDir, 'wb_top_queries.json'),
      wb_top_queries_csv: path.join(opts.outputDir, 'wb_top_queries.csv'),
      summary: path.join(opts.outputDir, 'summary.json'),
    },
  };
  writeJson(path.join(opts.outputDir, 'summary.json'), summary);
  console.log(JSON.stringify(summary, null, 2));
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
