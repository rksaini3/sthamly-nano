import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'

// डेटाबेस को कनेक्ट करना
const url = process.env.NEXT_PUBLIC_SUPABASE_URL
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY

export async function POST(req: NextRequest) {
  try {
    if (!url || !serviceKey) {
      return NextResponse.json({ ok: false, code: 'config_missing', message: 'Database configuration missing.' }, { status: 500 })
    }

    const sb = createClient(url, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } })
    
    // फ्रंटएंड से भेजे गए मॉडल का नाम पढ़ना
    const { model } = await req.json()
    if (!model) {
      return NextResponse.json({ ok: false, code: 'invalid_model', message: 'Model name missing.' }, { status: 400 })
    }

    // Supabase की 'jobs' टेबल में नया queued रिकॉर्ड डालना
    const { data, error } = await sb
      .from('jobs')
      .insert([
        { 
          hf_model: model, 
          status: 'queued', 
          progress: 0,
          result: { kind: 'audit', sizes_gb: {}, results: {} }
        }
      ])
      .select()
      .single()

    if (error) {
      // अगर एक मॉडल का जॉब पहले से चल रहा है (Unique Index Violation)
      if (error.code === '23505') {
        return NextResponse.json({ ok: false, code: 'already_running', message: 'Is model ka audit pehle se chal raha hai.' }, { status: 400 })
      }
      return NextResponse.json({ ok: false, code: 'db_error', message: error.message }, { status: 500 })
    }

    // सफलता का रिस्पॉन्स भेजना ताकि प्रोग्रेस बार चालू हो सके
    return NextResponse.json({ ok: true, id: data.id })

  } catch (err: any) {
    return NextResponse.json({ ok: false, code: 'server_error', message: err.message }, { status: 500 })
  }
}
