/** @type {import('next').NextConfig} */
const nextConfig = {
  // 1. Next.js 16+ Turbopack नियमों के अनुसार बाहरी पैकेजों का ऑप्टिमाइजेशन
  serverExternalPackages: ['@supabase/supabase-js', 'pdf-lib'],

  // 2. वर्सेल क्लीन यूआरएल मैकेनिज्म (Clean URLs)
  trailingSlash: false,
  poweredByHeader: false,

  // 3. गिटहब का ऑफिशियल रॉ डायरेक्ट डाउनलोड इंजन (यह कभी फेल नहीं होगा)
  async redirects() {
    return [
      {
        source: '/download/windows',
        destination: 'https://githubusercontent.com',
        permanent: false, 
      },
      {
        source: '/download/mac',
        destination: 'https://githubusercontent.com',
        permanent: false,
      },
    ];
  },

  // 4. बैकएंड रूट्स के लिए CORS और हेडर सपोर्ट
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
