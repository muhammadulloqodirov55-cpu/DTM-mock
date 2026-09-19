# DTM Mock — production joylash yo'riqnomasi

Maqsad: platformani internetga xavfsiz chiqarish. Qatlamlar (tashqaridan ichkariga):

```
Internet → [Cloudflare] → [firewall/sysctl] → [nginx] → [fail2ban] → [waitress + ilova himoyasi]
```

## 1. Cloudflare (volumetrik DDoS'ga qarshi asosiy qalqon — BEPUL)

1. cloudflare.com da akkaunt oching, domeningizni qo'shing;
2. Domen registratoringizda NS yozuvlarini Cloudflare berganiga almashtiring;
3. DNS bo'limida A-yozuv → server IP, **Proxy: ON (to'q sariq bulut)** — shunda
   hujum trafigi Cloudflare tarmog'ida so'nadi, server IP yashirin qoladi;
4. SSL/TLS → "Full (strict)"; Security → Bot Fight Mode: ON;
5. Hujum paytida: Security → "Under Attack Mode" ni yoqing (har tashrifga JS-tekshiruv).

## 2. Server OS (bir marta)

```bash
sudo bash deploy/firewall.sh          # UFW + SYN-cookies + ulanish tezligi chegarasi
```

## 3. nginx + HTTPS

```bash
sudo apt install nginx certbot python3-certbot-nginx
sudo cp deploy/nginx-dtm.conf /etc/nginx/sites-available/dtm
sudo ln -s /etc/nginx/sites-available/dtm /etc/nginx/sites-enabled/
# faylda server_name ga domeningizni yozing
sudo certbot --nginx -d sizning-domen.uz
sudo nginx -t && sudo systemctl reload nginx
```

## 4. Ilova xizmati (systemd, sandbox bilan)

```bash
sudo cp deploy/dtm-mock.service /etc/systemd/system/
# faylda User/WorkingDirectory yo'llarini moslang
sudo systemctl daemon-reload
sudo systemctl enable --now dtm-mock
```

## 5. fail2ban (ilova logidan firewall-ban)

`deploy/fail2ban-dtm.conf` ichidagi yo'riqnoma bo'yicha — ilova `data/security.log` ga
yozgan buzilishlar (parol terish, flood, CSRF) 15 daqiqalik ilova-banidan tashqari
1 soatlik firewall-ban ham oladi.

## 6. Zaxira

```bash
crontab -e     # qo'shing:  0 2 * * * /to'liq/yo'l/deploy/backup.sh
```

## 7. Log aylantirish

```bash
sudo cp deploy/logrotate-dtm.conf /etc/logrotate.d/dtm-mock   # yo'lni moslang
```

## O'rnatiladigan paketlar (serverda)

Onlayn platformaga OpenCV KERAK EMAS — yengil ro'yxat yetadi:

```bash
python3 -m venv .venv
.venv/bin/pip install -r deploy/requirements-web.txt
```

(Qog'oz-varaqa skaneri/Telegram bot ham kerak bo'lsa: `pip install -r requirements.txt`,
serverda `opencv-contrib-python` o'rniga `opencv-contrib-python-headless` oling.)

## Muhit o'zgaruvchilari

| O'zgaruvchi | Qiymat | Nima uchun |
|---|---|---|
| `HOST` | `127.0.0.1` | faqat nginx orqali kirish (tashqariga to'g'ridan ochmang) |
| `DTM_BEHIND_PROXY` | `1` | X-Forwarded-For'dan haqiqiy IP olish (rate-limit to'g'ri ishlashi) |
| `DTM_HTTPS` | `1` | Secure cookie + HSTS |
| `DTM_ADMIN_PASSWORD` | kuchli parol | `data/admin_parol.txt` o'rniga |
| `DTM_OMR_SECRET` | 32+ belgi | sessiya kaliti (`data/.secret_key` o'rniga) |
| `DTM_IP_MULT` | masalan `20` | maktab NAT'i: ko'p o'quvchi bitta umumiy IP ortidan kirsa, per-IP chegaralarni shuncha barobar kengaytiradi (global chegaralar o'zgarmaydi). Uydan kirishda kerak emas. |

## Muntazam

- `sudo apt update && sudo apt upgrade` — OS yangilanishlari (haftada);
- `data/security.log` ni ko'zdan kechirish;
- Admin parolni hech kimga bermang, brauzerda saqlamang.
