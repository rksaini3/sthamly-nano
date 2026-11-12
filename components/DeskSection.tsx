const STEPS = [
  { title: 'App kholo', text: 'Computer par Sthamly Legal Desk chalao.' },
  { title: 'Activation key daalo', text: 'STH-XXXX-XXXX-XXXX-XXXX jo aapko di gayi hai.' },
  { title: 'Ek chhota check', text: 'Key ki jaanch hoti hai. Bas yahi ek network call hai.' },
  { title: 'Phir offline', text: 'Network ki zaroorat nahi. PDF aur chat aapke computer par hi rehte hain.' },
]

export default function DeskSection() {
  const win = (process.env.NEXT_PUBLIC_DESK_WIN_URL || '').trim()
  const contact = (process.env.NEXT_PUBLIC_CONTACT_URL || '').trim()

  return (
    <section id="desk" className="mt-24" aria-labelledby="desk-title">
      <p className="eyebrow">Windows pehle</p>
      <h2 id="desk-title" className="mt-2 max-w-3xl font-display text-2xl font-bold tracking-tight sm:text-4xl">
        Vakeel ke liye offline Hindi sahayak
      </h2>
      <p className="mt-4 max-w-2xl text-muted">
        Aapki PDF aur chat aapke computer par hi rehte hain. Activation ke samay sirf ek chhota network call hota hai.
        Ye drafting aur analysis me madad deta hai. Ye kanooni salah nahi hai: output ko kisi qualified vakeel se verify karwayein.
      </p>

      <div className="panel mt-8 flex items-center justify-between gap-4 p-5">
        <div>
          <h3 className="font-display text-lg font-semibold">Windows</h3>
          <p className="font-mono text-xs text-muted">.exe installer</p>
        </div>
        {win ? (
          <a className="btn btn-primary" href={win} target="_blank" rel="noopener noreferrer" download>
            Download
          </a>
        ) : (
          <button type="button" className="btn btn-ghost" disabled>
            Jaldi aa raha hai
          </button>
        )}
      </div>
      {!win && (
        <p className="mt-3 text-sm text-muted">
          Installer abhi taiyaar nahi hai.{' '}
          {contact ? (
            <a className="underline underline-offset-2" href={contact} target="_blank" rel="noopener noreferrer">
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
            <span className="font-mono text-xs text-brand">0{i + 1}</span>
            <p className="mt-1 font-medium">{s.title}</p>
            <p className="mt-1 text-sm text-muted">{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  )
}
