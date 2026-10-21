/** @type {import('next').NextConfig} */
const nextConfig = {
  // 1. Turbopack और सर्विंग आर्किटेक्चर के लिए सर्वर कंपोनेंट्स को कड़ाई से ऑप्टिमाइज़ करना
  experimental: {
    serverComponentsExternalPackages: ['@supabase/supabase-js'],
  },

  // 2. वर्सेल क्लीन यूआरएल मैकेनिज्म (Clean URLs): यह 404 रीडायरेक्शन एरर को रोकता है
  trailingSlash: false,
  poweredByHeader: false,

  // 3. डबल डाउनलोड मैट्रिक्स सुरक्षा: बैकएंड रूट्स के लिए CORS और हेडर सपोर्ट को कड़ाई से लागू करना
  async headers() {
    return [
      {
        // आपके दोनों नए बैकएंड एपीआई पाथ्स को कड़ाई से टारगेट करना
        source: '/api/:path*',
        headers: [
          { key: 'Access-Control-Allow-Origin', value: '*' },
          { key: 'Access-Control-Allow-Methods', value: 'GET,POST,OPTIONS' },
          { key: 'Access-Control-Allow-Headers', value: 'Content-Type, Authorization' },
          // ब्राउज़र को फाइलें कैशे करने से रोकना ताकि 404 दोबारा न आए
          { key: 'Cache-Control', value: 'no-store, max-age=0, must-revalidate' }
        ],
      },
    ]
  }
}

module.exports = nextConfig
