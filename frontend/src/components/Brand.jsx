import React from 'react';
import { Icon } from './Icon.jsx';

// 브랜드 버튼을 눌러 서비스 소개 화면으로 이동한다.
export function Brand({ onClick }) { return <button type="button" className="brand brand-button" onClick={onClick} aria-label="체크메이트 홈"><span className="brand-mark"><Icon name="check" /></span>체크메이트</button>; }
