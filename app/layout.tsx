import './globals.css'
import Link from 'next/link'
import type { Metadata } from 'next'
import type { ReactNode } from 'react'

export const metadata: Metadata = {
  title: 'Sthamly NanoBrain: Hindi quantization quality',
  description: 'Quantized models Hindi par FP16 se kitna door jaate hain: naapa hua, error bars ke saath.',
}

const NAV = [
  { href: '/leaderboard', label: 'Leaderboard' },
  { href: '/benchmark', label: 'Benchmark' },
]

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Fonts are optional: if they do not load, the system fonts in tailwind.config.js are used. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
        />
      </head>
      <body className="bg-bg text-ink font-sans antialiased min-h-screen flex flex-col">
        <header className="border-b border-line">
          <nav className="mx-auto w-full max-w-5xl px-4 sm:px-6 h-14 flex items-center justify-between gap-4" aria-label="Main">
            <Link href="/" className="font-display font-bold text-lg tracking-tight whitespace-nowrap">
              Sthamly NanoBrain
            </Link>
            <ul className="flex items-center gap-5 text-sm">
              {NAV.map((n) => (
                <li key={n.href}>
                  <Link href={n.href} className="text-muted hover:text-ink py-2 inline-block">
                    {n.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </header>

        <div className="flex-1">{children}</div>

        <footer className="border-t border-line mt-16">
          <div className="mx-auto w-full max-w-5xl px-4 sm:px-6 py-8 text-sm text-muted space-y-2">
            <p>
              Ye naap FP16 se distribution ka fark hai (proxy). Hindi sawal-jawab ya summary par accuracy abhi naapi
              nahi gayi.
            </p>
            <p>
              <a className="underline underline-offset-2 hover:text-ink" href="https://github.com/rksaini3/sthamly-nano">
                Code aur methodology (GitHub)
              </a>
            </p>
          </div>
        </footer>
      </body>
    </html>
  )
}