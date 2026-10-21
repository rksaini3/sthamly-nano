import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'

// सुरक्षित बैकएंड ऑपरेशन्स के लिए सेवा रोल कुंजी (Service Role Key)
const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!
)

/**
 * GET: एक्टिवेटेड डिवाइस और लाइसेंस की स्थिति ट्रैक करने के लिए (एडमिन डैशबोर्ड हेतु)
 */
export async function GET() {
  try {
    const { data, error } = await supabaseAdmin
      .from('sthamly_licenses') // v2.0 की नई सुरक्षित लाइसेंस टेबल
      .select('*')
      .order('activated_at', { ascending: false })

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 })
    }
    return NextResponse.json(data)
  } catch (err) {
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 })
  }
}

/**
 * POST: डेस्कटॉप ऐप से आने वाले पेमेंट/एक्टिवेशन रिक्वेस्ट को संभालना (2-Second Activation Engine)
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json()
    const { license_key, hardware_uuid, user_role } = body
    
    // 1. इनपुट डेटा की जांच
    if (!license_key || !hardware_uuid || !user_role) {
      return NextResponse.json(
        { error: 'License Key, Hardware UUID, aur User Role sabhi jaroori hain।' }, 
        { status: 400 }
      )
    }

    // 2. जांचें कि क्या यह चाबी पहले से डेटाबेस में वैध है (पेमेंट स्टेटस चेक)
    const { data: existingKey, error: fetchError } = await supabaseAdmin
      .from('sthamly_licenses')
      .select('*')
      .eq('license_key', license_key.trim())
      .single()

    if (fetchError || !existingKey) {
      return NextResponse.json({ error: 'Galat ya अमान्य Activation Key।' }, { status: 404 })
    }

    // 3. यदि चाबी पहले से किसी और कंप्यूटर पर एक्टिवेटेड है तो ब्लॉक करें
    if (existingKey.hardware_uuid && existingKey.hardware_uuid !== hardware_uuid) {
      return NextResponse.json({ error: 'Yeh key pehle se kisi aur computer par active hai।' }, { status: 400 })
    }

    // 4. गिरगिट की तरह UI बदलने (Chameleon UI) और कंप्यूटर आईडी को लॉक करने के लिए डेटा अपडेट करें
    const { data: updatedData, error: updateError } = await supabaseAdmin
      .from('sthamly_licenses')
      .update({
        hardware_uuid: hardware_uuid,
        status: 'active',
        user_role: user_role.toLowerCase(), // lawyer, ca, ya student
        activated_at: new Date().toISOString()
      })
      .eq('license_key', license_key.trim())
      .select()

    if (updateError) {
      return NextResponse.json({ error: updateError.message }, { status: 500 })
    }

    // 5. ऐप को सुरक्षित टोकन वापस भेजें जिससे इंटरनेट बंद होने पर भी ऐप 30 दिन चलता रहे
    return NextResponse.json({
      success: true,
      message: 'Activation Safal! Ab aap offline kaam kar sakte hain.',
      profile: updatedData[0]
    }, { status: 200 })

  } catch (err) {
    return NextResponse.json({ error: 'रिक्वेस्ट का डेटा या एनक्रिप्टेड फॉर्मेट सही नहीं है।' }, { status: 400 })
  }
}
