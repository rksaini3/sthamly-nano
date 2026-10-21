/** @type {import('next').NextConfig} */
const nextConfig = {
  serverExternalPackages: ['@supabase/supabase-js', 'pdf-lib'],
  trailingSlash: false,
  poweredByHeader: false,

  // अब आपकी वेबसाइट खुद ही फ़ाइल को अपने अंदर से डाउनलोड करवाएगी
  async redirects() {
    return [
      {
        source: '/download/windows',
        destination: '/SthamlyDesk.zip', // यह सीधे public फ़ोल्डर से फ़ाइल उठाएगा
        permanent: false, 
      },
      {
        source: '/download/mac',
        destination: '/SthamlyDesk.zip',
        permanent: false,
      },
    ];
  },

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
