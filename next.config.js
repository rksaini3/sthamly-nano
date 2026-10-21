/** @type {import('next').NextConfig} */
const nextConfig = {
  // 1. Next.js 16+ Turbopack नियमों के अनुसार बाहरी पैकेजों का ऑप्टिमाइजेशन
  serverExternalPackages: ['@supabase/supabase-js', 'pdf-lib'],

  // 2. वर्सेल क्लीन यूआरएल मैकेनिज्म (Clean URLs): यह 404 रीडायरेक्शन एरर को रोकता है
  trailingSlash: false,
  poweredByHeader: false,

  // 3. गूगल ड्राइव डायरेक्ट डाउनलोड रीडायरेक्ट्स (बिना किसी क्रेडिट कार्ड झंझट के)
  async redirects() {
    // ⚠️ निर्देश: अपनी Google Drive फ़ाइल के 'Share' लिंक से ID निकालकर यहाँ बदलें
    // उदाहरण: यदि लिंक https://google.com है, तो ID '1A2b3C...' होगी।
    const WINDOWS_DRIVE_ID = 'YOUR_WINDOWS_FILE_ID_HERE'; 
    const MAC_DRIVE_ID = 'YOUR_MAC_FILE_ID_HERE';

    return [
      {
        source: '/download/windows',
        destination: `https://google.com{WINDOWS_DRIVE_ID}`,
        permanent: false, // फ़ाइल अपडेट होने पर कैशे की समस्या से बचने के लिए false रखें
      },
      {
        source: '/download/mac',
        destination: `https://google.com{MAC_DRIVE_ID}`,
        permanent: false,
      },
    ];
  },

  // 4. डबल डाउनलोड मैट्रिक्स सुरक्षा: बैकएंड रूट्स के लिए CORS और हेडर सपोर्ट को कड़ाई से लागू करना
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
