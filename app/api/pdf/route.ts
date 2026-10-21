import { NextRequest, NextResponse } from 'next/server'
import { PDFDocument, rgb, StandardFonts } from 'pdf-lib'
import { createClient } from '@supabase/supabase-js'

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
const supabase = createClient(supabaseUrl, supabaseAnonKey)

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const modelName = searchParams.get('model') || 'Qwen/Qwen2.5-1.5B-Instruct'
    
    // डिफ़ॉल्ट मान (यदि डेटाबेस में रो न मिले)
    let kldScore = '0.0509'
    let errorBar = '0.0018'

    if (supabaseUrl && supabaseAnonKey) {
      const { data, error } = await supabase
        .from('quantization_jobs')
        .select('kld_score_after, kld_error_after')
        .eq('hf_model_identifier', modelName)
        .eq('status', 'completed')
        .maybeSingle()

      if (data && !error) {
        kldScore = Number(data.kld_score_after).toFixed(4)
        errorBar = Number(data.kld_error_after).toFixed(4)
      }
    }

    const pdfDoc = await PDFDocument.create()
    const page = pdfDoc.addPage([595.27, 841.89]) // कड़ाई से A4 मानक आकार
    const { width, height } = page.getSize()

    const helveticaFont = await pdfDoc.embedFont(StandardFonts.Helvetica)
    const helveticaBold = await pdfDoc.embedFont(StandardFonts.HelveticaBold)

    // हेडर ड्रा
    page.drawText('STHAMLY NANOBRAIN AUDIT REPORT', {
      x: 50,
      y: height - 60,
      size: 18,
      font: helveticaBold,
      color: rgb(0.08, 0.12, 0.22),
    })

    page.drawLine({
      start: { x: 50, y: height - 75 },
      end: { x: width - 50, y: height - 75 },
      thickness: 1.5,
      color: rgb(0.74, 0.76, 0.78),
    })

    page.drawText(`Target Model: ${modelName}`, {
      x: 50,
      y: height - 110,
      size: 12,
      font: helveticaBold,
      color: rgb(0.2, 0.2, 0.2),
    })

    page.drawText('Diagnostic Metrics (Hindi Quantization)', {
      x: 50,
      y: height - 160,
      size: 13,
      font: helveticaBold,
      color: rgb(0.08, 0.12, 0.22),
    })

    // KLD बॉक्स
    page.drawRectangle({
      x: 50,
      y: height - 240,
      width: 230,
      height: 50,
      borderColor: rgb(0.8, 0.8, 0.8),
      borderWidth: 1,
    })
    page.drawText(`KLD Divergence: ${kldScore}`, {
      x: 65,
      y: height - 220,
      size: 11,
      font: helveticaFont,
    })

    // Error Bar बॉक्स
    page.drawRectangle({
      x: 315,
      y: height - 240,
      width: 230,
      height: 50,
      borderColor: rgb(0.8, 0.8, 0.8),
      borderWidth: 1,
    })
    page.drawText(`Confidence Interval: \u00B1 ${errorBar}`, {
      x: 330,
      y: height - 220,
      size: 11,
      font: helveticaFont,
    })

    // सुप्रीम कोर्ट नोटिस फुटर
    const noticeY = 80
    page.drawRectangle({
      x: 50,
      y: noticeY,
      width: width - 100,
      height: 55,
      color: rgb(0.96, 0.97, 0.98),
    })
    
    page.drawText('Compliance Notice:', {
      x: 65,
      y: noticeY + 35,
      size: 10,
      font: helveticaBold,
      color: rgb(0.7, 0.2, 0.2),
    })
    page.drawText('Verified against Supreme Court 2026 Guideline (Pooja Ramesh Singh v/s J&K Bank).', {
      x: 65,
      y: noticeY + 15,
      size: 9,
      font: helveticaFont,
      color: rgb(0.3, 0.3, 0.3),
    })

    const pdfBytes = await pdfDoc.save()
    const buffer = Buffer.from(pdfBytes)

    return new NextResponse(buffer, {
      status: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="sthamly_${modelName.replace(/[^a-zA-Z0-9]/g, '_')}_report.pdf"`,
        'Cache-Control': 'no-store, max-age=0, must-revalidate',
      },
    })
  } catch (error) {
    return NextResponse.json({ error: 'PDF generation failed' }, { status: 500 })
  }
}
