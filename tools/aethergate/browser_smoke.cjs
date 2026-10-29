const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const origin = process.env.AETHERGATE_SMOKE_ORIGIN;
assert.ok(origin && /^http:\/\/172\./.test(origin), 'Isolated Docker endpoint required');
const user = { id: 999999, email: 'browser-fixture@example.invalid', role: 'admin', status: 'active', balance: 0 };
const fullText = 'Complete current-turn review content. '.repeat(450) + 'END-OF-FULL-INPUT';
const config = {
  engine: 'openai', enabled: true, mode: 'pre_block', base_url: 'https://example.invalid',
  model: 'omni-moderation-latest', proxy_id: null, api_key_configured: false,
  api_key_masked: '', api_key_count: 0, api_key_masks: [], api_key_statuses: [],
  timeout_ms: 3000, sample_rate: 100, all_groups: true, group_ids: [],
  record_non_hits: false, worker_count: 4, queue_size: 32768, block_status: 403,
  block_message: 'Blocked', email_on_hit: false, auto_ban_enabled: false,
  ban_threshold: 10, violation_window_hours: 720, retry_count: 2,
  hit_retention_days: 180, non_hit_retention_days: 3, pre_hash_check_enabled: false,
  blocked_keywords: [], keyword_blocking_mode: 'keyword_and_api',
  thresholds: { harassment: 0.98, sexual: 0.65 }, model_filter: { type: 'all', models: [] },
};
const runtime = {
  engine: 'openai', enabled: true, risk_control_enabled: true, mode: 'pre_block',
  worker_count: 4, max_workers: 32, active_workers: 0, idle_workers: 4,
  queue_size: 32768, queue_length: 0, queue_usage_percent: 0, enqueued: 0,
  dropped: 0, processed: 0, errors: 0, pre_block_active: 0, pre_block_checked: 0,
  pre_block_allowed: 0, pre_block_blocked: 0, pre_block_errors: 0,
  pre_block_avg_latency_ms: 0, pre_block_api_key_active: 0,
  pre_block_api_key_available_count: 0, pre_block_api_key_total_calls: 0,
  pre_block_api_key_loads: [], api_key_statuses: [], flagged_hash_count: 0,
  last_cleanup_deleted_hit: 0, last_cleanup_deleted_non_hit: 0,
};
const row = {
  id: 7, request_id: 'browser-fixture', user_id: null, user_email: user.email,
  api_key_id: null, api_key_name: '', group_id: null, group_name: '',
  endpoint: '/v1/responses', provider: 'openai', model: 'fixture-model',
  mode: 'pre_block', action: 'allow', flagged: false, highest_category: '',
  highest_score: 0, matched_keyword: '', category_scores: {}, threshold_snapshot: {},
  input_excerpt: 'Short fixture excerpt', upstream_latency_ms: 2, error: '',
  violation_count: 0, auto_banned: false, email_sent: false, user_status: 'active',
  queue_delay_ms: null, created_at: '2026-09-30T00:00:00Z',
};

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height }, locale: 'zh-CN', serviceWorkers: 'block' });
      await context.addInitScript(user => {
        // The embedded application reads server-injected settings before any API
        // request. Keep that bootstrap source consistent with the mocked API.
        const settings = { site_name: 'stay', registration_enabled: false, custom_menu_items: [], run_mode: 'standard', risk_control_enabled: true };
        Object.defineProperty(window, '__APP_CONFIG__', { get: () => settings, set: () => {} });
        localStorage.setItem('auth_token', 'synthetic-browser-fixture');
        localStorage.setItem('auth_user', JSON.stringify(user));
        localStorage.setItem('sub2api_locale', 'zh');
        localStorage.setItem('admin_guide_' + user.id + '_admin_v4_interactive', 'true');
      }, user);
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.origin === origin || url.protocol === 'data:' ? route.continue() : route.abort();
      });
      const page = await context.newPage();
      const fixtureImage = await page.evaluate(() => {
        const canvas = document.createElement('canvas');
        canvas.width = 320;
        canvas.height = 180;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#278b62';
        ctx.fillRect(0, 0, 320, 180);
        ctx.fillStyle = '#ffffff';
        ctx.font = '22px sans-serif';
        ctx.fillText('Review image fixture', 42, 95);
        return canvas.toDataURL('image/png');
      });
      const errors = [];
      const mutations = [];
      const unknown = new Set();
      let detailRequests = 0;
      let legacy = false;
      page.on('pageerror', error => errors.push(error.message));
      // Every API request is intercepted. No real credentials, mutations or model calls.
      await context.route('**/api/v1/**', async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname.replace('/api/v1', '');
        if (request.method() !== 'GET') {
          mutations.push(request.method() + ' ' + path);
          return route.abort();
        }
        let data;
        if (path === '/auth/me') data = user;
        else if (path === '/settings/public') data = { site_name: 'stay', registration_enabled: false, custom_menu_items: [], run_mode: 'standard', risk_control_enabled: true };
        else if (path === '/admin/compliance') data = { required: false, version: 'fixture' };
        else if (path === '/admin/settings') data = { custom_menu_items: [], risk_control_enabled: true };
        else if (path === '/admin/payment/config') data = { enabled: false };
        else if (path === '/admin/risk-control/config') data = config;
        else if (path === '/admin/risk-control/status') data = runtime;
        else if (path === '/admin/groups/all' || path === '/admin/proxies/all') data = [];
        else if (path === '/admin/risk-control/logs') data = { items: [row], total: 1, page: 1, page_size: 20, pages: 1 };
        else if (path === '/admin/risk-control/logs/7') {
          detailRequests++;
          data = legacy ? row : { ...row, input_items: [
            { index: 0, source: 'user', type: 'text', text: fullText },
            { index: 1, source: 'tool', type: 'text', text: 'Complete tool result beyond audit budget. '.repeat(50) },
            { index: 2, source: 'tool', type: 'image', image_ref: fixtureImage },
          ] };
        } else {
          unknown.add(path);
          data = {};
        }
        await route.fulfill({ json: { code: 0, data } });
      });
      await page.goto(origin + '/admin/risk-control', { waitUntil: 'networkidle', timeout: 60000 });
      await page.screenshot({ path: '/tmp/stay-moderation-ready-' + name + '.png', animations: 'disabled' });
      const excerpt = page.getByRole('button', { name: 'Short fixture excerpt' });
      try {
        await excerpt.waitFor({ timeout: 20000 });
      } catch (error) {
        console.error(JSON.stringify({ url: page.url(), errors, unknownAPIPaths: [...unknown] }));
        throw error;
      }
      assert.equal(detailRequests, 0, 'List must not fetch complete input');
      await excerpt.click();
      const full = page.locator('[data-test="audit-full-input"]');
      await full.waitFor();
      assert.equal(await full.locator('pre').first().textContent(), fullText);
      assert.equal(detailRequests, 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false, name + ' page overflow');
      assert.equal(await full.evaluate(el => el.scrollWidth > el.clientWidth + 2), false, name + ' detail overflow');
      await page.screenshot({ path: '/tmp/stay-moderation-' + name + '.png', animations: 'disabled' });
      await full.locator('img').scrollIntoViewIfNeeded();
      await page.waitForFunction(() => {
        const image = document.querySelector('[data-test="audit-full-input"] img');
        return image && image.complete && image.naturalWidth === 320;
      });
      await full.locator('img').scrollIntoViewIfNeeded();
      const imageBox = await full.locator('img').boundingBox();
      assert.ok(imageBox && imageBox.width > 0 && imageBox.height > 0 && imageBox.y >= 0 && imageBox.y + imageBox.height <= height, name + ' image outside viewport');
      await page.screenshot({ path: '/tmp/stay-moderation-image-' + name + '.png', animations: 'disabled' });
      await page.getByRole('button', { name: /^(关闭|Close)$/ }).last().click();
      await full.waitFor({ state: 'hidden' });
      legacy = true;
      await excerpt.click();
      await page.waitForFunction(() => [...document.querySelectorAll('pre')].some(el => el.textContent === 'Short fixture excerpt'));
      assert.equal(await full.count(), 0, 'Legacy record must use excerpt fallback');
      assert.equal(detailRequests, 2);
      assert.deepEqual(errors, [], 'Browser errors');
      assert.deepEqual(mutations, [], 'Unexpected API mutations');
      console.log(JSON.stringify({ viewport: name, fullTextCharacters: fullText.length, detailRequests, imageLoaded: true, legacyFallback: true, unknownAPIPaths: [...unknown] }));
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
