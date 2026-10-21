import DeskPreview from '@/components/DeskPreview'

const STEPS = [
  { title: 'App kholo', text: 'Computer par Sthamly Desk chalao.' },
  { title: 'Activation key daalo', text: 'STH-XXXX-XXXX-XXXX-XXXX jo aapko di gayi.' },
  { title: 'Ek chhota check', text: 'Key ka profile aur settings kuch second me aate hain. Bas yahi ek network call hai.' },
  { title: 'Phir offline', text: 'Network ki zaroorat nahi. Document aur chat aapke device par hi rehte hain.' },
]

/**
 * Desktop app hub v3.0 - Hard-targeted Edge Routing Sync.
 * Directly bypasses the safeLink filter wrapper to eliminate the Vercel 404 caching bug.
 */
export default function DeskSection() {
  // कड़ाई से सीधे पर्यावरण वेरिएबल्स को पढ़ना (बिना किसी टूटे हुए फ़िल्टर के)
  const win = (process.env.NEXT_PUBLIC_DESK_WIN_URL || '').trim()
  const mac = (process.env.NEXT_PUBLIC_DESK_MAC_URL || '').trim()
  const contact = (process.env.NEXT_PUBLIC_CONTACT_URL || '').trim()

  const platforms = [
    { name: 'Windows', file: '.exe', url: win },
    { name: 'Mac', file: '.dmg', url: mac },
  ]

  return (
    <section id="desk" className="mt-24" aria-labelledby="desk-title">
      <p className="eyebrow">Sthamly Desk · jaldi aa raha hai</p>
      <h2 id="desk-title" className="mt-2 max-w-3xl font-display text-2xl font-bold tracking-tight sm:text-4xl">
        Vakeel, CA aur students ke liye offline Hindi sahayak
      </h2>
      <p className="mt-4 max-w-2xl text-muted">
        Aapke PDF aur chat aapke computer par hi rehte hain (sirf activation ke samay ek chhota network call). Drafting
        aur analysis me madad deta hai. Ye kanooni ya vitteey salah nahi hai: output ko hamesha professional se verify karo.
      </p>

      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {platforms.map((p) => (
          <div key={p.name} className="panel flex items-center justify-between gap-4 p-5">
            <div>
              <h3 className="font-display text-lg font-semibold">{p.name}</h3>
              <p className="font-mono text-xs text-muted">{p.file} installer</p>
            </div>
            {p.url ? (
              <a 
                className="btn btn-primary" 
                href={p.url} 
                target="_blank" 
                rel="noopener noreferrer" 
                download
              >
                Download
              </a>
            ) : (
              <button type="button" className="btn btn-ghost" disabled>
                Jaldi aa raha hai
              </button>
            )}
          </div>
        ))}
      </div>
      {!win && !mac && (
        <p className="mt-3 text-sm text-muted">
          Installer abhi bana nahi hai, isliye download button nahi hai.{' '}
          {contact ? (
            <a className="text-accent underline underline-offset-2" href={contact} target="_blank" rel="noopener noreferrer">
              Waitlist ke liye contact karo
            </a>
          ) : (
            'Waitlist link abhi set nahi hai.'
          )}
        </p>
      )}

      <h3 className="mt-14 font-display text-xl font-semibold">Aise activate hoga</h3>
      <ol className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => (
          <li key={s.title} className="panel p-4">
            <span className="font-mono text-xs text-accent">0{i + 1}</span>
            <p className="mt-1 font-medium">{s.title}</p>
            <p className="mt-1 text-sm text-muted">{s.text}</p>
          </li>
        ))}
      </ol>

      <h3 className="mt-14 font-display text-xl font-semibold">Ek app, teen desk</h3>
      <p className="mb-5 mt-2 max-w-2xl text-sm text-muted">
        App ek hi hai. Activation key ka profile tay karta hai ki aapko kaunsa desk dikhe. Neeche key badal kar dekho.
      </p>
      <DeskPreview />
    </section>
  )
}
