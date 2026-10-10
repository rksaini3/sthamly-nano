'use client'

import { useRouter } from 'next/navigation'
import { useState, type FormEvent } from 'react'
import { createAuditJob } from '@/lib/jobs-api'
import { createErrorMessage } from '@/lib/jobs'
import { NOT_CONFIGURED } from '@/lib/supabase'

/** Free audit entry: one Hugging Face model id in, a job page out. Errors are shown in plain Hindi. */
export default function AuditForm() {
  const router = useRouter()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (busy) return
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
      <div className="mt-2 flex flex-col gap-3 sm:flex-row">
        <input
          id="audit-model"
          name="model"
          type="text"
          inputMode="url"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Qwen/Qwen2.5-1.5B-Instruct"
          aria-describedby={error ? 'audit-error audit-help' : 'audit-help'}
          aria-invalid={error ? true : undefined}
          className="min-w-0 flex-1 rounded-md border border-line bg-bg px-3 py-2.5 font-mono text-sm placeholder:text-muted/70"
        />
        <button
          type="submit"
          disabled={busy || value.trim() === ''}
          className="inline-flex items-center justify-center rounded-md bg-ink px-5 py-2.5 text-sm font-medium text-bg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? 'Bhej raha hoon...' : 'Free audit chalao'}
        </button>
      </div>
      <p id="audit-help" className="mt-2 text-xs text-muted">
        owner/model-name likho ya Hugging Face ka link paste karo. Sirf public safetensors models (weights lagbhag 4 GB
        tak). Din me 3 audit.
      </p>
      {error && (
        <p id="audit-error" role="alert" className="mt-3 text-sm text-bad">
          {error}
        </p>
      )}
    </form>
  )
}
