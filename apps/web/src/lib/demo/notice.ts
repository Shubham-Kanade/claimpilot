/**
 * Two tiny browser-side stores for the public demo, readable with `useSyncExternalStore` (server
 * snapshots are safe defaults, so the static shell never depends on the browser):
 *
 *  - the "demo notice" banner is dismissible per session (sessionStorage), and
 *  - the "Started over" confirmation survives the navigation to the upload screen (memory only).
 */
const BANNER_KEY = "claimpilot.demo-banner-dismissed";

const bannerListeners = new Set<() => void>();

export function isBannerDismissed(): boolean {
  try {
    return window.sessionStorage.getItem(BANNER_KEY) === "1";
  } catch {
    return false;
  }
}

/** On the server the banner is treated as dismissed: it is only ever drawn in the browser. */
export function isBannerDismissedOnServer(): boolean {
  return true;
}

export function dismissBanner(): void {
  try {
    window.sessionStorage.setItem(BANNER_KEY, "1");
  } catch {
    // storage blocked: the banner just comes back on the next page load
  }
  for (const listener of bannerListeners) listener();
}

export function subscribeBanner(onChange: () => void): () => void {
  bannerListeners.add(onChange);
  return () => {
    bannerListeners.delete(onChange);
  };
}

let startedOverNotice: string | null = null;
const noticeListeners = new Set<() => void>();

export function setStartedOverNotice(text: string | null): void {
  startedOverNotice = text;
  for (const listener of noticeListeners) listener();
}

export function getStartedOverNotice(): string | null {
  return startedOverNotice;
}

export function getServerNotice(): string | null {
  return null;
}

export function subscribeNotice(onChange: () => void): () => void {
  noticeListeners.add(onChange);
  return () => {
    noticeListeners.delete(onChange);
  };
}
