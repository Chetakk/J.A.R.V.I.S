// JARVIS Link: answers JARVIS (through the native host tools/chrome-link/host.py) about the tab you're looking at.
// "page": its video and link titles; "open": go to a URL in that tab; "label": number links on screen;
// "fullscreen" / "video": the biggest video; "tabs" / "focus_tab": every tab. Nothing is sent anywhere but JARVIS.
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
    const t = await chrome.tabs.update(msg.id, {active: true});
    await chrome.windows.update(t.windowId, {focused: true});
    return {ok: true};
  }
  return {error: "unknown op"};
}

// Full screen needs a user gesture, which scripts don't have; a moment of debugger access gives one (Chrome shows
// its "debugging this browser" bar until it detaches). Keys can't do it: Vimium takes "f".
async function withGesture(tab, expression) {
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
  const like = () => q('[data-testid="add-button"]', q('[data-testid="now-playing-widget"]') || bar());
  const range = () => q('[data-testid="volume-bar"] input[type="range"]');
  const state = () => {
    const sh = q('[data-testid="control-button-shuffle"]'), rp = q('[data-testid="control-button-repeat"]');
    const v = range(), l = like();
    return {path: location.pathname,
            shuffle: sh ? sh.getAttribute("aria-checked") === "true" : null,
            repeat: rp ? {"true": true, "mixed": "one"}[rp.getAttribute("aria-checked")] || false : null,
            liked: l ? l.getAttribute("aria-checked") === "true" : null,
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
    if (l.getAttribute("aria-checked") !== "true") {  // liked already: a click opens "add to playlist" instead
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

chrome.runtime.onStartup.addListener(() => {});  // wakes this worker when Chrome starts, so it connects
connect();
