import './globals.css'

export const metadata = {
  title: 'Sthamly NanoBrain',
  description: 'Hindi-calibrated AI model compression',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="bg-black text-white">
        <nav className="border-b border-gray-800 px-8 py-4 flex gap-6">
          <a href="/" className="font-semibold">Sthamly NanoBrain</a>
          <a href="/benchmark" className="text-gray-400 hover:text-white">Benchmark</a>
        </nav>
        {children}
      </body>
    </html>
  )
}