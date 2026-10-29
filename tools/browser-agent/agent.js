// JARVIS's browser agent: its own Chrome (separate profile, so its own logins), driven by Playwright over a
// pipe (no debug port another program could use). One request per line on stdin, one JSON reply per line on
// stdout: {"seq": 1, "op": "read", "url": "..."} -> {"seq": 1, "ok": true, ...} or {"seq": 1, "ok": false, "error": "..."}.
//
// It never types into password or card fields and never clicks by itself: system/webagent.py and
// skills/web.py decide what's allowed and ask first. Page text comes back as plain data.
const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');
const { chromium } = require('playwright-core');

const PROFILE = process.env.JARVIS_AGENT_PROFILE || path.join(os.homedir(), '.local/share/jarvis/agent-chrome');
const CHROME = process.env.JARVIS_AGENT_CHROME || '/opt/google/chrome/chrome';
const DOWNLOADS = path.join(os.homedir(), 'Downloads');
const MAX_TEXT = 20000;

let ctx = null;
let headless = true;
let page = null; // the agent's current page: what "snapshot" and "act" work on

async function browser(wantHeadless) {
  if (ctx && (wantHeadless === undefined || wantHeadless === headless)) return ctx;
  if (ctx) await ctx.close().catch(() => {});
  headless = wantHeadless === undefined ? headless : wantHeadless;
  ctx = await chromium.launchPersistentContext(PROFILE, {
    executablePath: CHROME, headless, acceptDownloads: true, viewport: headless ? { width: 1280, height: 900 } : null,
  });
  ctx.on('close', () => { ctx = null; page = null; });
  page = ctx.pages()[0] || null;
  return ctx;
}

async function current() {
  await browser();
  if (!page || page.isClosed()) page = ctx.pages().filter(p => !p.isClosed()).pop() || await ctx.newPage();
  return page;
}

async function goto(p, url) {
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await p.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});
}

// The readable part of a page: <article>/<main> when there is one, else the body, without menus and footers.
function pageText(max) {
  const root = document.querySelector('article, main, [role=main]') || document.body;
  if (!root) return '';
  const skip = 'nav, header, footer, aside, script, style, noscript, svg, [aria-hidden=true], [role=navigation]';
  const parts = [];
  const walk = (el) => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) parts.push(n.textContent);
      else if (n.nodeType === 1 && !n.matches(skip)) {
        const s = getComputedStyle(n);
        if (s.display === 'none' || s.visibility === 'hidden') continue;
        const block = /^(block|flex|grid|list-item|table|table-row)/.test(s.display);
        if (block) parts.push('\n');
        walk(n);
        if (block) parts.push('\n');
      }
    }
  };
  walk(root);
  return parts.join('').replace(/[ \t ]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim().slice(0, max);
}

async function read(p, max) {
  return { url: p.url(), title: await p.title(), text: await p.evaluate(pageText, max || MAX_TEXT) };
}

// Everything on the page you can click or type into, each tagged with a number "act" can use.
function interactive() {
  const out = [];
  const els = document.querySelectorAll('a[href], button, input, select, textarea, [role=button], [role=link], ' +
    '[role=checkbox], [role=tab], [role=menuitem], [contenteditable=true], summary');
  let n = 0;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || s.visibility === 'hidden' || s.display === 'none') continue;
    if (el.type === 'hidden') continue;
    const id = ++n;
    el.setAttribute('data-jarvis', id);
    const byId = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    const label = (el.getAttribute('aria-label') || (byId && byId.innerText) || (el.closest('label') || {}).innerText ||
      el.placeholder || el.innerText || el.value || el.title || el.name || el.alt || '').trim().replace(/\s+/g, ' ');
    const tag = el.tagName.toLowerCase();
    const item = { id, tag, label: label.slice(0, 80) };
    if (el.type && tag === 'input') item.type = el.type;
    if (el.type === 'submit' || (tag === 'button' && (el.type || 'submit') === 'submit' && el.form)) item.submit = true;
    if (el.autocomplete) item.autocomplete = el.autocomplete;
    if (el.name) item.name = el.name;
    if (tag === 'a') item.href = el.href.slice(0, 200);
    if (tag === 'select') item.options = [...el.options].map(o => o.text.trim()).slice(0, 15);
    if (tag === 'select' && el.selectedIndex >= 0) item.value = el.options[el.selectedIndex].text.trim();
    if ((tag === 'input' || tag === 'textarea') && el.type !== 'password' && el.value) item.value = el.value.slice(0, 80);
    if (el.type === 'checkbox' || el.type === 'radio') item.checked = el.checked;
    if (el.required) item.required = true;
    out.push(item);
    if (out.length >= 150) break;
  }
  return out;
}

async function snapshot(p) {
  const elements = await p.evaluate(interactive);
  return { url: p.url(), title: await p.title(), text: await p.evaluate(pageText, 3000), elements };
}

const SECRET = /password|passwd|cc-|card.?number|cvv|cvc|security.?code|otp|one.?time|pin\b/i;

async function element(p, id) {
  const el = p.locator('[data-jarvis="' + Number(id) + '"]').first();
  if (!(await el.count())) throw new Error('that element is gone from the page');
  return el;
}

async function act(p, req) {
  const { action, id, value } = req;
  if (action === 'goto') { await goto(p, value); return snapshot(p); }
  if (action === 'back') { await p.goBack({ waitUntil: 'domcontentloaded' }).catch(() => {}); return snapshot(p); }
  if (action === 'scroll') { await p.mouse.wheel(0, value === 'up' ? -700 : 700); return snapshot(p); }
  const el = await element(p, id);
  const info = await el.evaluate(e => ({ type: e.type || '', autocomplete: e.autocomplete || '', name: e.name || '',
    id: e.id || '', tag: e.tagName.toLowerCase() }));
  if (action === 'fill') {
    if (info.type === 'password' || info.type === 'file' || SECRET.test(info.autocomplete + ' ' + info.name + ' ' + info.id))
      throw new Error('I never type into password, card or code fields');
    await el.fill(String(value));
  } else if (action === 'select') {
    await el.selectOption({ label: String(value) }).catch(() => el.selectOption(String(value)));
  } else if (action === 'check') {
    await el.setChecked(value !== false && value !== 'false');
  } else if (action === 'press') {
    await el.press(String(value || 'Enter'));
  } else if (action === 'click') {
    const [nav] = await Promise.all([
      p.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 5000 }).catch(() => null),
      el.click({ timeout: 8000 }),
    ]);
    if (!nav) await p.waitForTimeout(600);
  } else if (action === 'upload') {
    if (info.type !== 'file') throw new Error("that isn't a file box");
    await el.setInputFiles(String(value));
  } else {
    throw new Error('unknown action ' + action);
  }
  await p.waitForLoadState('networkidle', { timeout: 3000 }).catch(() => {});
  return snapshot(p);
}

function freeName(name) {
  name = String(name || '').replace(/[\/\0]/g, '_').trim() || 'download';
  let dest = path.join(DOWNLOADS, name);
  for (let i = 1; fs.existsSync(dest); i++) dest = path.join(DOWNLOADS, name.replace(/(\.[^.]*)?$/, ' (' + i + ')$1'));
  return dest;
}

// A link on the page (clicked as a download), or a URL fetched with the agent's cookies (a PDF would only open
// in Chrome's viewer otherwise).
async function download(p, req) {
  fs.mkdirSync(DOWNLOADS, { recursive: true });
  if (req.id) {
    const el = await element(p, req.id);
    const [dl] = await Promise.all([p.waitForEvent('download', { timeout: 30000 }), el.click({ modifiers: ['Alt'] })]);
    const dest = freeName(dl.suggestedFilename());
    await dl.saveAs(dest);
    return { path: dest };
  }
  const res = await ctx.request.get(req.url, { timeout: 120000 });
  if (!res.ok()) throw new Error('the site answered ' + res.status());
  const cd = res.headers()['content-disposition'] || '';
  const named = (cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i) || [])[1];
  const dest = freeName(decodeURIComponent(named || new URL(res.url()).pathname.split('/').pop() || ''));
  fs.writeFileSync(dest, await res.body());
  return { path: dest };
}

// Search results from Bing. (DuckDuckGo shows the agent a "prove you're human" puzzle, which it doesn't do.)
async function search(req) {
  const p = await ctx.newPage();
  try {
    await goto(p, 'https://www.bing.com/search?q=' + encodeURIComponent(req.query));
    const results = await p.evaluate(() => [...document.querySelectorAll('#b_results > li.b_algo')].map(r => {
      const a = r.querySelector('h2 a');
      if (!a) return null;
      let url = a.href;
      const u = new URL(url, location.href).searchParams.get('u'); // bing.com/ck/a?...&u=a1<base64 url>
      if (/bing\.com\/ck\//.test(url) && u && u.startsWith('a1')) {
        try { url = atob(u.slice(2).replace(/-/g, '+').replace(/_/g, '/')); } catch (e) {}
      }
      return { title: a.innerText.trim(), url, snippet: ((r.querySelector('.b_caption p, p') || {}).innerText || '').trim() };
    }).filter(r => r && /^https?:/.test(r.url)));
    if (!results.length && /captcha|challenge|verify you are human/i.test(await p.evaluate(() => document.body.innerText)))
      throw new Error('the search site wants a human check');
    return { results: results.slice(0, req.n || 8) };
  } finally { await p.close().catch(() => {}); }
}

// Several pages at once, each in its own tab.
async function readMany(req) {
  const pages = await Promise.all((req.urls || []).slice(0, 6).map(async (url) => {
    const p = await ctx.newPage();
    try { await goto(p, url); return await read(p, req.max || 8000); }
    catch (e) { return { url, error: e.message.split('\n')[0] }; }
    finally { await p.close().catch(() => {}); }
  }));
  return { pages };
}

// A YouTube video's transcript, from the "Show transcript" panel, opened the way you would.
async function transcript(req) {
  const p = await ctx.newPage();
  try {
    await goto(p, req.url);
    const title = (await p.title()).replace(/ - YouTube$/, '');
    await p.locator('#description #expand').first().click({ timeout: 5000 }).catch(() => {});
    const btn = p.locator('ytd-video-description-transcript-section-renderer button:visible').first();
    let segments = [];
    if (await btn.count()) {
      await btn.click({ timeout: 5000 }).catch(() => {});
      await p.waitForSelector('ytd-transcript-segment-renderer', { timeout: 10000 }).catch(() => {});
      segments = await p.$$eval('ytd-transcript-segment-renderer .segment-text', els => els.map(e => e.innerText));
    }
    return { url: req.url, title, text: segments.join(' ').replace(/\s+/g, ' ').trim().slice(0, 60000) };
  } finally { await p.close().catch(() => {}); }
}

async function handle(req) {
  switch (req.op) {
    case 'start': await browser(req.headless); return { headless };
    case 'open': { const p = await current(); await goto(p, req.url); return { url: p.url(), title: await p.title() }; }
    case 'read': {
      const p = await current();
      if (req.url) await goto(p, req.url);
      return read(p, req.max);
    }
    case 'readMany': await browser(); return readMany(req);
    case 'search': await browser(); return search(req);
    case 'transcript': await browser(); return transcript(req);
    case 'snapshot': return snapshot(await current());
    case 'act': return act(await current(), req);
    case 'download': { const p = await current(); return download(p, req); }
    case 'screenshot': { const p = await current(); await p.screenshot({ path: req.path }); return { path: req.path }; }
    case 'tabs': await browser(); return { tabs: await Promise.all(ctx.pages().map(async p => ({ url: p.url(), title: await p.title() }))) };
    case 'close': if (ctx) await ctx.close().catch(() => {}); return {};
    default: throw new Error('unknown op ' + req.op);
  }
}

const rl = readline.createInterface({ input: process.stdin });
let queue = Promise.resolve();
rl.on('line', (line) => {
  queue = queue.then(async () => {
    let req = {};
    try {
      req = JSON.parse(line);
      const out = await handle(req);
      process.stdout.write(JSON.stringify(Object.assign({ seq: req.seq, ok: true }, out)) + '\n');
    } catch (e) {
      process.stdout.write(JSON.stringify({ seq: req.seq, ok: false, error: String(e.message || e).split('\n')[0] }) + '\n');
    }
  });
});
rl.on('close', async () => { await queue; if (ctx) await ctx.close().catch(() => {}); process.exit(0); });
