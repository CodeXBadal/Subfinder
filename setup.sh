#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  SubHunter Bot v4.0 — VPS Setup Script
#  Tested on: Ubuntu 22.04 / Debian 12
#
#  USAGE:
#    chmod +x setup.sh
#    sudo ./setup.sh
# ══════════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Must run as root ──────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "Run as root: sudo ./setup.sh"

BOT_DIR="/opt/subhunter"
DATA_DIR="/var/lib/subhunter"
BOT_USER="subhunter"

info "═══════════════════════════════════════════"
info "  SubHunter Bot v4.0 — VPS Setup"
info "═══════════════════════════════════════════"

# ── 1. System packages ────────────────────────────────────────
info "Installing system packages…"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

PYTHON_VER=$(python3 --version | awk '{print $2}')
info "Python version: $PYTHON_VER"

# ── 2. Create system user ─────────────────────────────────────
info "Creating system user '$BOT_USER'…"
if ! id "$BOT_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$BOT_USER"
    info "User created."
else
    warn "User '$BOT_USER' already exists."
fi

# ── 3. Create directories ─────────────────────────────────────
info "Creating directories…"
mkdir -p "$BOT_DIR" "$DATA_DIR"
chown "$BOT_USER:$BOT_USER" "$DATA_DIR"

# ── 4. Copy bot files ─────────────────────────────────────────
info "Installing bot files to $BOT_DIR…"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -r "$SCRIPT_DIR"/. "$BOT_DIR"/
chown -R "$BOT_USER:$BOT_USER" "$BOT_DIR"

# ── 5. Virtual environment ────────────────────────────────────
info "Creating Python virtual environment…"
python3 -m venv "$BOT_DIR/venv"
"$BOT_DIR/venv/bin/pip" install --quiet --upgrade pip
"$BOT_DIR/venv/bin/pip" install --quiet -r "$BOT_DIR/requirements.txt"
info "Dependencies installed."

# ── 6. .env file ──────────────────────────────────────────────
if [[ ! -f "$BOT_DIR/.env" ]]; then
    cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
    warn "Created .env from template."
    warn ">>> Edit $BOT_DIR/.env with your BOT_TOKEN and ADMIN_IDS before starting! <<<"
else
    info ".env already exists — skipping."
fi

# Always override DATA_DIR in .env to the correct VPS path
if grep -q "^DATA_DIR=" "$BOT_DIR/.env"; then
    sed -i "s|^DATA_DIR=.*|DATA_DIR=$DATA_DIR|" "$BOT_DIR/.env"
else
    echo "DATA_DIR=$DATA_DIR" >> "$BOT_DIR/.env"
fi

chown "$BOT_USER:$BOT_USER" "$BOT_DIR/.env"
chmod 600 "$BOT_DIR/.env"   # Only owner can read — protects BOT_TOKEN

# ── 7. systemd service ────────────────────────────────────────
info "Installing systemd service…"
cp "$BOT_DIR/subhunter.service" /etc/systemd/system/subhunter.service
systemctl daemon-reload
systemctl enable subhunter
info "Service enabled (will auto-start on reboot)."

# ── 8. Done ───────────────────────────────────────────────────
echo ""
info "═══════════════════════════════════════════"
info "  Setup Complete!"
info "═══════════════════════════════════════════"
echo ""
echo -e "  ${YELLOW}NEXT STEPS:${NC}"
echo -e "  1. Edit your config:"
echo -e "     ${GREEN}nano $BOT_DIR/.env${NC}"
echo ""
echo -e "  2. Set these values:"
echo -e "     ${GREEN}BOT_TOKEN${NC}   = token from @BotFather"
echo -e "     ${GREEN}ADMIN_IDS${NC}   = your Telegram user ID (from @userinfobot)"
echo ""
echo -e "  3. Start the bot:"
echo -e "     ${GREEN}sudo systemctl start subhunter${NC}"
echo ""
echo -e "  4. Check it's running:"
echo -e "     ${GREEN}sudo systemctl status subhunter${NC}"
echo -e "     ${GREEN}sudo journalctl -u subhunter -f${NC}"
echo ""
echo -e "  5. (Optional) Set up UptimeRobot to monitor:"
echo -e "     ${GREEN}http://YOUR_VPS_IP:8080/health${NC}"
echo ""
