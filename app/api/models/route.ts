import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'

// 1. डेटाबेस कतार (Queue Scheduler) को बिना किसी रुकावट के चलाने के लिए Supabase एडमिन क्लाइंट का निर्माण
// इसमें आपके .env फ़ाइल से प्रेषित URL और सर्विस रोल कुंजी (Service Role Key) का कड़ाई से उपयोग किया गया है।
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY

if (!supabaseUrl || !supabaseServiceKey) {
  console.error("CRITICAL ERROR: Supabase environment variables missing inside api/models/route.ts")
}

const supabaseAdmin = createClient(
  supabaseUrl || '',
  supabaseServiceKey || '',
  {
    auth: {
      persistSession: false,
      autoRefreshToken: false
    }
  }
)

/**
 * ============================================================================
 * GET: लीडरबोर्ड और पुराने सभी क्वांटाइजेशन डेटा की विस्तृत सूची (Public Access)
 * ============================================================================
 * वेबसाइट पर आने वाला कोई भी आम व्यक्ति इसे बिना लॉग-इन या बिना किसी सीक्रेट टोकन के देख सकता है।
 * यह आपके रिपॉजिटरी की मुख्य 'quantization_jobs' टेबल से डेटा को अवरोही (Descending) क्रम में खींचता है।
 */
export async function GET() {
  try {
    // डेटाबेस से रिकॉर्ड्स का चयन करना
    const { data: jobsData, error: dbFetchError } = await supabaseAdmin
      .from('quantization_jobs') // आपके DB Schema की प्रामाणिक टेबल का नाम
      .select('*')
      .order('created_at', { ascending: false })

    // यदि डेटाबेस कोई एरर फेंकता है तो उसे सुरक्षित हैंडल करना
    if (dbFetchError) {
      return NextResponse.json(
        { error: `Database fetch failed: ${dbFetchError.message}` }, 
        { status: 500 }
      )
    }

    // यदि टेबल पूरी तरह खाली है तो खाली एरे भेजना
    if (!jobsData) {
      return NextResponse.json([], { status: 200 })
    }

    // सफ़ल रिस्पॉन्स डेटा भेजना
    return NextResponse.json(jobsData, { status: 200 })

  } catch (globalCatchError: any) {
    return NextResponse.json(
      { error: 'Internal Server Error: GET operation crashed.', details: globalCatchError?.message || '' }, 
      { status: 500 }
    )
  }
}

/**
 * ============================================================================
 * POST: नया फ्री ऑडिट (Free Hindi Quality Audit) शुरू करने का मुख्य हैंडलर
 * ============================================================================
 * इसे आम पब्लिक यूज़र्स बिना किसी 'x-admin-key' या लाइसेंस लॉक के एक्सेस कर सकते हैं।
 * यह फ्रंटएंड से आने वाले डेटा को आपके डेटाबेस में 'pending' स्थिति के साथ दर्ज करता है।
 */
export async function POST(req: NextRequest) {
  try {
    // 1. आने वाली रिक्वेस्ट की बॉडी (JSON) को पार्स करना
    const requestBody = await req.json()
    
    // 2. हाइब्रिड डेटा मैपिंग: फ्रंटएंड के नए वैल्यू मॉडल और पुराने स्कीमा दोनों को सपोर्ट करना
    // यह फ्रंटएंड के 'value' (hf_model_identifier) और 'modelId' दोनों को सुरक्षित रूप से कलेक्ट करता है।
    const targetModelIdentifier = requestBody.hf_model_identifier || requestBody.modelId

    // 3. इनपुट चेक: यदि यूजर ने मॉडल का नाम नहीं डाला या फील्ड खाली छोड़ दी
    if (!targetModelIdentifier || typeof targetModelIdentifier !== 'string' || targetModelIdentifier.trim() === '') {
      return NextResponse.json(
        { 
          error: 'Hugging Face model identifier (hf_model_identifier) ki jaankari gayab hai। Kripya sahi owner/model-name likhein.' 
        }, 
        { status: 400 }
      )
    }

    const cleanModelName = targetModelIdentifier.trim()

    // 4. डेटाबेस में 'pending' स्थिति के साथ नया रो (Row) दर्ज करना 
    // यहीं से बैकएंड की प्रोग्रेस टेलीमेट्री लूप (worker.py) ट्रिगर होगी
    const { data: insertedJobRecord, error: dbInsertError } = await supabaseAdmin
      .from('quantization_jobs')
      .insert([
        {
          hf_model_identifier: cleanModelName,
          status: 'pending', // डिफ़ॉल्ट रूप से काम पेंडिंग कतार में जाएगा
          created_at: new Date().toISOString()
        }
      ])
      .select()

    // 5. यदि डेटाबेस में डेटा इन्सर्ट करते समय कोई रुकावट या RLS उल्लंघन होता है
    if (dbInsertError) {
      return NextResponse.json(
        { error: `Supabase database insertion error: ${dbInsertError.message}` }, 
        { status: 500 }
      )
    }

    // 6. यदि डेटा सफलतापूर्वक इन्सर्ट नहीं हो पाया या एरे खाली आया
    if (!insertedJobRecord || insertedJobRecord.length === 0) {
      return NextResponse.json(
        { error: 'Database me job record register nahi ho saka। Kripya thodi der baad prayas karein.' }, 
        { status: 500 }
      )
    }

    // 7. नया बना हुआ जॉब डेटा (पहला ऑब्जेक्ट) फ्रंटएंड को वापस भेजना ताकि आपका प्रोग्रेस काउंटर एक्टिव हो सके
    // यह स्टेटस 201 (Created) के साथ रिस्पॉन्स डिलीवर करता है
    return NextResponse.json(insertedJobRecord[0], { status: 201 })

  } catch (globalCatchError: any) {
    // 8. यदि इनपुट डेटा का JSON फॉर्मेट ही खराब है या सर्वर क्रैश होता है
    return NextResponse.json(
      { 
        error: 'Request ka JSON format sahi nahi hai ya network handshake asafal raha.',
        details: globalCatchError?.message || ''
      }, 
      { status: 400 }
    )
  }
}
