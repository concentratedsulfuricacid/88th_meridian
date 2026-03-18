# EC2 Deployment

This bot is now self-contained inside `src`.

All runtime helpers live under `src/runtime`.

## 1. Copy files to the instance

```bash
scp -r src ubuntu@YOUR_EC2_IP:~/bot/
```

## 2. SSH in

```bash
ssh ubuntu@YOUR_EC2_IP
cd ~/bot
```

## 3. Install Python and create a venv

For Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r src/requirements-live.txt
```

## 4. Create the live env file

Create `src/.env`:

```dotenv
ROOSTOO_BASE_URL=https://mock-api.roostoo.com
ROOSTOO_API_KEY=YOUR_KEY
ROOSTOO_API_SECRET=YOUR_SECRET
ROOSTOO_TIMEOUT_SECONDS=30
BINANCE_BASE_URL=https://api.binance.com
POLLING_SECONDS=60
SUBMISSION_STATE_PATH=src/state/live_state.json
LIVE_TRADING=false
```

Important:

- start with a fresh state file
- do not copy the old local `state/live_state.json`

## 5. Smoke test

Dry run:

```bash
.venv/bin/python -m src.live_bot --run-once
```

Live run:

```bash
.venv/bin/python -m src.live_bot --live --run-once
```

Continuous live run:

```bash
.venv/bin/python -m src.live_bot --live
```

## 6. Keep it running with systemd

Create `/etc/systemd/system/submission-bot.service`:

```ini
[Unit]
Description=Submission 50 50 Roostoo Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/bot
ExecStart=/home/ubuntu/bot/.venv/bin/python -m src.live_bot --live
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable submission-bot
sudo systemctl start submission-bot
sudo systemctl status submission-bot
journalctl -u submission-bot -f
```

## 7. Safer rollout

Recommended sequence:

1. run `--run-once` without `--live`
2. run `--live --run-once`
3. check balances and state
4. only then start the continuous service
