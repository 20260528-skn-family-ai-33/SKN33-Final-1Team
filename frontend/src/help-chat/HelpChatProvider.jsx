import React, { createContext, useContext, useLayoutEffect, useRef, useState } from 'react';
import { useAuth } from '../auth/AuthProvider.jsx';
import { mountHelpChat } from './index.js';

const HelpChatAnchor = createContext(null);

// The widget outlives page headers and workspace resets, while account changes clear it.
export function HelpChatProvider({ children }) {
  const { status, user, refreshSession } = useAuth();
  const userId = status === 'authenticated' ? user?.id : null;
  const [anchor, setAnchor] = useState(null);
  const widget = useRef(null);

  useLayoutEffect(() => {
    if (!userId) return;
    const container = document.createDocumentFragment();
    const instance = mountHelpChat({ container, userId, position: 'header', onSessionExpired: refreshSession });
    widget.current = { instance, container };
    return () => {
      instance.destroy();
      widget.current = null;
    };
  }, [userId, refreshSession]);

  useLayoutEffect(() => {
    const current = widget.current;
    if (current) current.instance.setContainer(anchor ?? current.container);
  }, [anchor, userId, refreshSession]);

  return <HelpChatAnchor.Provider value={setAnchor}>{children}</HelpChatAnchor.Provider>;
}

export function useHelpChatAnchor() {
  return useContext(HelpChatAnchor);
}
