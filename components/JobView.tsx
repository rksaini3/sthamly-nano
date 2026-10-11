'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { AuditResultView, CalibrateResultView } from '@/components/JobResult'
import { fetchJob } from '@/lib/jobs-api'
import { isTerminal, jobErrorMessage, type JobData, type JobStatus, type StepState } from '@/lib/jobs'

const POLL_MS = 3000
const RETRY_MS = 8000

const STATUS_TEXT: Record<JobStatus, string> = {
  queued: 'Line me hai',
  running: 'Chal raha hai',
  done: 'Poora hua',
  failed: 'Poora nahi hua',
  refused: 'Nahi liya gaya',
}

// A symbol next to the words, so a step never depends on color alone.
const STEP_MARK: Record<StepState, { mark: string; text: string; cls: string }> = {
  done: { mark: '✓', text: 'ho gaya', cls: 'text-good' },
  running: { mark: '⚙', text: 'chal raha hai', cls: 'text-ink' },
  failed: { mark: '✕', text: 'fail', cls: 'text-bad' },
  pending: { mark: '○', text: 'baaki', cls: 'text-muted' },
}

/** Live view of one job: polls get_job every 3 seconds until the job reaches a final state. */
export default function JobView({ id }: { id: string }) {
  const [job, setJob] = useState<JobData | null | undefined>(undefined) // undefined = still loading
  const [netError, setNetError] = useState<string | null>(null)

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function tick() {
      let delay = POLL_MS
      try {
        const r = await fetchJob(id)
        if (stopped) return
        if (r.ok) {
          setJob(r.job)
          setNetError(null)
          if (r.job === null || isTerminal(r.job.status)) return // nothing more to wait for
        } else {
          setNetError(r.message)
          delay = RETRY_MS
        }
      } catch {
        if (stopped) return
        setNetError('Network me dikkat aayi.')
        delay = RETRY_MS
      }
      timer = setTimeout(tick, delay)
    }

    tick()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [id])

  if (job === undefined) {
    return (
      <div aria-live="polite">
        <p className="text-muted">{netError ? `Load nahi hua: ${netError}` : 'Load ho raha hai...'}</p>
      </div>
    )
  }

  if (job === null) {
    return (
      <div>
        <h1 className="font-display text-2xl font-semibold">Ye job nahi mila</h1>
        <p className="mt-2 text-muted">Link galat hai, ya job purana hokar hat gaya.</p>
        <p className="mt-4">
          <Link className="underline underline-offset-2" href="/">Home par jao</Link>
        </p>
      </div>
    )
  }

  const active = job.status === 'queued' || job.status === 'running'
  const contact = process.env.NEXT_PUBLIC_CONTACT_URL

  return (
    <div className="space-y-8">
      <header>
        <p className="text-sm text-muted">{job.kind === 'audit' ? 'Free Hindi audit' : 'Calibrated Q4'}</p>
        <h1 className="break-words font-display text-2xl font-bold tracking-tight sm:text-3xl">{job.hf_model}</h1>
        <p className="mt-2 text-sm" aria-live="polite">
          <span className="font-medium">{STATUS_TEXT[job.status]}</span>
          {job.status === 'queued' && job.queue_position
            ? `. Aapka number: ${job.queue_position}. Abhi kaam haath se chalaya jaata hai, isliye line me der ho sakti hai.`
            : ''}
        </p>
      </header>

      {netError && (
        <p role="status" className="text-sm text-warn">
          Update laane me dikkat ({netError}). Dobara koshish ho rahi hai.
        </p>
      )}

      {(active || job.steps.length > 0) && (
        <section aria-labelledby="steps-title">
          <h2 id="steps-title" className="sr-only">Progress</h2>
          {job.status === 'running' && (
            <div
              role="progressbar"
              aria-label="Kaam kitna hua"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={job.progress}
              className="mb-4 h-2 w-full overflow-hidden rounded-full bg-line"
            >
              <div className="h-full bg-ink" style={{ width: `${job.progress}%` }} />
            </div>
          )}
          <ol className="space-y-2">
            {job.steps.map((s) => {
              const m = STEP_MARK[s.state]
              return (
                <li key={s.id} className={`flex items-baseline gap-3 text-sm ${m.cls}`}>
                  <span aria-hidden className="w-4 text-center">{m.mark}</span>
                  <span>
                    {s.label} <span className="text-muted">({m.text})</span>
                  </span>
                </li>
              )
            })}
          </ol>
          {job.status === 'running' && (
            <p className="mt-3 text-xs text-muted">{job.progress}% (andaaza). Is page ko band kar sakte ho, link se wapas aa sakte ho.</p>
          )}
        </section>
      )}

      {(job.status === 'failed' || job.status === 'refused') && (
        <div role="alert" className="rounded-md border border-bad/40 bg-bad/5 px-4 py-3 text-sm">
          <p className="font-medium text-bad">{STATUS_TEXT[job.status]}</p>
          <p className="mt-0.5 text-muted">{jobErrorMessage(job.status, job.error)}</p>
          <p className="mt-2">
            <Link className="underline underline-offset-2" href="/">Doosra model try karo</Link>
          </p>
        </div>
      )}

      {job.status === 'done' && job.result?.kind === 'audit' && <AuditResultView result={job.result} />}
      {job.status === 'done' && job.result?.kind === 'calibrate' && (
        <CalibrateResultView result={job.result} contact={contact} />
      )}
    </div>
  )
}
