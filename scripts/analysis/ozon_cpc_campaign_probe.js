#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('../lib/playwright');
const { assertOzonCdpContour } = require('../lib/ozon_cdp_guard');

const root = path.resolve(__dirname, '../..');
const cdpUrl = process.env.OZON_CDP_URL || 'http://127.0.0.1:9544';
const outputPath = process.env.OUTPUT_PATH
  || path.join(root, 'data', 'runs', '2026-07-30', 'ozon_cpc_campaign_probe.json');

function safeUrl(value) {
  try {
    const url = new URL(value);
    for (const key of Array.from(url.searchParams.keys())) {
      if (/token|auth|key|secret|session|cookie/i.test(key)) url.searchParams.set(key, '<redacted>');
    }
    return url.toString();
  } catch {
    return String(value).slice(0, 500);
  }
}

(async () => {
  assertOzonCdpContour({
    cdpUrl,
    expectedPort: 9544,
    expectedProfileDir: path.join(root, '.sessions', 'ozon', 'chrome-profile'),
  });
  const browser = await chromium.connectOverCDP(cdpUrl);
  const context = browser.contexts()[0];
  const page = await context.newPage();
  const responses = [];
  page.on('response', async (response) => {
    const url = response.url();
    if (/campaign|advert|promotion|product/i.test(url)) {
      responses.push({ method: response.request().method(), status: response.status(), url: safeUrl(url) });
    }
  });
  await page.goto('https://seller.ozon.ru/app/advertisement/product/cpc', {
    waitUntil: 'domcontentloaded',
    timeout: 60000,
  });
  await page.waitForTimeout(8000);
  const links = await page.locator('a').evaluateAll((items) => items.map((item) => ({
    text: (item.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 300),
    href: item.href,
  })).filter((item) => item.text || item.href));
  const campaignLink = links.find((item) => item.text.includes('Все кроме позывных'));
  if (campaignLink) {
    await page.goto(campaignLink.href, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(8000);
  }
  const addButton = page.getByRole('button', { name: 'Добавить товары', exact: true });
  if (await addButton.count()) {
    await addButton.first().click();
    await page.waitForTimeout(5000);
    const listTab = page.getByText('Список', { exact: true });
    if (await listTab.count()) {
      await listTab.last().click();
      await page.waitForTimeout(2000);
    }
  }
  const result = {
    collected_at: new Date().toISOString(),
    current_url: safeUrl(page.url()),
    title: await page.title(),
    campaign_link: campaignLink ? { text: campaignLink.text, href: safeUrl(campaignLink.href) } : null,
    links: links.slice(0, 300).map((item) => ({ text: item.text, href: safeUrl(item.href) })),
    form_controls: await page.locator('input, textarea, button').evaluateAll((items) => items.map((item) => ({
      tag: item.tagName,
      text: (item.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200),
      placeholder: item.getAttribute('placeholder') || '',
      qa: item.getAttribute('data-qa') || item.getAttribute('data-testid') || '',
      disabled: item.disabled,
    })).slice(-300)),
    body_text: (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 20000),
    responses: responses.slice(-500),
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
  await page.close();
  await browser.close();
  process.stdout.write(`${JSON.stringify({ ok: true, output_path: outputPath, current_url: result.current_url })}\n`);
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});
