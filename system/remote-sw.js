// JARVIS phone app: a service worker is what lets Android install the page as an app. Nothing is cached: every
// request goes to JARVIS, so the app is always live (and says offline when the PC can't be reached).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
