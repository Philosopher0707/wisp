import React, { createContext, useContext, useReducer, Dispatch, useMemo } from 'react';
import { AppState, Action, createInitialState, appReducer } from './types.js';

export interface AppContextType {
  state: AppState;
  dispatch: Dispatch<Action>;
}

export const AppContext = createContext<AppContextType>({
  state: createInitialState(),
  dispatch: () => {},
});

export function useAppState(): AppContextType {
  return useContext(AppContext);
}

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, dispatch] = useReducer(appReducer, undefined, createInitialState);
  const value = useMemo(() => ({ state, dispatch }), [state, dispatch]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
};
