/** @type {import('next').NextConfig} */
const nextConfig = {
  // 1. Next.js 16+ Turbopack नियमों के अनुसार बाहरी पैकेजों का ऑप्टिमाइजेशन
  serverExternalPackages: ['@supabase/supabase-js', 'pdf-lib'],

  // 2. वर्सेल क्लीन यूआरएल मैकेनिज्म (Clean URLs): यह 404 रीडायरेक्शन एरर को रोकता है
  trailingSlash: false,
  poweredByHeader: false,

  // 3. GitHub Releases से SthamlyDesk.zip का सीधा और सुरक्षित डाउनलोड रीडायरेक्ट्स
  async redirects() {
    // स्क्रीनशॉट के अनुसार सटीक रिलीज पाथ और ज़िप फ़ाइल का नाम
    const GITHUB_ZIP_URL = 'https://github.com';

    return [
      {
        source: '/download/windows',
        destination: GITHUB_ZIP_URL, // सीधे ज़िप फ़ाइल पर भेजेगा
        permanent: false, 
      },
      {
        source: '/download/mac',
        destination: GITHUB_ZIP_URL, // सीधे ज़िप फ़ाइल पर भेजेगा
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
