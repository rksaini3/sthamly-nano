/**
 * What the Desk app will look like per activation key. This mirrors the seeds in db/schema_v3.sql
 * (table license_profiles): if you change a label or action there, change it here too.
 * Used only for the design preview on the home page: the app itself is not built yet.
 */
export interface DeskProfile {
  id: 'lawyer' | 'finance' | 'student'
  tab: string
  label: string
  icon: string
  /** first action = the big button, the rest = chips */
  actions: string[]
  disclaimer: string
}

export const DESK_PROFILES: DeskProfile[] = [
  {
    id: 'lawyer',
    tab: 'Vakeel',
    label: 'Sthamly Legal Desk',
    icon: '📎',
    actions: ['Case PDF upload karo', 'Notice ka draft', 'Bail application ka draft'],
    disclaimer: 'Ye drafting aur analysis sahayak hai, kanooni salah nahi. Istemaal se pehle kisi qualified vakeel se verify karwayein.',
  },
  {
    id: 'finance',
    tab: 'CA / Finance',
    label: 'Sthamly Finance Desk',
    icon: '📊',
    actions: ['Ledger upload karo', 'Balance analysis', 'Statement ka saar'],
    disclaimer: 'Ye analysis sahayak hai, vitteey ya tax salah nahi. Kisi bhi hisaab ko istemaal se pehle professional se verify karwayein.',
  },
  {
    id: 'student',
    tab: 'Student',
    label: 'Sthamly Study Desk',
    icon: '📚',
    actions: ['Syllabus sync', 'Notes ka saar', 'Case aur ratios nikaalo'],
    disclaimer: 'Ye padhai me madad ke liye hai. Imtihaan se pehle apni kitaab aur teacher se milaan karein.',
  },
]
