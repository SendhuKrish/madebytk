# Singapore Toto Smart Filter Engine + Tracker

20 validated structural + inter-draw rules · Concentrated + Diverse + Low-Skew modes

## Quick Start

```bash
# On your Azure VM
cd ~
git clone <your-repo> toto
cd toto
chmod +x deploy/deploy.sh
./deploy/deploy.sh
```

## Project Structure

```
app/
├── main.py                  — FastAPI app with all endpoints
├── models.py                — Pydantic request/response models
├── services/
│   ├── engine.py            — Core prediction engine (20 rules, 3 strategies)
│   ├── scraper.py           — Fetches latest draw from third-party sites
│   └── db.py                — Supabase client wrapper
└── jobs/
    ├── predict.py           — Cron: generate predictions (Mon & Thu 08:00 SGT)
    └── results.py           — Cron: fetch actual results (Mon & Thu 22:00 SGT)
website/
    └── index.html           — Static frontend served by nginx
deploy/
    ├── deploy.sh            — Deployment script
    ├── toto-engine.service  — Systemd unit
    ├── nginx-madebytk.conf  — Nginx server block
    └── crontab.txt          — Cron schedule entries
docs/
    └── supabase_setup.sql   — Database schema
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /draws | All draws (predictions, bets, results) — descending by date |
| GET | /predict | Auto-fetch latest draw + generate 7 lines |
| POST | /predict | Manual draw input + generate |
| POST | /postmortem | Analyze past predictions vs actual |
| GET | /health | Status check |

## Deployment Steps

1. Run `deploy/deploy.sh` on the Azure VM
2. Point `madebytk.com` DNS A record to the VM's public IP
3. Run `sudo certbot --nginx -d madebytk.com -d www.madebytk.com`
4. Add cron entries: `crontab -e` (copy from `deploy/crontab.txt`)
5. Verify: `curl http://localhost:8100/health`

## Service Management

```bash
sudo systemctl status toto-engine
sudo systemctl restart toto-engine
sudo journalctl -u toto-engine -f
```
