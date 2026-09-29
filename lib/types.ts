export interface Model {
  id: string
  model_name: string
  hf_repo: string
  quant_type: string
  size_gb: number | null
  fp16_size_gb: number | null
  hindi_score_fp16: number | null
  hindi_score_standard_q4: number | null
  hindi_score_hindi_q4: number | null
  download_url: string | null
  notes: string | null
  created_at: string
}