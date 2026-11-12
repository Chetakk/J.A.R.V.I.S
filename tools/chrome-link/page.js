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

// Fields neither agent ever types into, by type, autocomplete, name or id.
const SECRET = /password|passwd|cc-|card.?number|cvv|cvc|security.?code|otp|one.?time|pin\b/i;

if (typeof module !== 'undefined') module.exports = { pageText, interactive, SECRET };
