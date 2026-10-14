import React from 'react';
import { Icon } from './Icon.jsx';

// 오류 내용을 스크린 리더가 알 수 있는 메시지로 표시한다.
export function ErrorMessage({ message }) { return message ? <p className="error error-box" role="alert"><Icon name="warning" />{message}</p> : null; }
