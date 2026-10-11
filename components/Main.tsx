import type { ReactNode } from 'react'

/** Standard page column: same width and gutters on every page. */
export default function Main({ children }: { children: ReactNode }) {
  return <main className="mx-auto w-full max-w-5xl px-4 sm:px-6 py-10 sm:py-14">{children}</main>
}
