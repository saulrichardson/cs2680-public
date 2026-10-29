'use client';
import { useEffect, useState } from 'react';

/**
 * The two keyboard hints the chrome shows, spelled for the keyboard in front of the operator.
 *
 * Read after mount rather than during render: the server has no `navigator`, and a hint that
 * disagreed with the server's HTML would be a hydration mismatch. macOS is the optimistic first
 * paint (this console is developed and demonstrated there), so only a non-Mac sees one frame of ⌘.
 */
export function useShortcutHints(): { palette: string; submit: string } {
  const [mac, setMac] = useState(true);
  useEffect(() => {
    const platform = navigator.platform || navigator.userAgent;
    setMac(/mac|iphone|ipad|ipod/i.test(platform));
  }, []);
  return mac ? { palette: '⌘K', submit: '⌘↵' } : { palette: 'Ctrl K', submit: 'Ctrl ↵' };
}
