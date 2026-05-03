# 🚀 Deployment Guide — GCP Free Tier + Streamlit Cloud

## Step 1: GCP e2-micro VM (always-on, free forever)

```bash
# 1. Go to console.cloud.google.com → Compute Engine → Create Instance
#    Name: crypto-agent
#    Region: us-central1 (required for free tier)
#    Machine type: e2-micro
#    Boot disk: Ubuntu 22.04 LTS, 30GB standard
#    Firewall: Allow HTTP + HTTPS
#    → Create

# 2. SSH into your VM (click SSH button in console)
# 3. Install Python deps
sudo apt update && sudo apt install python3-pip git -y
git clone https://github.com/YOUR_USERNAME/crypto-agent.git
cd crypto-agent
pip3 install -r requirements.txt --break-system-packages

# 4. Set up environment
cp .env.example .env
nano .env   # Add your Coinbase sandbox API keys

# 5. Test run first
python3 main.py
```

## Step 2: Auto-start with systemd (survives reboots)

```bash
# Replace YOUR_USERNAME with your Linux username (run: whoami)
sudo cp crypto-agent.service /etc/systemd/system/
sudo sed -i 's/YOUR_USERNAME/'$(whoami)'/g' /etc/systemd/system/crypto-agent.service

sudo systemctl daemon-reload
sudo systemctl enable crypto-agent    # auto-start on reboot
sudo systemctl start crypto-agent     # start now

# Check status
sudo systemctl status crypto-agent
sudo journalctl -u crypto-agent -f    # live logs
```

## Step 3: GitHub auto-sync (so dashboard always has fresh data)

```bash
# Set up SSH key for GitHub (one-time)
ssh-keygen -t ed25519 -C "crypto-agent-gcp"
cat ~/.ssh/id_ed25519.pub   # Add this to GitHub → Settings → Deploy Keys

# Make sync script executable
chmod +x sync_to_github.sh
sed -i 's/YOUR_USERNAME/'$(whoami)'/g' sync_to_github.sh

# Add to crontab (runs every 15 minutes)
crontab -e
# Add this line:
# */15 * * * * /home/YOUR_USERNAME/crypto_agent/sync_to_github.sh >> /home/YOUR_USERNAME/crypto_agent/logs/sync.log 2>&1
```

## Step 4: Streamlit Community Cloud (free dashboard)

```
1. Push your repo to GitHub (make sure data/trades.db is tracked)
2. Go to share.streamlit.io → New app
3. Repository: your-username/crypto-agent
4. Main file path: dashboard/app.py
5. Advanced → Secrets: paste your .env contents
6. Deploy → get your public URL: yourname-crypto-agent.streamlit.app
```

## Useful commands

```bash
# Check agent is running
sudo systemctl status crypto-agent

# View live logs
tail -f logs/agent.log

# Check open trades
sqlite3 logs/trades.db "SELECT symbol,direction,entry_price,size_usd FROM trades WHERE status='OPEN';"

# Check all stats
sqlite3 logs/trades.db "SELECT COUNT(*),SUM(pnl_usd),AVG(pnl_pct) FROM trades WHERE status='CLOSED';"

# Restart agent
sudo systemctl restart crypto-agent

# Stop agent
sudo systemctl stop crypto-agent
```

## Getting Coinbase Sandbox API Keys

1. Go to https://portal.cdp.coinbase.com
2. Create account → API Keys section
3. Switch to Sandbox environment
4. Generate new key → copy API Key + Secret into .env
5. COINBASE_SANDBOX=true ensures no real trades are placed
