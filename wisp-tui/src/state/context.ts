import React, { createContext, useContext, Dispatch } from 'react';
import { AppState, Action, createInitialState } from './types.js';

export interface AppContextType {
  state: AppState;
  dispatch: Dispatch<Action>;
  sendMessage: (msg: { type: string; [key: string]: unknown }) => void;
}

export const AppContext = createContext<AppContextType>({
  state: createInitialState('http://localhost:8000'),
  dispatch: () => {},
  sendMessage: () => {},
});

export function useAppState(): AppContextType {
  return useContext(AppContext);
}
