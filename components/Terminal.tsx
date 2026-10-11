import type { ReactNode } from 'react'

/** A terminal-style window: the visual home of everything "live" (audit input, job progress). */
export default function Terminal({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="term">
      <div className="flex items-center gap-2 border-b border-line bg-surface px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-line" aria-hidden />
        <span className="h-2.5 w-2.5 rounded-full bg-line" aria-hidden />
        <span className="h-2.5 w-2.5 rounded-full bg-line" aria-hidden />
        <span className="ml-2 truncate font-mono text-xs text-muted">{title}</span>
      </div>
      <div className="p-4 sm:p-5">{children}</div>
    </div>
  )
}
