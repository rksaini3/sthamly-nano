import { NextRequest, NextResponse } from 'next/server'

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const modelName = searchParams.get('model') || 'Unknown-Model'

    // एक साधारण लेकिन प्रामाणिक टेक्स्ट-आधारित PDF या डायनेमिक डेटा स्ट्रीम जेनरेट करना
    const dummyPdfContent = `%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << >> /Contents 4 0 R >>\nendobj\n4 0 obj\n<< /Length 60 >>\nstream\nBT /F1 12 Tf 50 700 Td (Sthamly NanoBrain Audit Report for ${modelName}) Tj ET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f\n0000000010 00000 n\n0000000060 00000 n\n0000000120 00000 n\n0000000210 00000 n\ntrailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n0000000320\n%%EOF`

    const buffer = Buffer.from(dummyPdfContent, 'utf-8')

    // ब्राउज़र को बताना कि यह एक असली डाउनलोड करने योग्य PDF फ़ाइल है
    return new NextResponse(buffer, {
      status: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="sthamly_${modelName.replace(/[^a-zA-Z0-9]/g, '_')}_report.pdf"`,
      },
    })
  } catch (error) {
    return NextResponse.json({ error: 'PDF generation failed' }, { status: 500 })
  }
}
