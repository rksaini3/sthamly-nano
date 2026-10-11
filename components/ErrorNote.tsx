/** Shown when data could not be loaded. Says what happened and what to do, without crashing the page. */
export default function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="mb-8 rounded-md border border-bad/40 bg-bad/5 px-4 py-3 text-sm">
      <p className="font-medium text-bad">Data load nahi hua</p>
      <p className="text-muted mt-0.5">{message} Thodi der baad page dobara kholo.</p>
    </div>
  )
}
