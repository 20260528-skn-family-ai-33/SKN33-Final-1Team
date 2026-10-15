import React from 'react';
import { Icon } from './Icon.jsx';

// 브랜드 버튼을 눌러 서비스 소개 화면으로 이동한다.
export function Brand({ onClick }) { return <button type="button" className="brand brand-button" onClick={onClick} aria-label="크리에이티브체커 홈"><span className="brand-mark"><Icon name="check" /></span>크리에이티브체커</button>; }
