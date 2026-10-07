// Plain-language names for the test sets that scripts/trl4_bench.py and scripts/audit.py write.
const DOMAIN_NAMES: Record<string, string> = {
  wiki: 'Hindi Wikipedia',
  sangraha: 'Sangraha (verified Hindi text)',
  hinglish: 'Hinglish',
}

/** Friendly name for a test set key; unknown keys are shown as they are. */
export function domainName(key: string): string {
  return DOMAIN_NAMES[key] ?? key
}