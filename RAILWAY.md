# DTM Mock — Railway'ga joylash yo'riqnomasi

Loyiha Railway'ga tayyor: `Dockerfile` bor (Railway uni avto-aniqlaydi),
HTTPS/proxy/host sozlamalari Railway muhitida o'z-o'zidan yoqiladi.

## 1. GitHub'ga chiqarish (bir marta)

Loyihada git allaqachon tayyorlangan (`data/` va sirlar .gitignore bilan chiqarib
tashlangan — ular HECH QACHON GitHub'ga tushmaydi). Sizdan faqat:

1. github.com da **yangi PRIVATE repo** yarating (masalan `dtm-mock`), README qo'shmasdan;
2. Terminalda:

```bash
cd ~/Desktop/dtm_omr
git remote add origin https://github.com/SIZNING_LOGIN/dtm-mock.git
git push -u origin main
```

## 2. Railway'da servis yaratish

1. railway.com → **New Project** → **Deploy from GitHub repo** → `dtm-mock` ni tanlang;
2. Railway `Dockerfile`ni ko'rib o'zi quradi (2–3 daqiqa).

## 3. Volume ulash — MAJBURIY (busiz natijalar har deploy'da o'chadi!)

Servis → **Settings → Volumes → Add Volume** → Mount path: **`/app/data`**

## 4. O'zgaruvchilar (Variables bo'limida)

| Nomi | Qiymati | Izoh |
|---|---|---|
| `DTM_ADMIN_PASSWORD` | kuchli parol | **MAJBURIY** — Railway'da faylni o'qib bo'lmaydi, parol shu yerdan |
| `DTM_ADMIN_CODE` | masalan `QWERTY77` | yashirin admin-kod (kod + "Muhammadullo" ismi) |
| `DTM_IP_MULT` | `20` | maktab Wi-Fi'sidan ko'p o'quvchi kirsa bloklamaslik uchun |

`PORT`, `HOST`, HTTPS, proxy — avtomatik, tegmang.

## 5. Domen

- Tezkor: Settings → **Networking → Generate Domain** → `xxx.up.railway.app` (HTTPS tayyor);
- O'z domeningiz: .uz domen oling (ahost.uz / webspace.uz, ~60–100 ming so'm/yil) →
  Railway'da **Custom Domain** qo'shing → ko'rsatilgan **CNAME** yozuvini domen
  registratorida (yoki bepul Cloudflare'da, Proxy ON bilan) kiriting. HTTPS avtomatik.

## 6. Ishga tushgach tekshirish

1. `https://.../` — o'quvchi sahifasi ochiladi;
2. `https://.../admin/login` — `DTM_ADMIN_PASSWORD` bilan kiring;
3. Imtihon yarating, yo'nalish + PDF + kalitlar, "Ochish" — kod bilan sinab ko'ring.

Eslatma: lokal kompyuterdagi imtihonlar (`data/exams/…`) serverga avtomatik o'tmaydi —
serverda yangisini yaratasiz (PDF va kalitlarni qayta kiritish 5 daqiqa).

## 7. Zaxira odati

Railway'da kunlik cron zaxiramiz ishlamaydi. Qoida oddiy: **har imtihondan keyin
admin paneldan Excel'ni yuklab oling** — bu natijalarning to'liq nusxasi.
(Volume'ning o'zi deploy'lar orasida saqlanadi, lekin qo'shimcha nusxa — omonlik.)

## Yangilash (kod o'zgarsa)

```bash
git add -A && git commit -m "yangilash" && git push
```
Railway push'ni ko'rib o'zi qayta quradi va chiqaradi.
