'use client'

import { useRouter } from 'next/navigation'
import { useState, type FormEvent, type ReactNode } from 'react'
import { createAuditJob } from '@/lib/jobs-api'
import { createErrorMessage } from '@/lib/jobs'
import { NOT_CONFIGURED } from '@/lib/supabase'

/**
 * Free audit entry: one Hugging Face model id in, a job page out. Errors are shown in plain Hindi.
 * With `disabled` the same form is shown switched off (with `note` explaining why), so the page layout
 * does not change when the audit backend is turned on.
 */
export default function AuditForm({ disabled = false, note }: { disabled?: boolean; note?: ReactNode }) {
  const router = useRouter()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (busy || disabled) return
    setBusy(true)
    setError(null)
    try {
      const r = await createAuditJob(value)
      if (r.ok) {
        router.push(`/job/${r.id}`) // stay "busy" while the page changes
        return
      }
      setError(r.code === 'not_configured' ? NOT_CONFIGURED : createErrorMessage(r.code))
    } catch {
      setError(createErrorMessage('unknown'))
    }
    setBusy(false)
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <label htmlFor="audit-model" className="block text-sm font-medium">
        Public Hugging Face model ID
      </label>
      <p className="mt-3 font-mono text-sm text-accent" aria-hidden>
        <span className="text-muted">$</span> sthamly audit --model
      </p>
      <input
        id="audit-model"
        name="model"
        type="text"
        inputMode="url"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        disabled={disabled}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Qwen/Qwen2.5-1.5B-Instruct"
        aria-describedby={error ? 'audit-error audit-help' : 'audit-help'}
        aria-invalid={error ? true : undefined}
        className="mt-2 w-full rounded-lg border border-line bg-bg px-3.5 py-3 font-mono text-sm text-ink placeholder:text-muted/60 focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
      />
      <button type="submit" disabled={disabled || busy || value.trim() === ''} className="btn btn-primary mt-3 w-full">
        {disabled ? 'Jaldi chalu hoga' : busy ? 'Bhej raha hoon...' : 'Free audit chalao →'}
      </button>
      <p id="audit-help" className="mt-3 text-xs text-muted">
        {note ??
          'owner/model-name likho ya Hugging Face ka link paste karo. Sirf public safetensors models (weights lagbhag 4 GB tak). Din me 3 audit.'}
      </p>
      {error && (
        <p id="audit-error" role="alert" className="mt-3 text-sm text-bad">
          {error}
        </p>
      )}
    </form>
  )
}
