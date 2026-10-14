import React from 'react';
import { labels } from '../reviewConstants.js';

// 현재 검수 단계와 화면 제목을 표시한다.
export function Heading({ step, title, children }) { return <div className="page-heading"><div className="eyebrow">STEP {String(step).padStart(2, '0')} · {labels[['input', 'criteria', 'running', 'results'][step - 1]]}</div><h1 tabIndex="-1" id="page-title">{title}</h1><p>{children}</p></div>; }
