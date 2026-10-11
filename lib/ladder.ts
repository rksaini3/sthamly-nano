// The commercial ladder from docs/PLAN.md. Edit prices / wording here; the home page reads this file.
// Prices are starting proposals: confirm them with real customers before treating them as final.
export interface LadderStep {
  step: string
  name: string
  what: string
  price: string
  /** small print under the step */
  note?: string
}

export const LADDER: LadderStep[] = [
  {
    step: 'Step 0',
    name: 'Public Hindi Quant Report',
    what: 'Leaderboard par measured numbers + Hugging Face par ek model. Koi sign-up nahi.',
    price: 'Free',
  },
  {
    step: 'Step 1',
    name: 'Free Hindi audit (aapka model)',
    what: 'Public Hugging Face model ka plain Q4, usi FP16 ke muqable do Hindi test sets par naapkar report, error bars ke saath. Aapke system me kuch badalna nahi.',
    price: 'Free (din me 3 tak)',
  },
  {
    step: 'Step 2',
    name: 'Calibrated Q4 (ek model)',
    what: 'Hindi, English aur Mixed imatrix teeno naapkar jo best nikle wo. Fayda tabhi likha jaata hai jab naap use saaf dikhaye.',
    price: '₹399 per model',
    note: 'Shuruaati price. GST, payment fee aur GPU kharch ka hisaab abhi khula hai; shuru me payment haath se (UPI).',
  },
  {
    step: 'Step 3',
    name: 'Hindi Audit Report (PDF)',
    what: 'Aapke quantized model ka PDF: FP16 se KLD (error bars ke saath) aur model ki seemaayein. Aapke system me kuch badalna nahi.',
    price: '₹3,000 – ₹5,000',
  },
  {
    step: 'Step 4',
    name: 'Full Package',
    what: 'Audit + imatrix calibration (Hindi, English, Mixed ko naapkar best chuna) + compression + speed report.',
    price: '₹15,000 (one-time)',
  },
  {
    step: 'Step 5',
    name: 'Retainer',
    what: 'Naya model aane par dobara compress, calibrate aur benchmark. Baad me, jab Step 4 ke customers ho jayein.',
    price: '₹5,000 / month',
  },
]

// Shown on the home page so nobody has to guess what we do NOT claim.
export const NOT_CLAIMED = [
  'Hindi text se calibrate karna English se behtar hai: abhi saabit nahi (Qwen2.5-1.5B par dono lagbhag barabar).',
  'Real Hindi task (QA, summary) par quality: abhi naapi nahi gayi, sirf KLD proxy hai.',
  'Imatrix ka fayda har model par hota hai: sirf Qwen2.5-1.5B par naapa hai, doosre models par alag ho sakta hai.',
  'Koi number bina measurement ke nahi dikhaya jata.',
  'Desk app abhi bana nahi hai. Aane par bhi: drafting aur analysis sahayak, kanooni ya vitteey salah nahi; output verify karna zaroori hai.',
]
