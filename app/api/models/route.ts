import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'

// बैकएंड कतार (Queue Scheduler) को बिना किसी बाधा के चलाने के लिए सेवा रोल कुंजी (Service Role Key) का उपयोग
const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!
)

/**
 * GET: पुराना डेटा और लीडरबोर्ड दिखाता है।
 * वेबसाइट पर आने वाला कोई भी व्यक्ति इसे बिना लॉग-इन के देख सकता है।
 */
export async function GET() {
  try {
    const { data, error } = await supabaseAdmin
      .from('quantization_jobs') // आपके DB Schema की सही टेबल का नाम
      .select('*')
      .order('created_at', { ascending: false })

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 })
    }
    return NextResponse.json(data)
  } catch (err) {
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 })
  }
}

/**
 * POST: नया फ्री ऑडिट (Free Audit) शुरू करता है।
 * इसे आम पब्लिक यूज़र्स बिना किसी 'x-admin-key' या एडमिन लॉक के एक्सेस कर सकते हैं।
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    
    // इनपुट चेक: सुनिश्चित करें कि यूजर ने मॉडल आईडी प्रदान की है
    if (!body.hf_model_identifier) {
      return NextResponse.json(
        { error: 'Hugging Face model identifier (hf_model_identifier) की जानकारी गायब है।' }, 
        { status: 400 }
      )
    }

    // डेटाबेस में 'pending' स्थिति के साथ नया काम दर्ज करें ताकि worker.py इसे तुरंत उठा सके
    const { data, error } = await supabaseAdmin
      .from('quantization_jobs')
      .insert({
        hf_model_identifier: body.hf_model_identifier.trim(),
        status: 'pending' // यहाँ से बैकएंड की प्रोग्रेस टेलीमेट्री लूप ट्रिगर होगी
      })
      .select()

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 })
    }

    // नया बना हुआ जॉब डेटा वापस फ्रंटेंड को भेजें (201 Created)
    return NextResponse.json(data[0], { status: 201 })

  } catch (err) {
    return NextResponse.json({ error: 'रिक्वेस्ट का JSON फॉर्मेट या डेटा सही नहीं है।' }, { status: 400 })
  }
}
