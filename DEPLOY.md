# Deploying Scout on a DigitalOcean Droplet

Scout is a lightweight outbound-polling bot — the smallest Droplet runs it
comfortably, and no inbound ports need to be opened.

## 1. Create the Droplet

1. DigitalOcean → **Create → Droplets**.
2. Image: **Ubuntu 24.04 LTS**.
3. Plan: **Basic / Regular**, the ~$6/mo (1 GB) size is plenty.
4. Authentication: **SSH key** (recommended).
5. Create it, then note the Droplet's **public IP**.

## 2. Create a non-root user

Droplets log in as `root`; don't run the bot as root. SSH in and create a user:

```bash
ssh root@DROPLET_IP

adduser ubuntu                          # set a password when prompted
usermod -aG sudo ubuntu
rsync --archive ~/.ssh /home/ubuntu/    # copy your SSH key over
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
exit
```

Then log back in as that user:

```bash
ssh ubuntu@DROPLET_IP
```

## 3. Clone the repo

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/zachyboiii/scout-bot.git /home/ubuntu/scout-bot
cd /home/ubuntu/scout-bot
```

## 4. Run the setup script

```bash
bash deploy/setup.sh
```

The first run creates a `.env` from the template and stops so you can fill it
in:

```bash
nano .env
```

Set your **production** `TELEGRAM_BOT_TOKEN` and `ANTHROPIC_API_KEY` (plus
optional `ALLOWED_USERS`, `SCOUT_*`). Save, then run it again:

```bash
bash deploy/setup.sh
```

This time it installs Python, creates the virtualenv, installs dependencies,
writes a `systemd` unit, and starts the bot. It auto-starts on boot and
auto-restarts on crash.

## 5. Verify

```bash
systemctl status scout-bot
journalctl -u scout-bot -f      # live logs; Ctrl+C to stop watching
```

You should see `Scout is polling…`. Message your **production** bot on Telegram
to confirm.

## Updating after code changes

Push from your dev machine, then on the Droplet:

```bash
cd /home/ubuntu/scout-bot
bash deploy/update.sh
```

(pulls latest, syncs dependencies, restarts the service.)

## Useful commands

| Command | Purpose |
|---|---|
| `sudo systemctl restart scout-bot` | Restart the bot |
| `sudo systemctl stop scout-bot` | Stop it |
| `sudo systemctl disable --now scout-bot` | Stop and disable autostart |
| `journalctl -u scout-bot -f` | Follow logs |
| `journalctl -u scout-bot --since "10 min ago"` | Recent logs |

## Notes

- **One token per running bot.** Telegram allows a single poller per token, so
  the Droplet (prod token) and your local machine (test token) can run at once
  only because they use different tokens. Never share a token across two
  running instances.
- **Secrets live only in `.env`** on the Droplet — never committed (`.gitignore`
  covers it). If a token leaks, revoke it in @BotFather and update `.env`.
- **Cost:** the Droplet bills continuously (~$6/mo) whether or not the bot is
  busy. Destroy it to stop charges.
