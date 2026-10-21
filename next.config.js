/** @type {import('next').NextConfig} */
const nextConfig = {
  // 1. Next.js 16+ Turbopack नियमों के अनुसार बाहरी पैकेजों का ऑप्टिमाइजेशन
  serverExternalPackages: ['@supabase/supabase-js', 'pdf-lib'],

  // 2. वर्सेल क्लीन यूआरएल मैकेनिज्म (Clean URLs): यह 404 रीडायरेक्शन एरर को रोकता है
  trailingSlash: false,
  poweredByHeader: false,

  // 3. क्लाउडफ्लेयर R2 रीडायरेक्ट्स: 404 एरर को रोकने और भारी इंस्टॉलर को R2 से डाउनलोड कराने के लिए
  async redirects() {
    // यहाँ अपनी Cloudflare R2 की Public URL या Custom Domain डालें
    const R2_URL = 'https://sthamly.in'; 

    return [
      {
        source: '/download/windows',
        destination: `${R2_URL}/Sthamly_Setup.exe`,
        permanent: false, // अस्थाई (false) रखें ताकि फाइल अपडेट होने पर कैशे की समस्या न हो
      },
      {
        source: '/download/mac',
        destination: `${R2_URL}/Sthamly_Setup.dmg`,
        permanent: false,
      },
    ];
  },

  // 4. अगर भविष्य में R2 से इमेज या एसेट्स वेबसाइट पर दिखाने हों तो रिमोट पैटर्न कॉन्फ़िगरेशन
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'download.sthamly.in', // अपना R2 कस्टम डोमेन यहाँ बदलें
        port: '',
        pathname: '/assets/**',
      },
    ],
  },

  // 5. डबल डाउनलोड मैट्रिक्स सुरक्षा: बैकएंड रूट्स के लिए CORS और हेडर सपोर्ट को कड़ाई से लागू करना
  async headers() {
    return [
      {
        // आपके सभी बैकएंड एपीआई पाथ्स को टारगेट करना
        source: '/api/:path*',
        headers: [
          { key: 'Access-Control-Allow-Origin', value: '*' },
          { key: 'Access-Control-Allow-Methods', value: 'GET,POST,OPTIONS' },
          { key: 'Access-Control-Allow-Headers', value: 'Content-Type, Authorization' },
          // ब्राउज़र को फाइलें कैशे करने से रोकना ताकि पुराना ब्लैंक PDF दोबारा न आए
          { key: 'Cache-Control', value: 'no-store, max-age=0, must-revalidate' }
        ],
      },
    ]
  }
}

module.exports = nextConfig
