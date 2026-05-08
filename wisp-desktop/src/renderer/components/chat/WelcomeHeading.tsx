import React from 'react';
import { useAppState } from '../../state/context.js';
import './WelcomeHeading.css';

function modelLabel(name: string): string {
  return name
    .split(/[-:]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

export const WelcomeHeading: React.FC = () => {
  const { state } = useAppState();
  const model = modelLabel(state.selectedModel);

  return (
    <div className="welcome-block">
      <h1 className="welcome-heading">What should we work on?</h1>
      <p className="welcome-model">Using {model}</p>
    </div>
  );
};
