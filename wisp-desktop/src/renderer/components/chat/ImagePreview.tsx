import React, { useState } from 'react';
import { useAppState } from '../../state/context.js';
import { X } from '../../icons/index.js';
import './ImagePreview.css';

export const ImagePreview: React.FC = () => {
  const { state, dispatch } = useAppState();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (state.pendingImages.length === 0) return null;

  return (
    <div className="image-preview-row">
      {state.pendingImages.map((img) => (
        <div key={img.id} className="image-preview-thumb" onClick={() => setExpandedId(expandedId === img.id ? null : img.id)}>
          <img src={img.dataUrl} alt={img.fileName} />
          <button
            className="image-preview-remove"
            onClick={(e) => {
              e.stopPropagation();
              dispatch({ type: 'REMOVE_IMAGE', id: img.id });
            }}
            title="Remove image"
          >
            <X size={12} />
          </button>
          <div className="image-preview-info">
            <span className="image-preview-name">{img.fileName}</span>
            <span className="image-preview-size">{(img.size / 1024).toFixed(0)}KB</span>
          </div>
          {expandedId === img.id && (
            <div className="image-preview-expanded" onClick={() => setExpandedId(null)}>
              <img src={img.dataUrl} alt={img.fileName} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
