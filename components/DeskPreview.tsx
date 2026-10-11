'use client'

import { useRef, useState, type KeyboardEvent } from 'react'
import { DESK_PROFILES } from '@/lib/desk'

/**
 * "Chameleon" design preview: one app, and the activation key decides which desk you get.
 * Tabs stand in for three different keys. This is a picture of the plan, not the app.
 */
export default function DeskPreview() {
  const [index, setIndex] = useState(0)
  const tabs = useRef<Array<HTMLButtonElement | null>>([])
  const profile = DESK_PROFILES[index]
  const [primary, ...rest] = profile.actions

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const step = e.key === 'ArrowRight' ? 1 : DESK_PROFILES.length - 1
    const next = (index + step) % DESK_PROFILES.length
    setIndex(next)
    tabs.current[next]?.focus()
  }

  return (
    <div>
      <div role="tablist" aria-label="Activation key ka profile" onKeyDown={onKeyDown} className="flex flex-wrap gap-2">
        {DESK_PROFILES.map((p, i) => {
          const selected = i === index
          return (
            <button
              key={p.id}
              ref={(el) => {
                tabs.current[i] = el
              }}
              type="button"
              role="tab"
              id={`desk-tab-${p.id}`}
              aria-selected={selected}
              aria-controls="desk-panel"
              tabIndex={selected ? 0 : -1}
              onClick={() => setIndex(i)}
              className={`rounded-full border px-4 py-2 text-sm font-medium transition-colors ${
                selected
                  ? 'border-brand bg-brand/15 text-ink'
                  : 'border-line bg-raised text-muted hover:border-accent/60 hover:text-ink'
              }`}
            >
              Key: {p.tab}
            </button>
          )
        })}
      </div>

      <div id="desk-panel" role="tabpanel" aria-labelledby={`desk-tab-${profile.id}`} className="term mt-4">
        <div className="flex items-center gap-2 border-b border-line bg-surface px-4 py-2.5">
          <span className="h-2.5 w-2.5 rounded-full bg-line" aria-hidden />
          <span className="h-2.5 w-2.5 rounded-full bg-line" aria-hidden />
          <span className="h-2.5 w-2.5 rounded-full bg-line" aria-hidden />
          <span className="ml-2 truncate font-mono text-xs text-muted">{profile.label}</span>
          <span className="ml-auto rounded-full border border-warn/40 px-2 py-0.5 text-[0.7rem] text-warn">design preview</span>
        </div>

        <div className="grid gap-5 p-4 sm:grid-cols-[13rem_minmax(0,1fr)] sm:p-5">
          <div className="space-y-3 rounded-lg border border-line bg-surface p-3">
            <p className="text-xs text-muted">Activation key</p>
            <p className="font-mono text-xs text-accent">STH-••••-••••-••••-••••</p>
            <p className="text-xs text-muted">Profile</p>
            <p className="text-sm font-medium">{profile.label}</p>
            <p className="text-xs text-muted">Offline mode: network ki zaroorat nahi</p>
          </div>

          <div>
            <div aria-hidden className="btn btn-primary w-full justify-start sm:w-auto">
              <span>{profile.icon}</span> {primary}
            </div>
            <ul className="mt-4 flex flex-wrap gap-2" aria-label="Actions">
              {rest.map((a) => (
                <li key={a} className="rounded-lg border border-line bg-raised px-3 py-1.5 text-sm">
                  {a}
                </li>
              ))}
            </ul>
            <div className="mt-5 rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">
              Document yahan chhodo. Padhai aapke computer par hoti hai.
            </div>
          </div>
        </div>

        <p className="border-t border-line bg-surface px-4 py-3 text-xs text-muted">{profile.disclaimer}</p>
      </div>
    </div>
  )
}
