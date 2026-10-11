import './globals.css'
import Link from 'next/link'
import type { Metadata, Viewport } from 'next'
import type { ReactNode } from 'react'

export const metadata: Metadata = {
  title: 'Sthamly NanoBrain: Hindi quantization quality',
  description: 'Quantized models Hindi par FP16 se kitna door jaate hain: naapa hua, error bars ke saath.',
}

export const viewport: Viewport = { themeColor: '#080C16' }

const NAV = [
  { href: '/#audit', label: 'Audit' },
  { href: '/leaderboard', label: 'Leaderboard' },
  { href: '/benchmark', label: 'Benchmark' },
  { href: '/#desk', label: 'Desk' },
]

function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="rgb(var(--brand))" />
      <path
        d="M22 11C22 9 20 8 16 8C12 8 10 9.5 10 12C10 14.5 12.5 15.2 16 16C19.5 16.8 22 17.5 22 20C22 22.5 20 24 16 24C12 24 10 23 10 21"
        fill="none"
        stroke="rgb(var(--brand-ink))"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
    </svg>
  )
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Fonts are optional: if they do not load, the system fonts in tailwind.config.js are used. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
        />
      </head>
      <body className="bg-bg text-ink font-sans antialiased min-h-screen flex flex-col">
        <header className="sticky top-0 z-30 border-b border-line bg-bg/80 backdrop-blur">
          <nav
            className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between gap-3 px-4 sm:px-6"
            aria-label="Main"
          >
            <Link href="/" className="flex items-center gap-2.5 whitespace-nowrap font-display text-lg font-bold tracking-tight">
              <Logo />
              <span>
                Sthamly<span className="hidden text-muted sm:inline"> NanoBrain</span>
              </span>
            </Link>
            <ul className="flex items-center text-[0.8rem] sm:gap-2 sm:text-sm">
              {NAV.map((n) => (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    className="inline-block rounded-md px-1 py-2 text-muted transition-colors hover:bg-raised hover:text-ink sm:px-2.5"
                  >
                    {n.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </header>

        <div className="flex-1">{children}</div>

        <footer className="mt-20 border-t border-line">
          <div className="mx-auto w-full max-w-5xl space-y-2 px-4 py-8 text-sm text-muted sm:px-6">
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
