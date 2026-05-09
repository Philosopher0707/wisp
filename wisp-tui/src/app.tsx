#!/usr/bin/env node
import React from 'react';
import { render } from 'ink';
import { App } from './components/App.js';

function parseArgs(): string {
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i++) {
    if ((argv[i] === '-s' || argv[i] === '--server') && i + 1 < argv.length) {
      return argv[i + 1];
    }
  }
  return process.env.WISP_SERVER || 'http://localhost:8000';
}

const serverUrl = parseArgs();

const { waitUntilExit } = render(<App serverUrl={serverUrl} />);

process.on('SIGINT', () => {
  waitUntilExit().then(() => process.exit(0));
});
