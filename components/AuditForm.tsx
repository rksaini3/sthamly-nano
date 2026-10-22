'use client'

import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { createAuditJob, fetchJob } from '@/lib/jobs-api'
import {
  createErrorMessage, isTerminal, jobErrorMessage, normalizeModelId,
  type AuditResult, type JobData,
} from '@/lib/jobs'

const POLL_MS = 3000
const MAX_POLL_ERRORS = 5

/**
 * Free public audit form. Every status and number shown here comes from the server's job row.
 * The progress bar is the worker's real progress value. Nothing is animated and no result is invented.
 */
export default function AuditForm({ disabled = false, note }: { disabled?: boolean; note?: ReactNode }) {
  const [value, setValue] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const finished = job !== null && isTerminal(job.status)
  const polling = jobId !== null && !finished && error === null
  const busy = submitting || polling

  // Real status polling. Stops when the job finishes, after repeated errors, or on unmount.
  useEffect(() => {
    if (!jobId) return
    const id: string = jobId
    let cancelled = false
    let errors = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    async function tick() {
      const res = await fetchJob(id)
      if (cancelled) return
      if (!res.ok) {
        errors += 1
        if (errors >= MAX_POLL_ERRORS) {
          setError(res.message)
          return
        }
      } else if (res.job === null) {
        setError('Ye job ab mil nahi raha. Dobara try karo.')
        return
      } else {
        errors = 0
        setJob(res.job)
        if (isTerminal(res.job.status)) return
      }
      timer = setTimeout(tick, POLL_MS)
    }

    tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId])

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (disabled || busy) return

    const model = normalizeModelId(value)
    if (!model) {
      setError(createErrorMessage('invalid_model'))
      return
    }

    setSubmitting(true)
    setError(null)
    setJob(null)
    setJobId(null)

    const res = await createAuditJob(model)
    setSubmitting(false)
    if (!res.ok) {
      setError(createErrorMessage(res.code))
      return
    }
    setJobId(res.id)
  }

  const result: AuditResult | null =
    job?.status === 'done' && job.result?.kind === 'audit' ? job.result : null

  return (
    <form onSubmit={onSubmit} noValidate>
      <label htmlFor="audit-model" className="block text-sm font-medium">
        Public Hugging Face model ID
      </label>
      <p className="mt-3 font-mono text-sm text-accent" aria-hidden>
        <span className="text-muted">\$</span> sthamly audit --model
      </p>
      <input
        id="audit-model"
        name="model"
        type="text"
        inputMode="url"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        disabled={disabled || busy}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Qwen/Qwen2.5-1.5B-Instruct"
        aria-describedby={error ? 'audit-error audit-help' : 'audit-help'}
        aria-invalid={error ? true : undefined}
        className="mt-2 w-full rounded-lg border border-line bg-bg px-3.5 py-3 font-mono text-sm text-ink placeholder:text-muted/60 focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
      />

      <button
        type="submit"
        disabled={disabled || busy || value.trim() === ''}
        className="btn btn-primary mt-3 w-full"
      >
        {disabled
          ? 'Jaldi chalu hoga'
          : submitting
            ? 'Bhej rahe hain...'
            : polling
              ? 'Audit chal raha hai...'
              : 'Free audit chalao →'}
      </button>

      <p id="audit-help" className="mt-3 text-xs text-muted">
        {note ??
          'owner/model-name likho ya Hugging Face ka link paste karo. Sirf public safetensors models (weights lagbhag 4 GB tak). Din me 3 audit.'}
      </p>

      {error && (
        <p id="audit-error" role="alert" className="mt-3 text-sm text-red-500">
          ⚠️ {error}
        </p>
      )}

      {job && (
        <div aria-live="polite" className="mt-4 space-y-2 rounded-lg border border-line bg-surface p-4 font-mono text-xs text-ink">
          <p className="text-muted">// {job.hf_model} · {job.status}</p>

          {!finished && (
            <>
              <div
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={job.progress}
                aria-label="Audit progress"
                className="h-2 w-full rounded bg-line/30"
              >
                <div
                  className="h-2 rounded bg-accent transition-[width] duration-500"
                  style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
                />
              </div>
              <p>
                {job.step ?? (job.status === 'queued' ? 'Line me hai' : 'Chal raha hai')} · {job.progress}%
              </p>
              {job.status === 'queued' && job.queue_position !== null && (
                <p className="text-muted">Line me number: {job.queue_position}</p>
              )}
            </>
          )}

          <ul className="space-y-1">
            {job.steps.map((s) => (
              <li
                key={s.label}
                className={s.state === 'done' ? 'text-good' : s.state === 'failed' ? 'text-bad' : 'text-muted'}
              >
                [{s.state === 'done' ? '✓' : s.state === 'failed' ? '✗' : s.state === 'running' ? '…' : ' '}] {s.label}
              </li>
            ))}
          </ul>

          {finished && job.status !== 'done' && (
            <p role="alert" className="text-bad">{jobErrorMessage(job.status, job.error)}</p>
          )}

          {job.status === 'done' && !result && (
            <p className="text-bad">Audit poora hua, par numbers nahi mile. Ye report nahi dikhayi ja sakti.</p>
          )}

          {result && (
            <>
              <div className="overflow-x-auto pt-2">
                <table className="w-full text-left">
                  <thead>
                    <tr className="text-muted">
                      <th className="py-1 pr-3 font-normal">Test set</th>
                      <th className="py-1 pr-3 font-normal">Variant</th>
                      <th className="py-1 pr-3 font-normal">KLD (kam = behtar)</th>
                      <th className="py-1 font-normal">Same top-token</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(result.results).flatMap(([domain, variants]) =>
                      Object.entries(variants).map(([variant, m]) => (
                        <tr key={`${domain}-${variant}`} className="border-t border-line">
                          <td className="py-1.5 pr-3">{domain}</td>
                          <td className="py-1.5 pr-3">{variant}</td>
                          <td className="py-1.5 pr-3">{m.kld[0].toFixed(4)} ± {m.kld[1].toFixed(4)}</td>
                          <td className="py-1.5">{m.top[0].toFixed(1)}% ± {m.top[1].toFixed(1)}</td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              </div>
              <p className="pt-2 text-muted">
                {result.note || 'KLD FP16 se farak hai, task accuracy nahi. Ye ek proxy score hai.'}
              </p>
            </>
          )}
        </div>
      )}
    </form>
  )
}