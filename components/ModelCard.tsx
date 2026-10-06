import Card from './ui/card'
import type { Model } from '@/lib/types'
import { kldReductionPct } from '@/lib/verdict'

export default function ModelCard({ model }: { model: Model }) {
  const gains = Object.entries(kldReductionPct(model.results))
  const link = model.download_url ?? (model.hf_repo ? `https://huggingface.co/${model.hf_repo}` : null)
  return (
    <Card>
      <h3 className="text-lg font-semibold text-white">{model.model_name}</h3>
      <p className="text-sm text-gray-400 mb-2">
        {model.quant_type} · {model.size_gb ? `${model.size_gb} GB` : 'size unknown'}
        {model.fp16_size_gb ? ` (FP16 ${model.fp16_size_gb} GB)` : ''}
      </p>
      {gains.length > 0 && (
        <p className="text-sm text-green-400 mb-2">
          Plain Q4 ke muqable KLD kam:{' '}
          {gains.map(([d, g]) => `${d} ${g.toFixed(0)}%`).join(', ')}
        </p>
      )}
      {model.notes && <p className="text-sm text-gray-500 mb-3">{model.notes}</p>}
      {link && (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block text-sm font-medium text-indigo-400 hover:text-indigo-300"
        >
          Download on Hugging Face →
        </a>
      )}
    </Card>
  )
}