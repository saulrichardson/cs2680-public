import type { ReactNode } from 'react';
import { Providers } from '@/ui/Providers';
import '@/ui/styles/globals.css';
import '@/ui/styles/components.css';
import { ThemeScript } from '@/ui/theme/ThemeScript';

export const metadata = { title: 'Mission Control', description: 'An operator console for Claude Code' };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body>
        <Providers>
          <div className="app">{children}</div>
        </Providers>
      </body>
    </html>
  );
}
