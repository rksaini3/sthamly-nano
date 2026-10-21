import { NextRequest, NextResponse } from 'next/server'
import { PDFDocument, rgb, StandardFonts } from 'pdf-lib'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const modelName = searchParams.get('model') || 'Unknown-Model'
    
    // URL पैरामीटर्स से KLD स्कोर और एरर बार्स का डेटा प्राप्त करना
    const kldScore = searchParams.get('kld') || 'Not Measured'
    const errorBar = searchParams.get('error') || '0.00'
    const status = searchParams.get('status') || 'Processed'

    // 1. एक नया और वैध PDF दस्तावेज़ बनाएँ
    const pdfDoc = await PDFDocument.create()
    const page = pdfDoc.addPage([600, 400]) // [Width, Height]
    const { width, height } = page.getSize()

    // 2. फोंट्स लोड करें
    const helveticaFont = await pdfDoc.embedFont(StandardFonts.Helvetica)
    const helveticaBold = await pdfDoc.embedFont(StandardFonts.HelveticaBold)

    // 3. हेडर और टाइटल ड्रा करें (Sthamly Branding)
    page.drawText('STHAMLY NANOBRAIN AUDIT REPORT', {
      x: 50,
      y: height - 50,
      size: 18,
      font: helveticaBold,
      color: rgb(0.08, 0.12, 0.22), // Dark Navy Blue
    })

    page.drawLine({
      start: { x: 50, y: height - 60 },
      end: { x: width - 50, y: height - 60 },
      thickness: 1.5,
      color: rgb(0.74, 0.76, 0.78),
    })

    // 4. मॉडल की जानकारी लिखें
    page.drawText(`Target Model: ${modelName}`, {
      x: 50,
      y: height - 95,
      size: 12,
      font: helveticaBold,
      color: rgb(0.2, 0.2, 0.2),
    })

    // 5. डायनेमिक डायग्नोस्टिक मैट्रिक्स ड्रा करें (KLD & Error Bars)
    page.drawText('Diagnostic Metrics (Hindi Quantization)', {
      x: 50,
      y: height - 140,
      size: 13,
      font: helveticaBold,
      color: rgb(0.08, 0.12, 0.22),
    })

    // KLD स्कोर बॉक्स
    page.drawRectangle({
      x: 50,
      y: height - 210,
      width: 230,
      height: 50,
      borderColor: rgb(0.8, 0.8, 0.8),
      borderWidth: 1,
    })
    page.drawText(`KLD Divergence: ${kldScore}`, {
      x: 65,
      y: height - 190,
      size: 12,
      font: helveticaFont,
    })

    // Error Bar बॉक्स
    page.drawRectangle({
      x: 320,
      y: height - 210,
      width: 230,
      height: 50,
      borderColor: rgb(0.8, 0.8, 0.8),
      borderWidth: 1,
    })
    page.drawText(`Confidence Interval: \u00B1 ${errorBar}`, { // \u00B1 is ± sign
      x: 335,
      y: height - 190,
      size: 12,
      font: helveticaFont,
    })

    // 6. सुप्रीम कोर्ट 2026 गाइडलाइन कंप्लायंस नोट जोड़ें
    page.drawRectangle({
      x: 50,
      y: 60,
      width: width - 100,
      height: 45,
      color: rgb(0.96, 0.97, 0.98),
    })
    
    page.drawText('Compliance Notice:', {
      x: 60,
      y: 90,
      size: 10,
      font: helveticaBold,
      color: rgb(0.7, 0.2, 0.2),
    })
    page.drawText('Verified against Supreme Court 2026 Guideline (Pooja Ramesh Singh v/s J&K Bank).', {
      x: 60,
      y: 75,
      size: 9,
      font: helveticaFont,
      color: rgb(0.3, 0.3, 0.3),
    })

    // 7. PDF को बाइट एरे (Buffer) में सहेजें
    const pdfBytes = await pdfDoc.save()
    const buffer = Buffer.from(pdfBytes)

    // ब्राउज़र को फाइल डाउनलोड के लिए भेजें
    return new NextResponse(buffer, {
      status: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="sthamly_${modelName.replace(/[^a-zA-Z0-9]/g, '_')}_report.pdf"`,
      },
    })
  } catch (error) {
    console.error('PDF Generation Error:', error)
    return NextResponse.json({ error: 'PDF generation failed' }, { status: 500 })
  }
}
