#!/usr/bin/env bash
# DTM Mock — OS darajasidagi himoya: firewall + SYN-flood bardoshliligi
# Ishga tushirish: sudo bash deploy/firewall.sh
set -e

echo "== UFW firewall: faqat SSH/HTTP/HTTPS ochiq =="
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
# 5000-port TASHQARIGA YOPIQ qoladi — unga faqat nginx (localhost) kiradi
ufw --force enable
ufw status verbose

echo "== sysctl: SYN-flood / L3-L4 bardoshlilik =="
cat > /etc/sysctl.d/99-dtm-ddos.conf <<'EOF'
# SYN cookies — SYN flood ostida ham ulanishlar ishlaydi
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.tcp_synack_retries = 2
# yarim ochiq/eskirgan ulanishlarni tez tozalash
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_keepalive_time = 300
# spoofga qarshi teskari yo'l tekshiruvi
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
# ICMP broadcast (smurf) rad
net.ipv4.icmp_echo_ignore_broadcasts = 1
# redirect qabul qilinmaydi
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
EOF
sysctl --system | grep -E "syncookies|rp_filter" || true

echo "== bir IP'dan yangi ulanishlar tezligini cheklash (iptables) =="
iptables -I INPUT -p tcp --dport 80  -m state --state NEW -m recent --set
iptables -I INPUT -p tcp --dport 80  -m state --state NEW -m recent --update --seconds 10 --hitcount 40 -j DROP
iptables -I INPUT -p tcp --dport 443 -m state --state NEW -m recent --set
iptables -I INPUT -p tcp --dport 443 -m state --state NEW -m recent --update --seconds 10 --hitcount 40 -j DROP

echo "Tayyor. Eslatma: iptables qoidalari doimiy bo'lishi uchun 'iptables-persistent' o'rnating."
