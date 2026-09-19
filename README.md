# DTM Mock — onlayn imtihon platformasi

O'zbekiston DTM uslubidagi onlayn mock imtihonlar: admin imtihon yaratadi
(yo'nalishlar, PDF kitobchalar, javob kalitlari), o'quvchilar kod bilan kirib
onlayn topshiradi, tizim DTM qoidasi bo'yicha baholaydi.

## Baholash (o'zgarmas DTM qonuni)

- 90 savol: 1–30 majburiy fanlar (1.1 balldan), 31–60 fan 1 (3.1), 61–90 fan 2 (2.1)
- Maksimal: **189.0** — hisob aniq Decimal arifmetikada

## Ishga tushirish (lokal)

```bash
python3 -m venv .venv
.venv/bin/pip install -r deploy/requirements-web.txt
.venv/bin/python -m webapp.app        # http://127.0.0.1:5000
```

- O'quvchi: `/` — kod + Familiya Ism + sinf
- Admin: `/admin/login` — parol `data/admin_parol.txt` da (yoki `DTM_ADMIN_PASSWORD` env)

## Joylash

- **Railway**: [RAILWAY.md](RAILWAY.md) — Dockerfile tayyor, volume `/app/data`
- **VPS (nginx + Cloudflare)**: [deploy/README.md](deploy/README.md)

## Imkoniyatlar

- Umumiy imtihon ichida yo'nalishlar: har birida o'z kirish kodi, fanlar juftligi,
  1–8 variant (PDF + har fanga alohida kiritiladigan kalitlar)
- Imtihon ekrani: pdf.js ko'ruvchi (yuklab olish/chop etish yo'q), 90 savollik
  javob paneli, ⚑ bayroq, taymer, 12 soniyalik avto-saqlash + localStorage
- Anti-cheat: majburiy fullscreen, tab/fokus nazorati, buzilishda vaqt pauzaga
  olinib admin ruxsati kutiladi ("Yana imkon berish")
- Hisobotlar: Excel (sinf tartibida, fan ballari, 90 javob rang bilan) va PDF;
  o'quvchiga natija sahifasi + PDF
- Xavfsizlik: rate-limit + avto-ban, CSRF/XSS/IDOR himoyasi, sessiya-token,
  security.log auditi (fail2ban'ga tayyor), to'liq security headerlar

Muhit o'zgaruvchilari va xavfsizlik qatlamlari: [deploy/README.md](deploy/README.md).
