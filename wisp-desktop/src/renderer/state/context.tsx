import React, { createContext, useContext, Dispatch } from 'react';
import { AppState, Action, createInitialState } from './types.js';

export interface AppContextType {
  state: AppState;
  dispatch: Dispatch<Action>;
  sendMessage: (msg: Record<string, unknown>) => void;
}

export const AppContext = createContext<AppContextType>({
  state: createInitialState(),
  dispatch: () => {},
  sendMessage: () => {},
});

export function useAppState(): AppContextType {
  return useContext(AppContext);
}
