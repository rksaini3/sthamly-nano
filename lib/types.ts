/** [mean, +/- error] as printed by llama-perplexity */
export type Metric = [number, number]

export interface VariantResult {
  kld: Metric // KL divergence vs FP16, lower = better
  rms: Metric // RMS change in token probability (%), lower = better
  top: Metric // same-top-token rate vs FP16 (%), higher = better
}

/** domain -> variant -> measurements. Shape written by scripts/trl4_bench.py */
export type Results = Record<string, Record<string, VariantResult>>

export interface Model {
  id: string
  model_name: string
  hf_repo: string
  base_model: string | null
  quant_type: string
  size_gb: number | null
  fp16_size_gb: number | null
  download_url: string | null
  results: Results | null
  notes: string | null
  created_at: string
}