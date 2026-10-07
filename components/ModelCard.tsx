import type { Model } from '@/lib/types'
import { kldReductionPct } from '@/lib/verdict'

/** One model as a list row: name, size, the one-line result, and the download link. */
export default function ModelCard({ model }: { model: Model }) {
  const gains = Object.entries(kldReductionPct(model.results))
  const link = model.download_url ?? (model.hf_repo ? `https://huggingface.co/${model.hf_repo}` : null)
  // Audit rows (custom labels) have no "Hindi vs plain" gain: say how many quants were measured instead.
  const measured = Object.values(model.results ?? {})[0]
  const nQuants = measured ? Object.keys(measured).length : 0

  return (
    <li className="py-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h3 className="font-display font-semibold text-lg leading-snug">{model.model_name}</h3>
        <p className="text-sm text-muted">
          {model.quant_type} · {model.size_gb ? `${model.size_gb} GB` : 'size pata nahi'}
          {model.fp16_size_gb ? ` (FP16 ${model.fp16_size_gb} GB)` : ''}
        </p>
        {gains.length > 0 ? (
          <p className="text-sm mt-1">
            Plain Q4 ke muqable KLD kam: {gains.map(([d, g]) => `${d} ${g.toFixed(0)}%`).join(', ')}
          </p>
        ) : (
          nQuants > 0 && <p className="text-sm mt-1">{nQuants} quant FP16 ke muqable naape gaye</p>
        )}
        {model.notes && <p className="text-sm text-muted mt-1">{model.notes}</p>}
      </div>
      {link && (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 inline-flex items-center justify-center rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium hover:border-ink"
        >
          Hugging Face par kholo
        </a>
      )}
    </li>
  )
}