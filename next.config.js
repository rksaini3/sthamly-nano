/** @type {import('next').NextConfig} */
const nextConfig = {
  // 1. Next.js 16+ Turbopack नियमों के अनुसार बाहरी पैकेजों का ऑप्टिमाइजेशन
  serverExternalPackages: ['@supabase/supabase-js', 'pdf-lib'],

  // 2. वर्सेल क्लीन यूआरएल मैकेनिज्म (Clean URLs): यह 404 रीडायरेक्शन एरर को रोकता है
  trailingSlash: false,
  poweredByHeader: false,

  // 3. Render.sh से SthamlyDesk.zip का असली 100% डायरेक्ट डाउनलोड रीडायरेक्ट्स
  async redirects() {
    // रेंडर द्वारा दिया गया आपका लाइव डायरेक्ट एसेट डाउनलोड लिंक
    const RENDER_ZIP_URL = 'https://onrender.com';

    return [
      {
        source: '/download/windows',
        destination: RENDER_ZIP_URL,
        permanent: false, 
      },
      {
        source: '/download/mac',
        destination: RENDER_ZIP_URL,
        permanent: false,
      },
    ];
  },

  // 4. डबल डाउनलोड मैट्रिक्स सुरक्षा: बैकएंड रूट्स के लिए CORS और हेडर सपोर्ट को कड़ाई से लागू करना
  async headers() {
    return [
      {
        source: '/api/:path*',
        headers: [
          { key: 'Access-Control-Allow-Origin', value: '*' },
          { key: 'Access-Control-Allow-Methods', value: 'GET,POST,OPTIONS' },
          { key: 'Access-Control-Allow-Headers', value: 'Content-Type, Authorization' },
          { key: 'Cache-Control', value: 'no-store, max-age=0, must-revalidate' }
        ],
      },
    ]
  }
}

module.exports = nextConfig
