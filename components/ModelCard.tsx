import Card from './ui/card'
import type { Model } from '@/lib/types'

export default function ModelCard({ model }: { model: Model }) {
  return (
    <Card>
      <h3 className="text-lg font-semibold text-white">{model.model_name}</h3>
      <p className="text-sm text-gray-400 mb-2">
        {model.quant_type} · {model.size_gb ? `${model.size_gb} GB` : 'size unknown'}
      </p>
      {model.notes && <p className="text-sm text-gray-500 mb-3">{model.notes}</p>}
      {model.download_url && (
        <a
          href={model.download_url}
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