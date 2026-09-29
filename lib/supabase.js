import { createClient } from '@supabase/supabase-js'

// Public, read-only client — safe for the browser (protected by RLS, see db/schema.sql)
export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
)