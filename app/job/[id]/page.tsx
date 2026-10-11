import { notFound } from 'next/navigation'
import JobView from '@/components/JobView'
import Main from '@/components/Main'
import { isUuid } from '@/lib/jobs'

export const metadata = {
  title: 'Audit job: Sthamly NanoBrain',
  robots: { index: false, follow: false }, // job pages are private links, not content to index
}

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  if (!isUuid(id)) notFound()
  return (
    <Main>
      <JobView id={id} />
    </Main>
  )
}
