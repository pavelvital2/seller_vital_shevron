#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const { chromium, browserLaunchOptions } = require('../lib/playwright');

const projectRoot = path.resolve(__dirname, '../..');
const defaultProfile = path.join(projectRoot, '.sessions', 'wb', 'browser-profile');
const args = process.argv.slice(2);
const opts = {
  out: '',
  profile: process.env.WB_BROWSER_PROFILE || defaultProfile,
  limit: 30,
};

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i];
  if (arg === '--out') opts.out = path.resolve(args[++i]);
  else if (arg === '--profile') opts.profile = path.resolve(args[++i]);
  else if (arg === '--limit') opts.limit = Number(args[++i] || 30);
  else if (arg === '--help') {
    console.log([
      'Usage: node scripts/notifications/wb_news_readonly.js --out FILE [options]',
      '',
      'Options:',
      '  --out FILE      JSON output file.',
      '  --profile DIR   Persistent WB browser profile.',
      '  --limit N       Max news rows. Default: 30.',
    ].join('\n'));
    process.exit(0);
  } else {
    throw new Error(`Unknown argument: ${arg}`);
  }
}

if (!opts.out) throw new Error('--out is required');

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function uniqueBy(items, keyFn) {
  const seen = new Set();
  const result = [];
  for (const item of items) {
    const key = keyFn(item);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

async function main() {
  fs.mkdirSync(path.dirname(opts.out), { recursive: true });
  const context = await chromium.launchPersistentContext(opts.profile, browserLaunchOptions({
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  }));
  const page = context.pages()[0] || await context.newPage();
  const checkedAt = new Date().toISOString();
  const result = {
    status: 'ok',
    source: 'WB LK news-v2 read-only',
    checkedAt,
    url: '',
    title: '',
    items: [],
    blocker: '',
  };

  try {
    await page.goto('https://seller.wildberries.ru/news-v2', {
      waitUntil: 'domcontentloaded',
      timeout: 90000,
    });
    await page.waitForTimeout(8000);
    result.url = page.url();
    result.title = await page.title();

    if (/login|passport|auth/i.test(result.url)) {
      result.status = 'auth_required';
      result.blocker = `WB LK redirected to auth page: ${result.url}`;
    } else {
      const items = await page.evaluate((limit) => {
        function clean(value) {
          return String(value || '').replace(/\s+/g, ' ').trim();
        }
        const anchors = Array.from(document.querySelectorAll('a[href*="/news-v2/news-details"]'));
        const rows = anchors.map((anchor) => {
          const href = anchor.href || anchor.getAttribute('href') || '';
          const card = anchor.closest('article, li, [class*="card"], [class*="Card"], [class*="item"], [class*="Item"], div') || anchor;
          const text = clean(card.innerText || anchor.innerText || '');
          const lines = text.split(/\n| {2,}/).map(clean).filter(Boolean);
          const title = clean(anchor.innerText) || lines.find((line) => line.length > 8) || text.slice(0, 160);
          const date = lines.find((line) => /\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b/.test(line)) || '';
          return {
            title,
            date,
            href,
            text: text.slice(0, 1000),
          };
        });
        return rows.slice(0, limit);
      }, Math.max(1, Math.min(Number(opts.limit) || 30, 100)));
      result.items = uniqueBy(
        items.map((item) => ({
          title: cleanText(item.title),
          date: cleanText(item.date),
          href: cleanText(item.href),
          text: cleanText(item.text),
        })),
        (item) => item.href || `${item.title}:${item.date}`,
      );
    }
  } catch (error) {
    result.status = 'error';
    result.blocker = cleanText(error && error.message ? error.message : error).slice(0, 1000);
  } finally {
    await context.close();
  }

  fs.writeFileSync(opts.out, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  if (result.status === 'error') process.exitCode = 2;
}

main().catch((error) => {
  fs.mkdirSync(path.dirname(opts.out), { recursive: true });
  fs.writeFileSync(opts.out, `${JSON.stringify({
    status: 'error',
    source: 'WB LK news-v2 read-only',
    checkedAt: new Date().toISOString(),
    blocker: cleanText(error && error.message ? error.message : error).slice(0, 1000),
    items: [],
  }, null, 2)}\n`, 'utf8');
  process.exit(2);
});
