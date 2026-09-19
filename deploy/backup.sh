#!/usr/bin/env bash
# DTM Mock — data/ zaxira nusxasi (kunlik cron uchun)
# Cron: 0 2 * * * /home/muhammadullo/Desktop/dtm_omr/deploy/backup.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${DTM_BACKUP_DIR:-$HOME/dtm_backups}"
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d_%H%M)
tar -czf "$DEST/dtm_data_$STAMP.tar.gz" -C "$ROOT" data
# 14 kundan eski nusxalarni tozalash
find "$DEST" -name "dtm_data_*.tar.gz" -mtime +14 -delete
echo "Zaxira: $DEST/dtm_data_$STAMP.tar.gz"
