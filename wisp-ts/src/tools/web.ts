import { stripAnsi } from "../terminal_width.js";

const _USER_AGENT = "Wisp-Agent/0.1.0 (Web Fetch Tool; Respects robots.txt)";
const _ROBOTS_TTL = 3600_000;
const _ROBOTS_FETCH_TIMEOUT = 10_000;
const _robotsCache = new Map<string, { allowed: boolean; ts: number }>();

function _parseRobotsTxt(robotsText: string, userAgent: string, targetPath: string): boolean {
  const uaLower = userAgent.toLowerCase().trim() || "*";
  if (!targetPath.startsWith("/")) targetPath = "/" + targetPath;
  let currentGroupApplies = false;
  let result = true;
  for (const raw of robotsText.split("\n")) {
    const line = raw.split("#", 1)[0].trim();
    if (!line) continue;
    const directive = line.toLowerCase();
    if (directive.startsWith("user-agent:")) {
      const uaMatch = line.split(":", 1)[1]?.trim().toLowerCase() ?? "";
      currentGroupApplies = uaMatch === "*" || uaLower.includes(uaMatch);
      continue;
    }
    if (!currentGroupApplies) continue;
    if (directive.startsWith("disallow:")) {
      const p = line.split(":", 1)[1]?.trim() ?? "";
      if (!p) { result = true; continue; }
      if (targetPath.startsWith(p)) result = false;
    } else if (directive.startsWith("allow:")) {
      const p = line.split(":", 1)[1]?.trim() ?? "";
      if (targetPath.startsWith(p)) result = true;
    }
  }
  return result;
}

async function _checkRobotsTxt(targetUrl: string): Promise<boolean> {
  const parsed = new URL(targetUrl);
  const robotsUrl = `${parsed.protocol}//${parsed.host}/robots.txt`;
  const cached = _robotsCache.get(robotsUrl);
  const now = Date.now();
  if (cached && now - cached.ts < _ROBOTS_TTL) return cached.allowed;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), _ROBOTS_FETCH_TIMEOUT);
    const resp = await fetch(robotsUrl, { headers: { "User-Agent": _USER_AGENT }, signal: ctrl.signal });
    clearTimeout(timer);
    if (resp.status === 404) {
      _robotsCache.set(robotsUrl, { allowed: true, ts: now });
      return true;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const text = await resp.text();
    const allowed = _parseRobotsTxt(text, _USER_AGENT, parsed.pathname || "/");
    _robotsCache.set(robotsUrl, { allowed, ts: now });
    return allowed;
  } catch {
    _robotsCache.set(robotsUrl, { allowed: true, ts: now });
    return true;
  }
}

/** Extract readable text from HTML */
function _extractTextFromHtml(html: string): string {
  const noScript = html
    .replace(/\u003cscript\b[^\u003c]*\u003e[\s\S]*?\u003c\/script\u003e/gi, " ")
    .replace(/\u003cstyle\b[^\u003c]*\u003e[\s\S]*?\u003c\/style\u003e/gi, " ");
  const text = noScript
    .replace(/\u0026lt;/g, "\u003c")
    .replace(/\u0026gt;/g, "\u003e")
    .replace(/\u0026amp;/g, "\u0026")
    .replace(/\u003c[^\u003e]+\u003e/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  return lines.join("\n");
}

export async function toolWebFetch(url: string, maxChars = 10_000): Promise<string> {
  const trimmed = url.trim();
  if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
    throw new Error(`Unsupported URL scheme: ${trimmed}`);
  }
  if (maxChars < 100 || maxChars > 100_000) maxChars = 10_000;

  const allowed = await _checkRobotsTxt(trimmed);
  if (!allowed) {
    const u = new URL(trimmed);
    throw new Error(`[WEB_FETCH_BLOCKED] ${u.host} disallows fetching via robots.txt`);
  }

  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 30_000);
    const resp = await fetch(trimmed, { headers: { "User-Agent": _USER_AGENT }, signal: ctrl.signal });
    clearTimeout(timer);
    if (!resp.ok) {
      throw new Error(`[WEB_FETCH_FAILED] HTTP ${resp.status}: ${trimmed}`);
    }
    const ct = resp.headers.get("content-type")?.toLowerCase() ?? "";
    let text: string;
    if (ct.includes("text/html")) {
      const raw = await resp.text();
      text = _extractTextFromHtml(raw);
      if (text.length === 0) {
        text = raw.slice(0, maxChars);
        if (raw.length > maxChars) text += "\n[Warning: HTML parsing failed, raw content shown.]";
      }
    } else {
      text = await resp.text();
    }
    const safe = stripAnsi(text);
    if (safe.length > maxChars) return `Fetched ${trimmed}\n\n${safe.slice(0, maxChars)}\n... [truncated: ${safe.length} total chars]`;
    return `Fetched ${trimmed}\n\n${safe}`;
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(`[WEB_FETCH_FAILED] Timeout after 30s: ${trimmed}`);
    }
    if (err instanceof Error && (err.message.includes("fetch failed") || err.message.includes("ENOTFOUND"))) {
      throw new Error(`[WEB_FETCH_FAILED] DNS/connection error: ${trimmed}`);
    }
    throw err;
  }
}

export async function toolWebSearch(query: string, numResults = 5): Promise<string> {
  const q = encodeURIComponent(query.trim());
  const url = `https://html.duckduckgo.com/html/?q=${q}`;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15_000);
    const resp = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0 (compatible; Wisp/0.1)" }, signal: ctrl.signal });
    clearTimeout(timer);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const html = await resp.text();
    const results: Array<{ title: string; href: string; snippet: string }> = [];
    const resultBlocks = html.split('\u003cdiv class="result ').slice(1);
    for (const block of resultBlocks) {
      const titleMatch = block.match(/class="result__a"[^>]*>([^<]*)<\/a>/);
      const snippetMatch = block.match(/class="result__snippet"[^>]*>([^<]*)<\/a>/);
      const hrefMatch = block.match(/class="result__a"[^>]*href="([^"]*)"/);
      const title = titleMatch?.[1]?.trim() ?? "";
      const snippet = snippetMatch?.[1]?.trim() ?? "";
      let href = hrefMatch?.[1]?.trim() ?? "";
      if (href.includes("/l/") && href.includes("uddg=")) {
        try {
          const m = href.match(/uddg=([^&]+)/);
          if (m) href = decodeURIComponent(m[1]);
        } catch { /* ignore */ }
      }
      if (title && snippet) {
        results.push({ title, href: href || "", snippet });
      }
      if (results.length >= numResults) break;
    }

    const formatted = results.map((r, i) => ({
      number: i + 1,
      title: r.title,
      url: r.href,
      snippet: r.snippet,
    }));

    return JSON.stringify({ status: "ok", data: { query, results: formatted }, metadata: { num_results: formatted.length, backend: "html" } }, null, 2);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return JSON.stringify({ status: "error", data: { query, results: [] }, metadata: { error: msg } });
  }
}
