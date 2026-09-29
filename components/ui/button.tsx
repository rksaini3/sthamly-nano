import type { ButtonHTMLAttributes } from 'react'

export default function Button({
  children,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`px-4 py-2 rounded-md bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-500 disabled:opacity-40 ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}