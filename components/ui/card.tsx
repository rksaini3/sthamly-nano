import type { ReactNode } from 'react'

export default function Card({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`border border-gray-800 rounded-lg p-5 bg-gray-950/40 ${className}`}>
      {children}
    </div>
  )
}