import { describe, it, expect } from 'vitest';
import { normalizeBrowserUrl, isAllowedNavigation } from './browser-url.js';

const ok = (s: string) => {
  const r = normalizeBrowserUrl(s);
  return r.ok ? r.url : `ERR:${r.reason}`;
};

describe('normalizeBrowserUrl', () => {
  it('adds https to a bare domain', () => expect(ok('example.com')).toBe('https://example.com/'));
  it('uses http for localhost and keeps the port', () => {
    expect(ok('localhost:3000')).toBe('http://localhost:3000/');
    expect(ok('127.0.0.1:8080/x')).toBe('http://127.0.0.1:8080/x');
  });
  it('keeps an explicit http(s) URL', () => expect(ok('http://a.test/p?q=1')).toBe('http://a.test/p?q=1'));
  it('turns plain words into a search', () => expect(ok('react hooks')).toContain('search?q=react%20hooks'));
  it('refuses file:, javascript: and data: pages', () => {
    for (const bad of ['file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,<b>x</b>']) {
      expect(ok(bad)).toMatch(/^ERR:/);
    }
  });
  it('refuses empty input', () => expect(ok('   ')).toMatch(/^ERR:/));
});

describe('isAllowedNavigation', () => {
  it('allows only http and https', () => {
    expect(isAllowedNavigation('https://a.b')).toBe(true);
    expect(isAllowedNavigation('http://a.b')).toBe(true);
    expect(isAllowedNavigation('file:///x')).toBe(false);
    expect(isAllowedNavigation('not a url')).toBe(false);
  });
});
