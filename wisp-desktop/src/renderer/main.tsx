import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App.js';

import './styles/tokens.css';
import './styles/reset.css';
import './styles/global.css';

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root element');

const params = new URLSearchParams(window.location.search);
const serverUrl = params.get('server') || 'http://localhost:8000';
const apiKey = params.get('api_key') || '';

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App serverUrl={serverUrl} apiKey={apiKey} />
  </React.StrictMode>,
);
