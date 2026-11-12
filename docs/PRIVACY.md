# Sthamly Legal Desk: privacy

## Computer par kya rehta hai (kabhi bahar nahi jaata)
- Aapki PDF aur un ka text. Ye sirf software chalne tak memory me rehte hain, band hote hi hat jaate hain.
- Chat ka content.
- AI model (GGUF file). Model aur PDF dono aapke computer par chalte hain (llama-server localhost par).

## Server par kya jaata hai (sirf activation aur check ke samay)
- Activation key: server sirf uska SHA-256 hash rakhta hai, asli key nahi.
- Device pehchaan: ek hash (computer ka naam ya MAC seedha nahi bheja jaata).
- OS ka naam (windows / macos / linux).

## License check
- Har 30 din me server se dobara check hota hai.
- Server na mile to 14 din tak poora access. Uske baad sirf padhne aur export ka mode (naya AI kaam band).

## Limits (sach)
- Local license cache puri tarah tamper-proof nahi hai. Poori rokthaam server ka signed token hoga, jo abhi nahi bana.
- Scanned (photo wali) PDF abhi nahi padhi jaati (OCR nahi hai).
- AI ke jawab galat ho sakte hain. Draft ko kisi qualified vakeel se verify karwayein.
