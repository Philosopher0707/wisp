/** What the in-app browser is willing to load. Pure, so the policy is tested without Electron. */

export type UrlDecision = { ok: true; url: string } | { ok: false; reason: string };

const LOCAL_HOST = /^(localhost|127\.0\.0\.1|\[::1\]|[\w-]+\.localhost)(:\d+)?(\/|$)/i;

export function normalizeBrowserUrl(input: string): UrlDecision {
  const text = input.trim();
  if (!text) return { ok: false, reason: 'Enter an address.' };

  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(text) && !/^[\w.-]+:\d+(\/|$)/.test(text);
  let candidate = text;
  if (!hasScheme) {
    if (/\s/.test(text) || !/[.:]|^localhost/i.test(text)) {
      return { ok: true, url: `https://www.google.com/search?q=${encodeURIComponent(text)}` };
    }
    candidate = `${LOCAL_HOST.test(text) ? 'http' : 'https'}://${text}`;
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return { ok: false, reason: 'That is not a valid address.' };
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { ok: false, reason: `Only http and https pages can be opened here (not ${parsed.protocol.replace(':', '')}).` };
  }
  return { ok: true, url: parsed.toString() };
}

export function isAllowedNavigation(url: string): boolean {
  try {
    const p = new URL(url).protocol;
    return p === 'http:' || p === 'https:';
  } catch {
    return false;
  }
}
