#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '../..');
const defaultProfile = path.join(projectRoot, '.sessions', 'wb', 'browser-profile');
const args = process.argv.slice(2);
const today = new Date().toISOString().slice(0, 10);
const opts = {
  day: process.env.WB_ACTIONS_DATE || today,
  outDir: process.env.WB_ACTIONS_OUT_DIR || '',
  pricesDir: process.env.WB_PRICES_DIR || '',
  profile: process.env.WB_BROWSER_PROFILE || defaultProfile,
};

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === '--date') opts.day = args[++i];
  else if (arg === '--out-dir') opts.outDir = path.resolve(args[++i]);
  else if (arg === '--prices-dir') opts.pricesDir = path.resolve(args[++i]);
  else if (arg === '--profile') opts.profile = path.resolve(args[++i]);
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/actions/wb_download_active_actions.js [options]',
      '',
      'Options:',
      '  --date YYYY-MM-DD   Date label for output folders. Default: today.',
      '  --out-dir DIR       Output folder for actions snapshot and Excel files.',
      '  --prices-dir DIR    Output folder for current prices JSON.',
      '  --profile DIR       Persistent WB browser profile.',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

const day = opts.day;
const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
const outDir = opts.outDir || path.join(projectRoot, 'data', 'runs', day, `wb_actions_snapshot_${stamp}`);
const excelDir = path.join(outDir, 'excel');
const pricesDir = opts.pricesDir || path.join(outDir, 'prices');

const calendarBase = 'https://discounts-prices.wildberries.ru/ns/calendar-api/dp-calendar';
const pricesUrl = 'https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/list/goods/filter';

fs.mkdirSync(excelDir, { recursive: true });
fs.mkdirSync(pricesDir, { recursive: true });

function safeName(value) {
  return String(value || 'promo')
    .replace(/[\\/:*?"<>|]+/g, '_')
    .replace(/\s+/g, '_')
    .slice(0, 120);
}

function withoutFiles(snapshot) {
  const copy = JSON.parse(JSON.stringify(snapshot));
  for (const row of copy.excel || []) {
    for (const item of row.downloads || []) {
      if (item.file) item.file = `base64:${item.file.length}`;
    }
  }
  return copy;
}

function timelineWindow(now) {
  const start = new Date(now.getTime() - 140 * 24 * 60 * 60 * 1000);
  const end = new Date(now.getTime() + 220 * 24 * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

async function main() {
  const context = await chromium.launchPersistentContext(opts.profile, browserLaunchOptions({
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  }));

  const page = context.pages()[0] || await context.newPage();
  await page.goto('https://seller.wildberries.ru/dp-promo-calendar?', {
    waitUntil: 'domcontentloaded',
    timeout: 90000,
  });
  await page.waitForTimeout(12000);

  const actionsSnapshot = await page.evaluate(async ({ calendarBase }) => {
    const now = new Date();
    const start = new Date(now.getTime() - 140 * 24 * 60 * 60 * 1000).toISOString();
    const end = new Date(now.getTime() + 220 * 24 * 60 * 60 * 1000).toISOString();
    const headers = {
      'wb-seller-lk': localStorage.getItem('wb-eu-portal.seller-token'),
      authorizev3: localStorage.getItem('wb-eu-passport-v2.access-token'),
      'root-version': localStorage.getItem('@root/latest-app-version') || 'v1.96.1',
      'content-type': 'application/json',
    };

    async function request(method, url, body) {
      const response = await fetch(url, {
        method,
        headers,
        credentials: 'include',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      return { status: response.status, data };
    }

    const timeline = await request(
      'GET',
      `${calendarBase}/web/api/v3/promotions/timeline?endDate=${encodeURIComponent(end)}&filter=AVAILABLE&startDate=${encodeURIComponent(start)}`,
    );

    const actionRows = [];
    for (let offset = 0; offset < 1000; offset += 200) {
      const actions = await request(
        'GET',
        `${calendarBase}/suppliers/api/v2/promo/actions/list?startDateTime=${encodeURIComponent(start)}&endDateTime=${encodeURIComponent(end)}&limit=200&offset=${offset}`,
      );
      const rows = Array.isArray(actions.data?.data) ? actions.data.data : [];
      actionRows.push(...rows);
      if (rows.length < 200) break;
    }

    const timelineRows = Array.isArray(timeline.data?.data?.promotions)
      ? timeline.data.data.promotions
      : (Array.isArray(timeline.data?.data) ? timeline.data.data : []);
    const byActionID = new Map(actionRows.map((item) => [item.actionID, item]));
    const activeRows = timelineRows.filter((item) => {
      const status = item.participation?.status || item.status;
      return status === 'PARTICIPATING'
        && new Date(item.startDate) <= now
        && now <= new Date(item.endDate);
    });
    const futureRows = timelineRows.filter((item) => {
      const status = item.participation?.status || item.status;
      return status === 'WILL_PARTICIPATE' && new Date(item.startDate) > now;
    });
    const promos = activeRows.map((widgetItem) => {
      const actionItem = byActionID.get(widgetItem.promoID) || {};
      const counts = widgetItem.participation?.counts || widgetItem.participation?.itemCountsDetails || {};
      return {
        actionID: widgetItem.promoID,
        periodID: actionItem.periodID || widgetItem.periodID,
        name: widgetItem.name || actionItem.name,
        type: widgetItem.type || (actionItem.isAutoAction ? 'AUTO_PROMO' : 'PROMO'),
        status: widgetItem.participation?.status || widgetItem.status,
        itemCountsDetails: {
          participate: counts.participating,
          participating: counts.participating,
          available: counts.available,
          eligible: counts.eligible,
          participatingOutOfStock: counts.participatingOutOfStock,
          availableOutOfStock: counts.availableOutOfStock,
        },
        startDate: widgetItem.startDate || actionItem.startDt,
        endDate: widgetItem.endDate || actionItem.endDt,
      };
    });
    const futurePromos = futureRows.map((widgetItem) => ({
      actionID: widgetItem.promoID,
      name: widgetItem.name,
      status: widgetItem.participation?.status || widgetItem.status,
      itemCountsDetails: widgetItem.participation?.itemCountsDetails,
      startDate: widgetItem.startDate,
      endDate: widgetItem.endDate,
    }));

    const excel = [];
    for (const promo of promos) {
      const downloads = [];
      for (const inAction of [true, false]) {
        if (!promo.periodID) {
          downloads.push({ inAction, create: null, polls: [], error: 'periodID not found', file: null });
          continue;
        }
        const create = await request('POST', `${calendarBase}/suppliers/api/v2/excel/create`, {
          periodID: promo.periodID,
          inAction,
        });
        const polls = [];
        let file = null;
        for (let attempt = 1; attempt <= 10; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, attempt === 1 ? 2500 : 5000));
          const poll = await request('GET', `${calendarBase}/suppliers/api/v2/excel?periodID=${promo.periodID}&inAction=${inAction}`);
          const pollForLog = JSON.parse(JSON.stringify(poll));
          if (pollForLog.data?.data?.file) pollForLog.data.data.file = `base64:${pollForLog.data.data.file.length}`;
          polls.push({ attempt, ...pollForLog });
          if (poll.data?.data?.file) {
            file = poll.data.data.file;
            break;
          }
        }
        downloads.push({ inAction, create, polls, file });
      }
      excel.push({ promo, downloads });
    }

    return {
      url: location.href,
      title: document.title,
      checkedAt: now.toISOString(),
      window: { start, end },
      hasSellerToken: Boolean(headers['wb-seller-lk']),
      hasAuthorizeV3: Boolean(headers.authorizev3),
      rootVersion: headers['root-version'],
      timeline,
      actions: { count: actionRows.length, data: actionRows },
      promos,
      futurePromos,
      excel,
    };
  }, { calendarBase });

  if (!actionsSnapshot.hasSellerToken || !actionsSnapshot.hasAuthorizeV3) {
    throw new Error('WB seller tokens were not found in browser localStorage');
  }

  await page.goto('https://seller.wildberries.ru/discount-and-prices/main-table', {
    waitUntil: 'domcontentloaded',
    timeout: 90000,
  });
  await page.waitForTimeout(12000);

  const pricesSnapshot = await page.evaluate(async ({ pricesUrl }) => {
    const headers = {
      'wb-seller-lk': localStorage.getItem('wb-eu-portal.seller-token'),
      authorizev3: localStorage.getItem('wb-eu-passport-v2.access-token'),
      'root-version': localStorage.getItem('@root/latest-app-version') || 'v1.96.1',
      'content-type': 'application/json',
    };

    async function request(method, url, body) {
      const response = await fetch(url, {
        method,
        headers,
        credentials: 'include',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = text;
      }
      return { status: response.status, data };
    }

    const pages = [];
    const goods = [];
    for (let offset = 0; offset < 10000; offset += 50) {
      const response = await request('POST', pricesUrl, {
        limit: 50,
        offset,
        facets: [],
        filterWithoutPrice: false,
        filterWithLeftovers: false,
        filterWithoutCompetitivePrice: false,
        sort: 'price',
        sortOrder: 0,
      });
      const rows = response.data?.data?.listGoods || response.data?.data?.goods || response.data?.data || [];
      const pageRows = Array.isArray(rows) ? rows : [];
      pages.push({ offset, status: response.status, count: pageRows.length });
      goods.push(...pageRows);
      if (pageRows.length < 50) break;
    }
    return {
      url: location.href,
      title: document.title,
      hasSellerToken: Boolean(headers['wb-seller-lk']),
      hasAuthorizeV3: Boolean(headers.authorizev3),
      rootVersion: headers['root-version'],
      prices: { pages, count: goods.length, goods },
    };
  }, { pricesUrl });

  const snapshot = { ...actionsSnapshot, pricesPage: pricesSnapshot.url, prices: pricesSnapshot.prices };
  fs.writeFileSync(path.join(outDir, 'cabinet-actions-snapshot.json'), JSON.stringify(withoutFiles(snapshot), null, 2));

  for (const row of snapshot.excel || []) {
    for (const item of row.downloads || []) {
      if (!item.file) continue;
      const mode = item.inAction ? 'in-action' : 'not-in-action';
      const filename = `promo-${row.promo.actionID}-period-${row.promo.periodID}-${mode}-${safeName(row.promo.name)}.xlsx`;
      fs.writeFileSync(path.join(excelDir, filename), Buffer.from(item.file, 'base64'));
    }
  }

  const pricesPath = path.join(pricesDir, `current-prices-list-goods-filter-${stamp}.json`);
  fs.writeFileSync(pricesPath, JSON.stringify(snapshot.prices, null, 2));
  fs.writeFileSync(path.join(pricesDir, 'current-prices-list-goods-filter-latest.json'), JSON.stringify(snapshot.prices, null, 2));

  console.log(JSON.stringify({
    outDir,
    excelDir,
    pricesPath,
    checkedAt: snapshot.checkedAt,
    hasSellerToken: snapshot.hasSellerToken,
    hasAuthorizeV3: snapshot.hasAuthorizeV3,
    rootVersion: snapshot.rootVersion,
    activePromos: snapshot.promos.length,
    futurePromos: snapshot.futurePromos.length,
    excelFiles: (snapshot.excel || []).reduce((count, row) => count + row.downloads.filter((item) => item.file).length, 0),
    pricePages: snapshot.prices.pages,
    pricesCount: snapshot.prices.count,
  }, null, 2));

  await context.close();
}

timelineWindow(new Date());
main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
