import './globals.css'
import Link from 'next/link'
import type { Metadata, Viewport } from 'next'
import type { ReactNode } from 'react'

export const metadata: Metadata = {
  title: 'Sthamly Legal Desk',
  description: 'Vakeel ke liye offline AI sahayak. Aapki PDF aapke computer par hi rehti hai.',
}

export const viewport: Viewport = { themeColor: '#080C16' }

const NAV = [{ href: '/#desk', label: 'Desk' }]

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
    <html lang="hi">
      <head>
        {/* Fonts optional hain: nahi load hue to tailwind.config.js ke system fonts chalte hain. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap"
        />
      </head>
      <body className="bg-bg text-ink font-sans antialiased min-h-screen flex flex-col">
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-raised focus:px-3 focus:py-2 focus:text-ink"
        >
          Seedha content par jaayein
        </a>

        <header className="sticky top-0 z-30 border-b border-line bg-bg/80 backdrop-blur">
          <nav
            className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between gap-3 px-4 sm:px-6"
            aria-label="Main"
          >
            <Link href="/" className="flex items-center gap-2.5 whitespace-nowrap font-display text-lg font-bold tracking-tight">
              <Logo />
              <span>Sthamly</span>
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

        <div id="content" className="flex-1">
          {children}
        </div>

        <footer className="mt-20 border-t border-line">
          <div className="mx-auto w-full max-w-5xl px-4 py-8 text-sm text-muted sm:px-6">
            <p>Ye kanooni salah nahi hai. Drafting aur analysis me madad karta hai; output ko kisi qualified vakeel se verify karwayein.</p>
          </div>
        </footer>
      </body>
    </html>
  )
}
