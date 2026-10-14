import { normalizeModelId, type JobData } from '@/lib/jobs'

// आउटपुट के लिए डेटा का प्रकार (Types) तय करना
export type CreateResult = { ok: true; id: string; deduped: boolean } | { ok: false; code: string }
export type FetchResult = { ok: true; job: JobData | null } | { ok: false; message: string }

/**
 * createAuditJob: नया फ्री ऑडिट शुरू करता है।
 * अब यह कोड सीधे हमारे बनाए गए सुरक्षित API राऊट (/api/models) को कॉल करेगा।
 */
export async function createAuditJob(input: string): Promise<CreateResult> {
  // 1. यूजर के इनपुट (मॉडल आईडी) को साफ़ और सही फॉर्मेट में लाना
  const model = normalizeModelId(input)
  if (!model) return { ok: false, code: 'invalid_model' }

  try {
    // 2. सीधे हमारे Next.js API राऊट पर POST रिक्वेस्ट भेजना
    const response = await fetch('/api/models', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        hf_model_identifier: model.trim() // हमारे DB Schema का सही कॉलम नाम
      })
    })

    const data = await response.json()

    // 3. अगर सर्वर कोई एरर देता है, तो उसे फ्रंटेंड को बताना
    if (!response.ok) {
      return { ok: false, code: data.error || 'failed' }
    }

    // 4. नया बना हुआ जॉब डेटा निकालना (डेटाबेस एरे या ऑब्जेक्ट के रूप में हो सकता है)
    const job = Array.isArray(data) ? data[0] : data
    if (!job || !job.id) {
      return { ok: false, code: 'bad_response' }
    }

    // 5. सफलता का रिपॉन्स भेजना ताकि फ्रंटेंड तुरंत /job/[id] पेज पर रीडायरेक्ट हो सके
    return { ok: true, id: job.id, deduped: false }

  } catch (err) {
    // नेटवर्क फेल होने की स्थिति में एरर हैंडलिंग
    return { ok: false, code: 'network_error' }
  }
}

/**
 * fetchJob: किसी एक जॉब की लाइव स्थिति (Status) डेटाबेस से मँगाना।
 */
export async function fetchJob(id: string): Promise<FetchResult> {
  try {
    // हमारे एपीआई राऊट से सभी जॉब्स की स्थिति मँगाना
    const response = await fetch('/api/models')
    if (!response.ok) {
      return { ok: false, message: 'Data fetch करने में असमर्थ।' }
    }

    const jobs: JobData[] = await response.json()
    // लिस्ट में से इस विशिष्ट (Specific) आईडी वाले जॉब को ढूँढना
    const targetJob = jobs.find((j: any) => j.id === id)

    return { ok: true, job: targetJob || null }
  } catch (err) {
    return { ok: false, message: 'नेटवर्क कनेक्शन बाधित हुआ है।' }
  }
}
