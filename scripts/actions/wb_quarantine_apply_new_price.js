#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '../..');
const defaultProfile = path.join(projectRoot, '.sessions', 'wb', 'browser-profile');
const quarantineUrl = 'https://seller.wildberries.ru/discount-and-prices/quarantine';
const quarantineGoodsUrl = 'https://discounts-prices.wildberries.ru/ns/dp-api/discounts-prices/suppliers/api/v1/quarantine/goods';

const args = process.argv.slice(2);
const opts = {
  targets: '',
  out: '',
  profile: process.env.WB_BROWSER_PROFILE || defaultProfile,
};

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === '--targets') opts.targets = path.resolve(args[++i]);
  else if (arg === '--out') opts.out = path.resolve(args[++i]);
  else if (arg === '--profile') opts.profile = path.resolve(args[++i]);
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/actions/wb_quarantine_apply_new_price.js --targets FILE --out FILE [options]',
      '',
      'Targets format: {"targets":[{"nmID":123,"price":1100,"discount":49}]}',
      '',
      'Options:',
      '  --profile DIR   Persistent WB browser profile.',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

if (!opts.targets) throw new Error('--targets is required');
if (!opts.out) throw new Error('--out is required');

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function cleanError(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, 2000);
}

function asInt(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.trunc(number) : null;
}

function targetKey(row) {
  return `${asInt(row.nmID)}|${asInt(row.price)}|${asInt(row.discount)}`;
}

function output(result) {
  fs.mkdirSync(path.dirname(opts.out), { recursive: true });
  fs.writeFileSync(opts.out, `${JSON.stringify(result, null, 2)}\n`);
}

async function main() {
  const targetsPayload = readJson(opts.targets);
  const targets = Array.isArray(targetsPayload.targets) ? targetsPayload.targets : [];
  if (!targets.length) throw new Error('targets list is empty');

  const context = await chromium.launchPersistentContext(opts.profile, browserLaunchOptions({
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  }));
  const page = context.pages()[0] || await context.newPage();
  const startedAt = new Date().toISOString();
  const result = {
    status: 'blocked',
    mode: 'apply_new_price',
    startedAt,
    finishedAt: '',
    targets_count: targets.length,
    before_count: 0,
    matched_count: 0,
    ids_count: 0,
    matched_targets: [],
    missing_targets: [],
    apply_response: null,
    after_count: 0,
    after_matched_remaining_count: 0,
    error: '',
  };

  try {
    await page.goto(quarantineUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForTimeout(6000);

    const lkResult = await page.evaluate(async ({ quarantineGoodsUrl, targets }) => {
      const headers = {
        'wb-seller-lk': localStorage.getItem('wb-eu-portal.seller-token'),
        authorizev3: localStorage.getItem('wb-eu-passport-v2.access-token'),
        'root-version': localStorage.getItem('@root/latest-app-version') || 'v1.96.1',
        'content-type': 'application/json',
      };
      if (!headers['wb-seller-lk'] || !headers.authorizev3) {
        throw new Error('WB seller tokens were not found in browser localStorage');
      }

      async function request(method, url, body) {
        const response = await fetch(url, {
          method,
          headers,
          credentials: 'include',
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        const text = await response.text();
        let json = null;
        try {
          json = text ? JSON.parse(text) : null;
        } catch {
          json = text;
        }
        return { status: response.status, ok: response.ok, json };
      }

      async function readQuarantine() {
        const response = await request('GET', `${quarantineGoodsUrl}?limit=1000&offset=0`);
        const rows = response.json?.data?.quarantineGoods;
        return {
          response,
          rows: Array.isArray(rows) ? rows : [],
        };
      }

      const before = await readQuarantine();
      const byTarget = new Map(targets.map((target) => [
        `${Number(target.nmID)}|${Number(target.price)}|${Number(target.discount)}`,
        target,
      ]));
      const matched = [];
      for (const row of before.rows) {
        const key = `${Number(row.nmID)}|${Number(row.newPrice)}|${Number(row.newDiscount)}`;
        if (!byTarget.has(key)) continue;
        matched.push({
          nmID: Number(row.nmID),
          price: Number(row.newPrice),
          discount: Number(row.newDiscount),
          internal_id: Number(row.id),
          vendorCode: row.vendorCode || '',
          title: row.title || '',
        });
      }
      const matchedKeys = new Set(matched.map((row) => `${row.nmID}|${row.price}|${row.discount}`));
      const missing = targets
        .filter((target) => !matchedKeys.has(`${Number(target.nmID)}|${Number(target.price)}|${Number(target.discount)}`))
        .map((target) => ({
          nmID: Number(target.nmID),
          price: Number(target.price),
          discount: Number(target.discount),
        }));

      let apply = null;
      if (matched.length) {
        apply = await request('POST', quarantineGoodsUrl, { data: matched.map((row) => row.internal_id) });
      }
      await new Promise((resolve) => setTimeout(resolve, 4000));
      const after = await readQuarantine();
      const remainingKeys = new Set(after.rows.map((row) => `${Number(row.nmID)}|${Number(row.newPrice)}|${Number(row.newDiscount)}`));
      const remainingMatched = matched.filter((row) => remainingKeys.has(`${row.nmID}|${row.price}|${row.discount}`));
      return {
        before_count: before.rows.length,
        before_response_status: before.response.status,
        matched,
        missing,
        apply,
        after_count: after.rows.length,
        after_response_status: after.response.status,
        remainingMatched,
      };
    }, { quarantineGoodsUrl, targets });

    result.before_count = lkResult.before_count;
    result.matched_count = lkResult.matched.length;
    result.ids_count = lkResult.matched.length;
    result.matched_targets = lkResult.matched;
    result.missing_targets = lkResult.missing;
    result.apply_response = lkResult.apply;
    result.after_count = lkResult.after_count;
    result.after_matched_remaining_count = lkResult.remainingMatched.length;
    if (!lkResult.matched.length) {
      result.status = 'blocked';
      result.error = 'no matching quarantine rows found';
    } else if (!lkResult.apply?.ok || lkResult.apply?.json?.error) {
      result.status = 'blocked';
      result.error = cleanError(lkResult.apply?.json?.errorText || `HTTP ${lkResult.apply?.status}`);
    } else if (lkResult.remainingMatched.length) {
      result.status = 'partial';
      result.error = 'some matched quarantine rows remained after Apply New Price';
    } else if (lkResult.missing.length) {
      result.status = 'partial';
      result.error = 'some target rows were not found in quarantine';
    } else {
      result.status = 'ok';
    }
  } catch (error) {
    result.status = 'blocked';
    result.error = cleanError(error && error.message ? error.message : error);
    process.exitCode = /Login required|tokens were not found|passport|auth/i.test(result.error) ? 10 : 1;
  } finally {
    result.finishedAt = new Date().toISOString();
    output(result);
    await context.close().catch(() => null);
  }
}

main().catch((error) => {
  output({
    status: 'blocked',
    mode: 'apply_new_price',
    targets_count: 0,
    error: cleanError(error && error.message ? error.message : error),
  });
  process.exit(1);
});
