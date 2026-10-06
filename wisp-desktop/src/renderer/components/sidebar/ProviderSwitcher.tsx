import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../state/context.js';
import { useApi, type ProviderCatalog } from '../../hooks/useApi.js';
import { useClickOutside } from '../../hooks/useClickOutside.js';
import { ChevronUp, Check, Cpu } from '../../icons/index.js';
import './ProviderSwitcher.css';

const MAX_SHOWN = 60;

export const ProviderSwitcher: React.FC = () => {
  const { state, dispatch } = useAppState();
  const api = useApi(state.serverUrl, state.apiKey);
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [open, setOpen] = useState(false);
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [anchor, setAnchor] = useState<{ left: number; bottom: number }>({ left: 8, bottom: 80 });
  const close = useCallback(() => setOpen(false), []);
  useClickOutside(root, close, open);

  const load = useCallback(async () => {
    try {
      const next = await api.fetchProviders();
      setCatalog(next);
      setError(null);
      if (next.active.model) dispatch({ type: 'SET_MODEL', model: next.active.model });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load providers.');
    }
  }, [api, dispatch]);

  useEffect(() => {
    if (state.connection === 'connected') void load();
  }, [state.connection, load]);

  const active = catalog?.active;
  const shown = browsing ?? active?.provider ?? null;
  const provider = catalog?.providers.find((p) => p.name === shown);

  const models = useMemo(() => {
    const all = provider?.models ?? [];
    const q = query.trim().toLowerCase();
    return q ? all.filter((m) => m.toLowerCase().includes(q)) : all;
  }, [provider, query]);

  const choose = async (providerName: string, model: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.selectProvider(providerName, model);
      await load();
      setOpen(false);
      setQuery('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not switch model.');
    } finally {
      setBusy(false);
    }
  };

  const activeLabel = catalog?.providers.find((p) => p.name === active?.provider)?.label ?? active?.provider ?? 'No provider';

  return (
    <div className="prov" ref={root}>
      {open && (
        <div className="prov-pop" role="dialog" aria-label="Model providers" style={anchor}>
          <div className="prov-list" role="listbox" aria-label="Providers">
            {catalog?.providers.map((p) => (
              <button
                key={p.name}
                role="option"
                aria-selected={p.name === shown}
                className={`prov-item${p.name === shown ? ' prov-item--shown' : ''}`}
                onClick={() => { setBrowsing(p.name); setQuery(''); }}
              >
                <span className="prov-item-name">{p.label}</span>
                {p.name === active?.provider && <Check size={13} />}
              </button>
            ))}
          </div>
          <div className="prov-models">
            <input
              className="prov-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.stopPropagation()}
              placeholder={`Search ${provider?.label ?? ''} models`}
              aria-label="Search models"
            />
            <div className="prov-model-list" role="listbox" aria-label="Models">
              {models.slice(0, MAX_SHOWN).map((m) => {
                const isActive = provider?.name === active?.provider && m === active?.model;
                return (
                  <button key={m} role="option" aria-selected={isActive} className={`prov-model${isActive ? ' prov-model--active' : ''}`} disabled={busy} onClick={() => provider && void choose(provider.name, m)} title={m}>
                    <span>{m}</span>
                    {isActive && <Check size={13} />}
                  </button>
                );
              })}
              {models.length === 0 && (
                <p className="prov-empty">
                  {provider?.models.length === 0
                    ? `No models listed for ${provider.label}.${provider.requires_key ? ' It needs an API key (Settings).' : ''}`
                    : 'No match.'}
                </p>
              )}
              {models.length > MAX_SHOWN && <p className="prov-empty">Showing {MAX_SHOWN} of {models.length}. Type to narrow.</p>}
            </div>
          </div>
          {error && <p className="prov-error">{error}</p>}
        </div>
      )}
      <button ref={trigger} className="prov-trigger" onClick={() => {
        const r = trigger.current?.getBoundingClientRect();
        if (r) setAnchor({ left: r.left, bottom: window.innerHeight - r.top + 6 });
        setOpen((v) => !v);
      }} aria-expanded={open} aria-haspopup="dialog" title="Model provider">
        <Cpu size={15} />
        <span className="prov-trigger-text">
          <span className="prov-trigger-provider">{activeLabel}</span>
          <span className="prov-trigger-model">{active?.model || state.selectedModel || 'Select a model'}</span>
        </span>
        <ChevronUp size={13} className={`prov-chevron${open ? ' prov-chevron--open' : ''}`} />
      </button>
      {!open && error && <p className="prov-error">{error}</p>}
    </div>
  );
};
