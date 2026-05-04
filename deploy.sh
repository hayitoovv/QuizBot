#!/usr/bin/env bash
# ====== Telegram bot serverga joylash skripti (Ubuntu/Debian) ======
# Ishlatish (server'da, loyiha papkasi ichida):
#   bash deploy.sh
#
# Bu skript:
#   1. Python3, pip, venv ni o'rnatadi
#   2. .venv yaratadi va requirements.txt'ni o'rnatadi
#   3. /etc/systemd/system/testbot.service yaratadi
#   4. Botni ishga tushiradi va server reboot bo'lsa avtomat qaytadan boshlanadigan qiladi

set -euo pipefail

SERVICE_NAME="testbot"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"

echo "📦 Tizim paketlari (python3, venv)..."
sudo apt update
sudo apt install -y python3 python3-pip python3-venv

echo "🐍 Virtual environment..."
if [ ! -d "$PROJECT_DIR/.venv" ]; then
  python3 -m venv "$PROJECT_DIR/.venv"
fi

echo "📚 Python paketlari (requirements.txt)..."
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "⚙️  systemd service: /etc/systemd/system/${SERVICE_NAME}.service"
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Telegram Quiz Bot
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "🚀 Botni ishga tushirish..."
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

sleep 1
echo ""
echo "✅ Tayyor!"
echo ""
sudo systemctl status ${SERVICE_NAME} --no-pager -l || true

echo ""
echo "📋 Foydali komandalar:"
echo "  Log ko'rish:        sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Qayta ishga:        sudo systemctl restart ${SERVICE_NAME}"
echo "  To'xtatish:         sudo systemctl stop ${SERVICE_NAME}"
echo "  Holat:              sudo systemctl status ${SERVICE_NAME}"
echo ""
echo "💡 Yangi test qo'shish:"
echo "  1) .docx faylni ${PROJECT_DIR} ga ko'chiring"
echo "  2) main.py ichidagi TESTS dict'iga element qo'shing"
echo "  3) sudo systemctl restart ${SERVICE_NAME}"
