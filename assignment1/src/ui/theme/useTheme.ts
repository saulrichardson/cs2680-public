'use client';
import { useCallback, useEffect, useState } from 'react';

export type Theme = 'system' | 'light' | 'dark';

export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void; cycle: () => void } {
  const [theme, setThemeState] = useState<Theme>('system');
  useEffect(() => {
    try {
      const t = localStorage.getItem('mc-theme');
      if (t === 'light' || t === 'dark') setThemeState(t);
    } catch {}
  }, []);
  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    try {
      if (t === 'system') {
        localStorage.removeItem('mc-theme');
        delete document.documentElement.dataset.theme;
      } else {
        localStorage.setItem('mc-theme', t);
        document.documentElement.dataset.theme = t;
      }
    } catch {}
  }, []);
  const cycle = useCallback(
    () => setTheme(theme === 'system' ? 'dark' : theme === 'dark' ? 'light' : 'system'),
    [theme, setTheme],
  );
  return { theme, setTheme, cycle };
}
