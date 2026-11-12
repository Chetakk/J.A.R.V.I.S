// What the browser agents read off a page, shared by JARVIS's own Chrome (tools/browser-agent/agent.js, through
// Playwright) and yours (background.js, through JARVIS Link). Each function runs inside the page, so it must be
// self-contained.

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
  return parts.join('').replace(/[ \t ]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim().slice(0, max);
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

// The page as an article for a reader card: {title, byline, site, image (its main picture's URL), text (markdown:
// headings, paragraphs, lists, quotes, code, small tables)}, as plain reading text: no links (underlines everywhere,
// and URLs with brackets broke them), menus, ads, side boxes, pronunciations, or the back matter (references, see also).
function article(max) {
  const meta = (k) => { const m = document.querySelector('meta[property="' + k + '"], meta[name="' + k + '"]'); return m ? (m.content || '').trim() : ''; };
  // the longest <article> (a page of teasers has many; GitHub's README is one inside <main>), else <main>, else all
  const arts = [...document.querySelectorAll('article')].sort((x, y) => y.innerText.length - x.innerText.length);
  const root = (arts[0] && arts[0].innerText.length > 500 && arts[0]) || document.querySelector('main, [role=main]') || document.body;
  const skip = 'nav, header, footer, aside, script, style, noscript, svg, form, button, iframe, sup, [aria-hidden=true], ' +
    '[role=navigation], [role=complementary], [class*=share], [class*=related], [class*=newsletter], [class*=advert], [id*=comments], ' +
    '[class*=infobox], [class*=navbox], [class*=sidebar], [role=presentation], [class*=IPA], [class*=pronunciation], ' +
    '.noprint, .mw-editsection, .headerlink, a.anchor, [class*=anchor-link]';
  const END = /^(see also|references|notes( and references)?|external links|further reading|citations|sources|bibliography|footnotes|related (articles|stories|posts)|more from|comments|read more|recommended)$/i;
  // one line of text; what a skipped bit leaves behind ("( Just", "word ,") closed up
  const clean = (s) => s.replace(/[ \t\n ]+/g, ' ').replace(/\(\s*[;,]?\s*/g, '(').replace(/\s+([,.;:)])/g, '$1').replace(/\(\)/g, '').trim();
  const inline = (el) => {  // a list inside comes as its own lines
    let s = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) s += n.textContent;
      else if (n.nodeType === 1 && !n.matches(skip) && !/^(UL|OL)$/.test(n.tagName) && getComputedStyle(n).display !== 'none') s += inline(n);
    }
    return s;
  };
  const out = [];
  let size = 0, firstImg = '', done = false;
  const walk = (el, depth) => {
    for (const n of el.children) {
      if (size > max || done) return;
      if (n.matches(skip)) continue;
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden') continue;
      const tag = n.tagName;
      let line = null;
      if (/^H[1-6]$/.test(tag)) {
        if (END.test(clean(inline(n)))) { done = true; return; }
        line = '#'.repeat(Math.min(3, Number(tag[1]))) + ' ' + clean(inline(n));
      }
      else if (tag === 'P') line = clean(inline(n));
      else if (tag === 'LI') line = '  '.repeat(depth) + '- ' + clean(inline(n));
      else if (tag === 'BLOCKQUOTE') line = '> ' + clean(inline(n));
      else if (tag === 'PRE') {  // long lines broken: a card is narrow, and code doesn't wrap
        const code = n.innerText.replace(/\n+$/, '').split('\n').slice(0, 60);
        line = '```\n' + code.map((l) => l.match(/.{1,47}(?=\s|$)|.{1,48}/g) || ['']).flat().join('\n') + '\n```';
      }
      else if (tag === 'TABLE') {  // a card is narrow: tables up to 4 columns, the rest left out
        const rows = [...n.rows].slice(0, 30).map((r) => [...r.cells].map((c) => clean(c.innerText).replace(/\|/g, '/').slice(0, 28)));
        line = rows.length && Math.max(...rows.map((r) => r.length)) <= 4 ? rows.map((r) => '| ' + r.join(' | ') + ' |').join('\n') : '';
      }
      else if (tag === 'IMG' && !firstImg && n.naturalWidth >= 300) firstImg = n.currentSrc || n.src;
      if (line !== null) {
        if (line.replace(/^[#>\- ]+/, '')) { out.push(line); size += line.length; }
        if (tag !== 'LI') continue;
      }
      walk(n, depth + (tag === 'UL' || tag === 'OL' ? 1 : 0));
    }
  };
  walk(root, -1);
  const text = out.join('\n\n').replace(/\n\n(  )*- /g, (m) => m.slice(1)).slice(0, max);
  const site = meta('og:site_name') || location.hostname.replace(/^www\./, '');
  // the title without the site's name around it ("X - Wikipedia", "GitHub - X")
  const names = [site.toLowerCase(), location.hostname.split('.').slice(-2)[0]];
  const parts = (meta('og:title') || document.title).split(/ [-|–—] /);
  const named = (part) => part.split(' ').length <= 4 && names.some((nm) => part.toLowerCase().includes(nm));
  if (parts.length > 1 && named(parts[parts.length - 1])) parts.pop();
  else if (parts.length > 1 && named(parts[0])) parts.shift();
  const title = parts.join(' - ');
  // a share picture that's only the title drawn on a card (docs, GitHub) is no picture: the article's own instead
  const og = meta('og:image');
  const image = og && !/social|opengraph|og[-_]?image|share[-_]?image|preview/i.test(og) ? og : firstImg;
  return { url: location.href, title, byline: meta('author'), site, image, text };
}

// Fields neither agent ever types into, by type, autocomplete, name or id.
const SECRET = /password|passwd|cc-|card.?number|cvv|cvc|security.?code|otp|one.?time|pin\b/i;

if (typeof module !== 'undefined') module.exports = { pageText, interactive, article, SECRET };
