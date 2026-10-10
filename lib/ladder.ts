export interface Rung {
  name: string
  what: string
  price: string
  /** small print shown under the rung */
  note?: string
}

/** What a visitor can take, cheapest first. Prices are a starting point, to be settled with real customers. */
export const LADDER: Rung[] = [
  {
    name: 'Public Hindi Quant Report',
    what: 'Quantized models FP16 se Hindi par kitna door hain: naapa hua, error bars ke saath. Kholkar padho.',
    price: 'Free',
  },
  {
    name: 'Free Hindi audit (aapka model)',
    what: 'Public Hugging Face model ka plain Q4 FP16 ke muqable Hindi par naapkar report. Aapke system me kuch badalna nahi.',
    price: 'Free (din me 3 tak)',
  },
  {
    name: 'Calibrated Q4 (self-serve)',
    what: 'Hindi, English aur Mixed imatrix teeno naapkar jo best nikle wo. Fayda tabhi likha jaata hai jab naap use saaf dikhaye.',
    price: 'Rs 399 per model',
    note: 'Shuruaati price. GST, gateway fee aur GPU kharch ka hisaab abhi khula hai; pehle customers se manual UPI.',
  },
  {
    name: 'Apne data par calibration',
    what: 'Aapke kaam ke text (kaanooni, vitteey, internal) se imatrix aur usi par naap. Baat karke tay hota hai.',
    price: 'Baat karke',
  },
]

/** Claims this project must not make. Shown on the home page. */
export const NOT_CLAIMED: string[] = [
  'Hindi task (sawal-jawab, summary, translation) par accuracy: abhi naapi nahi gayi. KLD sirf FP16 se distribution ka fark hai.',
  'Hindi text se calibrate karna English se alag se behtar hai: kisi test set me saaf fark nahi mila.',
  'Imatrix ka fayda har model par hota hai: sirf Qwen2.5-1.5B par naapa hai, doosre models par alag ho sakta hai.',
  'Cloud use karne par DPDP ke tahat jurmana: ye galat hai. Sahi bas itna: data on-device rakhna aasaan banata hai.',
  '"Zero data leaks": hum ye nahi kehte. Desk app aaye to bhi chat aur documents device se bahar nahi jaate (activation ke alawa).',
  'Legal ya financial salah: koi bhi output professional se verify karwana zaroori hai.',
]
