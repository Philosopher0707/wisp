import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { ArrowLeft, ArrowRight, RefreshCw, X, Globe } from '../../icons/index.js';
import type { BrowserState } from '../../../preload/index.js';

const EMPTY: BrowserState = { url: '', title: '', loading: false, canGoBack: false, canGoForward: false, error: null };

export const BrowserTab: React.FC = () => {
  const { state } = useAppState();
  const [nav, setNav] = useState<BrowserState>(EMPTY);
  const [address, setAddress] = useState('');
  const [editing, setEditing] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const browser = window.wisp?.browser;

  // A native view paints above all web content, so it must step aside whenever a modal or approval is showing.
  const covered = state.uiOverlay !== null || state.approvalPending !== null || state.pendingPlan !== null || state.checkpointPanelOpen;

  useEffect(() => {
    if (!browser) return undefined;
    void browser.getState().then((s) => s && setNav(s));
    return browser.onState(setNav);
  }, [browser]);

  useEffect(() => {
    if (!editing) setAddress(nav.url);
  }, [nav.url, editing]);

  useLayoutEffect(() => {
    const el = stage.current;
    if (!browser || !el) return undefined;
    const send = () => {
      const r = el.getBoundingClientRect();
      browser.setBounds({ x: r.x, y: r.y, width: r.width, height: r.height });
    };
    send();
    const observer = new ResizeObserver(send);
    observer.observe(el);
    window.addEventListener('resize', send);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', send);
    };
  }, [browser]);

  useEffect(() => {
    if (!browser) return undefined;
    browser.setVisible(!covered && nav.url !== '' && !nav.error);
    return () => browser.setVisible(false);
  }, [browser, covered, nav.url, nav.error]);

  if (!browser) {
    return <div className="dock-pane"><p className="dock-note">The browser needs the desktop app; it is not available in a plain web page.</p></div>;
  }

  const go = (e: React.FormEvent) => {
    e.preventDefault();
    void browser.navigate(address);
    (document.activeElement as HTMLElement | null)?.blur();
  };

  return (
    <div className="dock-pane">
      <form className="browser-bar" onSubmit={go}>
        <button type="button" className="dock-tool-btn" onClick={() => browser.action('back')} disabled={!nav.canGoBack} title="Back" aria-label="Back"><ArrowLeft size={15} /></button>
        <button type="button" className="dock-tool-btn" onClick={() => browser.action('forward')} disabled={!nav.canGoForward} title="Forward" aria-label="Forward"><ArrowRight size={15} /></button>
        <button type="button" className="dock-tool-btn" onClick={() => browser.action(nav.loading ? 'stop' : 'reload')} disabled={!nav.url} title={nav.loading ? 'Stop' : 'Reload'} aria-label={nav.loading ? 'Stop' : 'Reload'}>
          {nav.loading ? <X size={15} /> : <RefreshCw size={15} />}
        </button>
        <input
          className="browser-address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onFocus={(e) => { setEditing(true); e.currentTarget.select(); }}
          onBlur={() => setEditing(false)}
          onKeyDown={(e) => e.stopPropagation()}
          placeholder="Search or enter a web address"
          spellCheck={false}
          aria-label="Address"
        />
      </form>
      <div ref={stage} className="browser-stage">
        {(nav.url === '' || nav.error) && (
          <div className="browser-empty">
            <Globe size={28} />
            <p className={nav.error ? 'dock-note--error' : undefined}>
              {nav.error ?? 'Open docs, a local dev server (localhost:3000) or any page next to your session.'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
