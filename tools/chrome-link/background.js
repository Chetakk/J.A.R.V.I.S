// JARVIS Link: answers JARVIS (through the native host tools/chrome-link/host.py) about the tab you're looking at.
// "page": its video and link titles; "open": go to a URL in that tab; "label": number links on screen;
// "fullscreen" / "video": the biggest video; "tabs" / "focus_tab": every tab; "agent": the browser agent's calls
// (read, click, type, ...) in your own Chrome, see agent() below. Nothing is sent anywhere but JARVIS.
importScripts("page.js");  // pageText, interactive, SECRET: what the agents read off a page
const HOST = "com.jarvis.link";
let port = null;

function connect() {
  port = chrome.runtime.connectNative(HOST);  // an open port also keeps this service worker alive
  port.onMessage.addListener(async (msg) => {
    let reply;
    try {
      reply = await handle(msg);
    } catch (e) {
      reply = {error: String((e && e.message) || e)};
    }
    port.postMessage(Object.assign({id: msg.id}, reply));
  });
  port.onDisconnect.addListener(() => {
    port = null;
    setTimeout(connect, 5000);  // the host isn't installed yet, or it restarted
  });
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  return tab;
}

async function run(tab, func) {
  const [r] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func});
  return r && r.result;
}

async function handle(msg) {
  if (msg.op === "reload") {  // picks up a changed background.js without a trip to chrome://extensions
    setTimeout(() => chrome.runtime.reload(), 100);
    return {ok: true};
  }
  if (msg.op === "spotify") {  // the Spotify web app's tab, wherever it is (its own --app window)
    const [t] = await chrome.tabs.query({url: "https://open.spotify.com/*"});
    if (!t) return {error: "no spotify"};
    const [r] = await chrome.scripting.executeScript({target: {tabId: t.id}, func: spotify, args: [msg]});
    return {result: r && r.result};
  }
  if (msg.op === "agent") return agent(msg);
  const tab = await activeTab();
  if (!tab) return {error: "no tab"};
  if (msg.op === "page") {
    let found = {items: [], video: false};
    try {
      found = (await run(tab, readPage)) || found;
    } catch (e) {}  // chrome:// pages and the Web Store can't be read: just the address
    return {url: tab.url || "", title: tab.title || "", items: found.items, video: found.video};
  }
  if (msg.op === "open") {
    if (!/^https?:\/\//.test(msg.url || "")) return {error: "not a web address"};
    await chrome.tabs.update(tab.id, {url: msg.url});
    return {ok: true};
  }
  if (msg.op === "fullscreen") {
    return {result: await withGesture(tab, "(" + fullscreen + ")(" + JSON.stringify(msg.mode || "toggle") + ")")};
  }
  if (msg.op === "video") {
    const [r] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: videoControl, args: [msg]});
    return {result: r && r.result};
  }
  if (msg.op === "label") {
    await chrome.scripting.executeScript({target: {tabId: tab.id}, func: label, args: [msg.urls || []]});
    return {ok: true};
  }
  if (msg.op === "tabs") {
    const tabs = await chrome.tabs.query({});
    return {tabs: tabs.map((t) => ({id: t.id, windowId: t.windowId, title: t.title || "", url: t.url || "",
                                    active: t.active}))};
  }
  if (msg.op === "focus_tab") {
    const t = await chrome.tabs.update(msg.tab, {active: true});  // not msg.id: the host numbers its messages with that
    await chrome.windows.update(t.windowId, {focused: true});
    return {ok: true};
  }
  return {error: "unknown op"};
}

// Full screen needs a user gesture, which scripts don't have; a moment of debugger access gives one (Chrome shows
// its "debugging this browser" bar until it detaches). Keys can't do it: Vimium takes "f".
async function withGesture(tab, expression) {
  if (attached.has(tab.id)) return evaluate(tab.id, expression);  // the agent is working in it: its session
  const target = {tabId: tab.id};
  await chrome.debugger.attach(target, "1.3");
  try {
    const r = await chrome.debugger.sendCommand(target, "Runtime.evaluate",
                                                {expression, userGesture: true, awaitPromise: true, returnByValue: true});
    return r && r.result && r.result.value;
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

// Runs in the page (as text, through the debugger): "on", "off" or "none" (no video to show).
async function fullscreen(mode) {
  if (document.fullscreenElement) {
    if (mode === "on") return "on";
    await document.exitFullscreen();
    return "off";
  }
  if (mode === "off") return "off";
  const yt = document.querySelector(".ytp-fullscreen-button");
  if (yt && yt.offsetParent) {
    yt.click();  // YouTube's own button: its controls stay
    return "on";
  }
  const big = [...document.querySelectorAll("video")].filter((v) => v.offsetWidth > 200)
    .sort((a, b) => b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight)[0];
  if (!big) return "none";
  await big.requestFullscreen();
  return "on";
}

// Runs in the page: skip or speed on the biggest video. "none" when there's no video.
function videoControl(msg) {
  const v = [...document.querySelectorAll("video")].filter((x) => x.offsetWidth > 100)
    .sort((a, b) => b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight)[0];
  if (!v) return "none";
  if (msg.do === "seek") v.currentTime = Math.max(0, v.currentTime + msg.by);
  if (msg.do === "speed") v.playbackRate = msg.to;
  if (msg.do === "speed_step") v.playbackRate = Math.min(4, Math.max(0.25, v.playbackRate + msg.by));
  return {time: v.currentTime, speed: v.playbackRate};
}

// Runs in Spotify's page. msg.do: "state"; "library" (Your Library, in its own order); "play" msg.path (a
// playlist / album / artist page: its big play button; a /search/... page: its first play button); "shuffle" /
// "repeat" msg.on (true, false, or "one" for repeat); "like"; "volume" msg.to (0..1). Each answers with the state.
async function spotify(msg) {
  const q = (s, root) => (root || document).querySelector(s);
  const bar = () => q('[data-testid="now-playing-bar"]') || q("footer") || document;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const wait = async (find, ms) => {
    for (let t = 0; t < (ms || 8000); t += 250) {
      const x = find();
      if (x) return x;
      await sleep(250);
    }
    return null;
  };
  // The heart by its test id, else by its label: "Add to Liked Songs", "Add to playlist" once liked.
  const like = () => {
    const root = q('[data-testid="now-playing-widget"]') || bar();
    return q('[data-testid="add-button"]', root) ||
      [...root.querySelectorAll("button[aria-label]")].find((b) => /liked songs|add to playlist/i.test(b.getAttribute("aria-label")));
  };
  const liked = (l) => l.hasAttribute("aria-checked") ? l.getAttribute("aria-checked") === "true" : /playlist/i.test(l.getAttribute("aria-label"));
  const range = () => q('[data-testid="volume-bar"] input[type="range"]');
  const state = () => {
    const sh = q('[data-testid="control-button-shuffle"]'), rp = q('[data-testid="control-button-repeat"]');
    const v = range(), l = like();
    return {path: location.pathname,
            shuffle: sh ? sh.getAttribute("aria-checked") === "true" : null,
            repeat: rp ? {"true": true, "mixed": "one"}[rp.getAttribute("aria-checked")] || false : null,
            liked: l ? liked(l) : null,
            volume: v ? Number(v.value) / Number(v.max || 1) : null};
  };
  if (msg.do === "library") {
    const seen = new Map();
    const read = () => {
      for (const el of document.querySelectorAll('[id^="listrow-title-spotify:"]')) {
        const uri = el.id.slice("listrow-title-".length);
        const sub = document.getElementById("listrow-subtitle-" + uri);
        const p = uri.split(":");  // spotify:playlist:ID, spotify:user:NAME:collection (Liked Songs)
        const path = p[p.length - 1] === "collection" ? "/collection/tracks" : "/" + p[1] + "/" + p[2];
        if (!seen.has(uri)) seen.set(uri, {name: el.textContent.trim(), sub: sub ? sub.textContent.trim() : "", path});
      }
    };
    read();
    // The list only draws the rows near its scroll position: walk it to the end, then put it back.
    const first = q('[id^="listrow-title-spotify:"]');
    let box = first && first.parentElement;
    while (box && !(box.scrollHeight > box.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
    if (box) {
      const top = box.scrollTop;
      for (let y = 0; y < box.scrollHeight && y < 40000; y += box.clientHeight / 2) {
        box.scrollTop = y;
        await sleep(120);
        read();
      }
      box.scrollTop = top;
    }
    return {items: [...seen.values()]};
  }
  if (msg.do === "play") {
    if (location.pathname !== msg.path) {
      // Buttons on the page we leave mustn't be taken for the new page's.
      document.querySelectorAll('[data-testid="play-button"], button[aria-label^="Play"]').forEach((b) => b.dataset.jarvisOld = "");
      history.pushState({}, "", msg.path);  // in-app navigation: no reload, so the player keeps going
      dispatchEvent(new PopStateEvent("popstate", {state: {}}));
    }
    const fresh = (s) => [...document.querySelectorAll(s)].find((b) => !("jarvisOld" in b.dataset));
    const button = await wait(() => msg.path.startsWith("/search/")
      ? fresh('[data-testid="top-result-card"] [data-testid="play-button"]') ||
        fresh('[data-testid="tracklist-row"] button[aria-label^="Play"]') || fresh('main [data-testid="play-button"]')
      : fresh('[data-testid="action-bar-row"] [data-testid="play-button"]'));
    document.querySelectorAll("[data-jarvis-old]").forEach((b) => delete b.dataset.jarvisOld);
    if (!button) return {error: "no play button", state: state()};
    const title = (q('[data-testid="entityTitle"]') || q("main h1") || {}).textContent || "";
    if (/^Play/.test(button.getAttribute("aria-label") || "Play")) button.click();  // "Pause": it's playing already
    return Object.assign(state(), {played: true, title: title.trim()});
  }
  if (msg.do === "shuffle" || msg.do === "repeat") {
    const b = q('[data-testid="control-button-' + msg.do + '"]');
    if (!b) return {error: "no " + msg.do + " button"};
    for (let i = 0; i < 3 && state()[msg.do] !== msg.on; i++) {  // repeat goes off -> all -> one -> off
      b.click();
      await sleep(300);
    }
  }
  if (msg.do === "like") {
    const l = like();
    if (!l) return {error: "no like button"};
    if (!liked(l)) {  // liked already: a click opens "add to playlist" instead
      l.click();
      await sleep(400);
    }
  }
  if (msg.do === "volume") {
    const v = range();
    if (!v) return {error: "no volume bar"};
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    set.call(v, String(Math.max(0, Math.min(1, msg.to)) * Number(v.max || 1)));  // React only sees the native setter
    v.dispatchEvent(new Event("input", {bubbles: true}));
    v.dispatchEvent(new Event("change", {bubbles: true}));
    await sleep(200);
  }
  return state();
}

// Runs in the page: numbers 1..n on the links JARVIS lists (in that order), gone after a minute or on [].
function label(urls) {
  document.querySelectorAll("[data-jarvis-badge]").forEach((b) => b.remove());
  urls.forEach((url, i) => {
    const a = [...document.querySelectorAll("a[href]")].find((x) => {
      const r = x.getBoundingClientRect();
      return x.href === url && r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight;
    });
    if (!a) return;
    const r = a.getBoundingClientRect();
    const b = document.createElement("div");
    b.dataset.jarvisBadge = "";
    b.textContent = i + 1;
    b.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;font:bold 18px/1 sans-serif;" +
      "color:#fff;background:#000;border:2px solid #fff;border-radius:6px;padding:4px 7px;" +
      "left:" + Math.max(0, r.left) + "px;top:" + Math.max(0, r.top) + "px";
    document.documentElement.appendChild(b);
  });
  if (urls.length) setTimeout(() => document.querySelectorAll("[data-jarvis-badge]").forEach((b) => b.remove()), 60000);
}

// Runs inside the page. Self-contained: nothing from this file is visible there.
function readPage() {
  const VIDEO = /youtube\.com\/(?:watch\?|shorts\/)|youtu\.be\/|vimeo\.com\/\d|dailymotion\.com\/video|\/videos?\/|\/watch\b|\.(?:mp4|webm|mkv|m3u8)\b/i;
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 200);
  const worded = (s) => /[a-zऀ-ॿ]{2}/i.test(s);  // not just "10:24" or an icon
  const byUrl = new Map();
  for (const a of document.querySelectorAll("a[href]")) {
    const url = a.href;
    if (!/^https?:/.test(url) || url.split("#")[0] === location.href.split("#")[0]) continue;
    const img = a.querySelector("img");
    const named = clean(a.getAttribute("title")) || (a.id === "video-title" ? clean(a.textContent) : "");
    const title = named || clean(a.textContent) || clean(a.getAttribute("aria-label")) || clean(img && img.alt);
    if (!worded(title)) continue;
    const b = a.getBoundingClientRect();
    const seen = b.width > 0 && b.height > 0 && b.bottom > 0 && b.top < innerHeight;
    const old = byUrl.get(url);
    if (old && !(named && !old.named)) {
      old.seen = old.seen || seen;
      continue;
    }
    byUrl.set(url, {title, url, video: VIDEO.test(url), seen: seen || !!(old && old.seen), named: !!named});
  }
  const items = [...byUrl.values()].slice(0, 600).map(({named, ...it}) => it);
  const video = [...document.querySelectorAll("video")].some((v) => v.readyState > 0 && v.offsetWidth > 200);
  return {items, video};
}

// --- The browser agent in your own Chrome (system/chrome_link.Agent) ---------------------------------------
// The same calls as tools/browser-agent/agent.js, with your logins. msg.do is the call; msg.tab the tab a task
// works in (none yet: the tab you're on, or a new one for "open"), and every reply names it; msg.el an element's
// number from "snapshot" (not "id": the host numbers its messages with that). Clicks and keys are
// real input through the debugger (Chrome shows its "debugging this browser" bar meanwhile); reading needs no
// debugger. Pages read for "readMany" / "transcript" load muted in a minimized window that closes after.
const attached = new Map();  // tabId -> the timer that lets the tab go after 30 s without a call
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const WEB = /^https?:\/\//;

async function cdp(tabId, method, params) {
  if (!attached.has(tabId)) {
    await chrome.debugger.attach({tabId}, "1.3");
    attached.set(tabId, null);
    await sleep(400);  // the bar it brings shrinks the page: positions are only right after it's in
  }
  clearTimeout(attached.get(tabId));
  attached.set(tabId, setTimeout(() => release(tabId), 30000));
  return chrome.debugger.sendCommand({tabId}, method, params || {});
}

function release(tabId) {
  clearTimeout(attached.get(tabId));
  attached.delete(tabId);
  chrome.debugger.detach({tabId}).catch(() => {});
}

chrome.debugger.onDetach.addListener((src) => {  // the tab closed, or you pressed Cancel on the bar
  clearTimeout(attached.get(src.tabId));
  attached.delete(src.tabId);
});

async function evaluate(tabId, expression) {
  const r = await cdp(tabId, "Runtime.evaluate", {expression, userGesture: true, awaitPromise: true, returnByValue: true});
  if (r.exceptionDetails) {
    const ex = r.exceptionDetails.exception;
    throw new Error(((ex && ex.description) || r.exceptionDetails.text || "the page failed").split("\n")[0].replace(/^Error: /, ""));
  }
  return r.result && r.result.value;
}

async function inPage(tabId, func, args) {
  const [r] = await chrome.scripting.executeScript({target: {tabId}, func, args: args || []});
  return r && r.result;
}

// Until the tab has loaded (or `ms` went by), then a moment for its scripts.
async function settle(tabId, ms) {
  await sleep(300);
  for (let t = 0; t < (ms || 15000); t += 200) {
    if ((await chrome.tabs.get(tabId)).status === "complete") break;
    await sleep(200);
  }
  await sleep(500);
}

async function read(tabId, max) {
  const t = await chrome.tabs.get(tabId);
  return {url: t.url || "", title: t.title || "", text: await inPage(tabId, pageText, [max || 20000])};
}

async function snapshot(tabId) {
  const t = await chrome.tabs.get(tabId);
  let elements = [], text = "";
  try {
    elements = await inPage(tabId, interactive);
    text = await inPage(tabId, pageText, [3000]);
  } catch (e) {}  // a new tab, chrome:// page or the Web Store: nothing to read, "goto" somewhere first
  return {url: t.url || "", title: t.title || "", text, elements};
}

const pick = (id) => "document.querySelector('[data-jarvis=\"" + Number(id) + "\"]')";

// The middle of element `id`, scrolled into view: where a click goes.
async function point(tabId, id) {
  const p = await evaluate(tabId, "(() => { const e = " + pick(id) + "; if (!e) return null;" +
    " e.scrollIntoView({block: 'center', inline: 'center'}); const r = e.getBoundingClientRect();" +
    " return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()");
  if (!p) throw new Error("that element is gone from the page");
  return p;
}

async function click(tabId, id) {
  const {x, y} = await point(tabId, id);
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"])
    await cdp(tabId, "Input.dispatchMouseEvent", {type, x, y, button: type === "mouseMoved" ? "none" : "left", clickCount: 1});
}

const KEYS = {Enter: [13, "\r"], Tab: [9], Escape: [27], Backspace: [8], Delete: [46], Space: [32, " "], ArrowUp: [38],
              ArrowDown: [40], ArrowLeft: [37], ArrowRight: [39], PageUp: [33], PageDown: [34], Home: [36], End: [35]};
const MODS = {Alt: 1, Control: 2, Ctrl: 2, Meta: 4, Shift: 8};

// A key as Playwright names it ("Enter", "a", "Control+a", "Shift+Tab"), pressed and let go.
async function press(tabId, combo) {
  const parts = String(combo || "Enter").split("+");
  const name = parts.pop() || "+";
  const modifiers = parts.reduce((m, p) => m | (MODS[p] || 0), 0);
  let [vk, text] = KEYS[name] || [], code = name, key = name === "Space" ? " " : name;
  if (vk === undefined && name.length === 1) {
    [vk, text] = [name.toUpperCase().charCodeAt(0), name];
    code = /[a-z]/i.test(name) ? "Key" + name.toUpperCase() : /\d/.test(name) ? "Digit" + name : "";
  }
  if (vk === undefined) throw new Error("I don't know the key " + name);
  if (modifiers & ~MODS.Shift) text = undefined;  // a shortcut types nothing
  const ev = {key, code, windowsVirtualKeyCode: vk, modifiers};
  await cdp(tabId, "Input.dispatchKeyEvent", Object.assign({type: text ? "keyDown" : "rawKeyDown", text, unmodifiedText: text}, ev));
  await cdp(tabId, "Input.dispatchKeyEvent", Object.assign({type: "keyUp"}, ev));
}

async function act(tabId, msg) {
  const {action, el: id, value} = msg;
  if (action === "goto") {
    if (!WEB.test(value || "")) throw new Error("not a web address");
    await chrome.tabs.update(tabId, {url: value});
  } else if (action === "back") {
    await chrome.tabs.goBack(tabId).catch(() => {});
  } else if (action === "scroll") {
    const mid = await evaluate(tabId, "({x: innerWidth / 2, y: innerHeight / 2})");
    await cdp(tabId, "Input.dispatchMouseEvent", Object.assign({type: "mouseWheel", deltaX: 0, deltaY: value === "up" ? -700 : 700}, mid));
  } else {
    const info = await evaluate(tabId, "(() => { const e = " + pick(id) + "; return e && {type: e.type || '', " +
      "autocomplete: e.autocomplete || '', name: e.name || '', id: e.id || '', checked: !!e.checked}; })()");
    if (!info) throw new Error("that element is gone from the page");
    if (action === "fill") {
      if (info.type === "password" || info.type === "file" || SECRET.test(info.autocomplete + " " + info.name + " " + info.id))
        throw new Error("I never type into password, card or code fields");
      // The cursor in it and what's there selected, so the text typed replaces it (as real typing: React and the
      // like see it). Typing goes wherever the cursor is, so it must be in this box: else a click, else nothing.
      const ready = "(() => { const e = " + pick(id) + "; if (document.activeElement !== e) e.focus();" +
        " if (document.activeElement !== e && !e.contains(document.activeElement)) return false;" +
        " if (e.isContentEditable) { const r = document.createRange(); r.selectNodeContents(e);" +
        " getSelection().removeAllRanges(); getSelection().addRange(r); } else if (e.select) e.select(); return true; })()";
      await point(tabId, id);
      if (!(await evaluate(tabId, ready))) {
        await click(tabId, id);
        if (!(await evaluate(tabId, ready))) throw new Error("I couldn't put the cursor in that box");
      }
      if (String(value || "")) await cdp(tabId, "Input.insertText", {text: String(value)});
      else await press(tabId, "Delete");
    } else if (action === "select") {
      await evaluate(tabId, "((v) => { const e = " + pick(id) + "; const os = [...e.options];" +
        " const o = os.find((o) => o.text.trim() === v || o.value === v) || os.find((o) => o.text.toLowerCase().includes(v.toLowerCase()));" +
        " if (!o) throw new Error('there is no option ' + v); e.value = o.value;" +
        " e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); })(" +
        JSON.stringify(String(value)) + ")");
    } else if (action === "check") {
      if (info.checked !== (value !== false && value !== "false")) await click(tabId, id);
    } else if (action === "press") {
      await evaluate(tabId, "(() => { const e = " + pick(id) + "; e && e.focus(); })()");
      await press(tabId, value);
    } else if (action === "click") {
      await click(tabId, id);
    } else if (action === "upload") {
      if (info.type !== "file") throw new Error("that isn't a file box");
      const r = await cdp(tabId, "Runtime.evaluate", {expression: pick(id)});
      await cdp(tabId, "DOM.enable");
      await cdp(tabId, "DOM.setFileInputFiles", {files: [String(value)], objectId: r.result.objectId});
    } else {
      throw new Error("unknown action " + action);
    }
  }
  await settle(tabId, 8000);
  return snapshot(tabId);
}

// Saved to your Downloads by Chrome itself: {path} once it's finished.
async function finished(id) {
  for (let t = 0; t < 300000; t += 500) {
    const [d] = await chrome.downloads.search({id});
    if (d && d.state === "complete") return {path: d.filename};
    if (!d || d.state === "interrupted") throw new Error("the download failed" + (d && d.error ? " (" + d.error + ")" : ""));
    await sleep(500);
  }
  throw new Error("the download is still going");
}

async function download(tabId, msg) {
  if (msg.url) {
    if (!WEB.test(msg.url)) throw new Error("not a web address");
    return finished(await chrome.downloads.download({url: msg.url, conflictAction: "uniquify"}));
  }
  const href = await evaluate(tabId, "(() => { const e = " + pick(msg.el) + "; return e && e.href; })()");
  if (WEB.test(href || "")) return finished(await chrome.downloads.download({url: href, conflictAction: "uniquify"}));
  let stop;  // a button that makes the file: the download it starts
  const started = new Promise((resolve) => {
    const seen = (d) => resolve(d.id);
    chrome.downloads.onCreated.addListener(seen);
    const timer = setTimeout(() => resolve(null), 30000);
    stop = () => { clearTimeout(timer); chrome.downloads.onCreated.removeListener(seen); };
  });
  try {
    await click(tabId, msg.el);
    const id = await started;
    if (id === null) throw new Error("clicking it didn't download anything");
    return finished(id);
  } finally {
    stop();
  }
}

// Console messages and uncaught errors so far (Chrome keeps them for when DevTools opens), and failed loads.
async function consoleLog(tabId) {
  const out = [];
  const text = (a) => a.value !== undefined ? String(a.value) : a.description || a.type;
  const listen = (src, method, p) => {
    if (src.tabId !== tabId) return;
    if (method === "Runtime.consoleAPICalled") {
      const at = p.stackTrace && p.stackTrace.callFrames[0];
      out.push({level: p.type, text: p.args.map(text).join(" ").slice(0, 500), at: at ? at.url + ":" + (at.lineNumber + 1) : ""});
    } else if (method === "Runtime.exceptionThrown") {
      const d = p.exceptionDetails;
      out.push({level: "error", text: ((d.exception && d.exception.description) || d.text).slice(0, 500), at: d.url || ""});
    } else if (method === "Log.entryAdded") {
      out.push({level: p.entry.level, text: p.entry.text.slice(0, 500), at: p.entry.url || ""});
    }
  };
  chrome.debugger.onEvent.addListener(listen);
  try {
    await cdp(tabId, "Runtime.enable");
    await cdp(tabId, "Log.enable");
    await sleep(500);
  } finally {
    chrome.debugger.onEvent.removeListener(listen);
    await cdp(tabId, "Runtime.disable").catch(() => {});
    await cdp(tabId, "Log.disable").catch(() => {});
  }
  return {entries: out.slice(-200)};
}

// Runs in the page: everything it has loaded since it opened (the browser's own timing record).
function requests() {
  return performance.getEntriesByType("navigation").concat(performance.getEntriesByType("resource")).map((e) => ({
    url: e.name.slice(0, 300), type: e.initiatorType || "document", status: e.responseStatus || 0,
    ms: Math.round(e.duration), kb: Math.round((e.transferSize || 0) / 1024)})).slice(-300);
}

// Runs in a YouTube page: the transcript, from the "Show transcript" panel, opened the way you would.
async function ytTranscript() {
  const wait = async (find, ms) => {
    for (let t = 0; t < ms; t += 250) {
      const x = find();
      if (x) return x;
      await new Promise((r) => setTimeout(r, 250));
    }
    return null;
  };
  const title = document.title.replace(/ - YouTube$/, "");
  const more = await wait(() => document.querySelector("#description #expand"), 8000);
  if (more) more.click();
  const btn = await wait(() => document.querySelector("ytd-video-description-transcript-section-renderer button"), 5000);
  if (!btn) return {title, text: ""};
  btn.click();
  await wait(() => document.querySelector("ytd-transcript-segment-renderer, transcript-segment-view-model"), 10000);
  const text = [...document.querySelectorAll("ytd-transcript-segment-renderer .segment-text, " +  // older panel, newer (2026)
                                             "transcript-segment-view-model [role=text]")].map((e) => e.innerText);
  return {title, text: text.join(" ").replace(/\s+/g, " ").trim().slice(0, 60000)};
}

// Pages loaded out of your way (a minimized window, muted), each given to `use`, then the window closed.
async function offscreen(urls, use) {
  urls = urls.filter((u) => WEB.test(u)).slice(0, 6);
  if (!urls.length) throw new Error("not a web address");
  const win = await chrome.windows.create({url: urls, state: "minimized", focused: false});
  try {
    const tabs = await chrome.tabs.query({windowId: win.id});
    return await Promise.all(tabs.map(async (t, i) => {
      try {
        await chrome.tabs.update(t.id, {muted: true});
        await settle(t.id, 30000);
        return await use(t.id);
      } catch (e) {
        return {url: urls[i], error: String((e && e.message) || e)};
      }
    }));
  } finally {
    chrome.windows.remove(win.id).catch(() => {});
  }
}

async function agent(msg) {
  const d = msg.do;
  if (d === "readMany") return {pages: await offscreen(msg.urls || [], (id) => read(id, msg.max || 8000))};
  if (d === "transcript") {
    const [r] = await offscreen([msg.url], (id) => inPage(id, ytTranscript));
    if (r.error) throw new Error(r.error);
    return Object.assign({url: msg.url}, r);
  }
  if (d === "download" && msg.url) return download(null, msg);
  if (d === "open" && !msg.tab) {  // a task's own tab: shown in Chrome, but Chrome isn't raised (it'd take your typing)
    if (msg.url && !WEB.test(msg.url)) throw new Error("not a web address");
    const t = await chrome.tabs.create(msg.url ? {url: msg.url, active: true} : {active: true});
    if (msg.url) await settle(t.id);
    const now = await chrome.tabs.get(t.id);
    return {tab: t.id, url: now.url || now.pendingUrl || "", title: now.title || ""};
  }
  const tab = msg.tab ? await chrome.tabs.get(msg.tab).catch(() => null) : await activeTab();
  if (!tab) throw new Error(msg.tab ? "its tab was closed" : "no tab");
  if (d === "read" && msg.url && !msg.tab && tab.url !== msg.url) {  // not the page you're on: read out of your way
    const [p] = await offscreen([msg.url], (id) => read(id, msg.max));
    if (p.error) throw new Error(p.error);
    return p;
  }
  const id = tab.id, out = {tab: id};
  if (d === "open") {
    if (!WEB.test(msg.url || "")) throw new Error("not a web address");
    await chrome.tabs.update(id, {url: msg.url, active: true});
    await settle(id);
    const now = await chrome.tabs.get(id);
    return Object.assign(out, {url: now.url || "", title: now.title || ""});
  }
  if (d === "read") return Object.assign(out, await read(id, msg.max));
  if (d === "snapshot") return Object.assign(out, await snapshot(id));
  if (d === "act") return Object.assign(out, await act(id, msg));
  if (d === "download") return Object.assign(out, await download(id, msg));
  if (d === "screenshot") return Object.assign(out, {png: (await cdp(id, "Page.captureScreenshot", {format: "png"})).data});
  if (d === "console") return Object.assign(out, await consoleLog(id));
  if (d === "network") return Object.assign(out, {url: tab.url || "", requests: await inPage(id, requests)});
  if (d === "release") {
    release(id);
    return out;
  }
  throw new Error("unknown agent call " + d);
}

chrome.runtime.onStartup.addListener(() => {});  // wakes this worker when Chrome starts, so it connects
connect();
