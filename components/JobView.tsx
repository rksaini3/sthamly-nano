'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { AuditResultView, CalibrateResultView } from '@/components/JobResult'
import Terminal from '@/components/Terminal'
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

const STATUS_CLS: Record<JobStatus, string> = {
  queued: 'border-line text-muted',
  running: 'border-accent/50 text-accent',
  done: 'border-good/50 text-good',
  failed: 'border-bad/50 text-bad',
  refused: 'border-warn/50 text-warn',
}

// [✓] [⚙] [ ] like a terminal log. A symbol AND a word per step, so nothing depends on color alone.
const STEP_MARK: Record<StepState, { mark: string; text: string; cls: string }> = {
  done: { mark: '✓', text: 'ho gaya', cls: 'text-good' },
  running: { mark: '⚙', text: 'chal raha hai', cls: 'text-ink' },
  failed: { mark: '✕', text: 'fail', cls: 'text-bad' },
  pending: { mark: '\u00a0', text: 'baaki', cls: 'text-muted' },
}

/** Everything a visitor sees for one job. Pure (no fetching), so it can be rendered in tests. */
export function JobBody({ job, netError, contact }: { job: JobData; netError: string | null; contact?: string }) {
  const active = job.status === 'queued' || job.status === 'running'

  return (
    <div className="space-y-8">
      <header>
        <p className="eyebrow">{job.kind === 'audit' ? 'Free Hindi audit' : 'Calibrated Q4'}</p>
        <h1 className="mt-2 break-words font-mono text-xl font-medium tracking-tight sm:text-2xl">{job.hf_model}</h1>
        <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" aria-live="polite">
          <span className={`rounded-full border px-3 py-0.5 text-xs font-medium ${STATUS_CLS[job.status]}`}>
            {STATUS_TEXT[job.status]}
          </span>
          {job.status === 'queued' && job.queue_position ? (
            <span className="text-muted">
              Aapka number: {job.queue_position}. Abhi kaam haath se chalaya jaata hai, isliye line me der ho sakti hai.
            </span>
          ) : null}
        </p>
      </header>

      {netError && (
        <p role="status" className="text-sm text-warn">
          Update laane me dikkat ({netError}). Dobara koshish ho rahi hai.
        </p>
      )}

      {(active || job.steps.length > 0) && (
        <Terminal title="live progress">
          <h2 className="sr-only">Progress</h2>
          <ol className="space-y-2 font-mono text-sm">
            {job.steps.map((s) => {
              const m = STEP_MARK[s.state]
              return (
                <li key={s.id} className={`flex gap-3 ${m.cls}`}>
                  <span aria-hidden className="shrink-0 whitespace-pre">[{m.mark}]</span>
                  <span>
                    {s.label}
                    {s.state === 'running' && job.status === 'running' ? ` ... ${job.progress}%` : ''}{' '}
                    <span className="text-muted">({m.text})</span>
                  </span>
                </li>
              )
            })}
          </ol>
          {job.status === 'running' && (
            <div className="mt-5">
              <div
                role="progressbar"
                aria-label="Kaam kitna hua"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={job.progress}
                className="h-1.5 w-full overflow-hidden rounded-full bg-line"
              >
                <div className="h-full rounded-full bg-brand transition-[width] duration-500" style={{ width: `${job.progress}%` }} />
              </div>
              <p className="mt-2 text-xs text-muted">
                {job.progress}% (andaaza). Is page ko band kar sakte ho, isi link se wapas aa sakte ho.
              </p>
            </div>
          )}
        </Terminal>
      )}

      {(job.status === 'failed' || job.status === 'refused') && (
        <div role="alert" className="rounded-xl border border-bad/40 bg-bad/10 px-4 py-4 text-sm">
          <p className="font-medium text-bad">{STATUS_TEXT[job.status]}</p>
          <p className="mt-1 text-ink/90">{jobErrorMessage(job.status, job.error)}</p>
          <p className="mt-3">
            <Link className="underline underline-offset-2" href="/#audit">Doosra model try karo</Link>
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
      <Terminal title="live progress">
        <p className="caret font-mono text-sm text-muted" aria-live="polite">
          {netError ? `Load nahi hua: ${netError}` : 'Job load ho raha hai'}
        </p>
      </Terminal>
    )
  }

  if (job === null) {
    return (
      <div>
        <h1 className="font-display text-2xl font-semibold">Ye job nahi mila</h1>
        <p className="mt-2 text-muted">Link galat hai, ya job purana hokar hat gaya.</p>
        <p className="mt-4">
          <Link className="btn btn-ghost" href="/#audit">Naya audit chalao</Link>
        </p>
      </div>
    )
  }

  return <JobBody job={job} netError={netError} contact={process.env.NEXT_PUBLIC_CONTACT_URL} />
}
