import React from 'react';
import { AppProvider } from './state/context.js';
import { AppShell } from './components/layout/AppShell.js';

export const App: React.FC = () => {
  return (
    <AppProvider>
      <AppShell />
    </AppProvider>
  );
};
