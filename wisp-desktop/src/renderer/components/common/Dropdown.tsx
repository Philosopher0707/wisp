import React, { useRef } from 'react';
import { useClickOutside } from '../../hooks/useClickOutside.js';
import './Dropdown.css';

interface DropdownOption {
  value: string;
  label: string;
}

interface DropdownProps {
  options: DropdownOption[];
  selected: string;
  onSelect: (value: string) => void;
  onClose: () => void;
  position?: 'left' | 'right';
}

export const Dropdown: React.FC<DropdownProps> = ({
  options,
  selected,
  onSelect,
  onClose,
  position = 'left',
}) => {
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, onClose, true);

  return (
    <div className={`dropdown ${position === 'right' ? 'dropdown--right' : ''}`} ref={ref}>
      {options.map((opt) => (
        <button
          key={opt.value}
          className={`dropdown-item ${opt.value === selected ? 'dropdown-item--selected' : ''}`}
          onClick={() => onSelect(opt.value)}
        >
          <span>{opt.label}</span>
          {opt.value === selected && <span className="dropdown-check">✓</span>}
        </button>
      ))}
    </div>
  );
};
