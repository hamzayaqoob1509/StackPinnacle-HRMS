# Deploying to production

How the production server is laid out and how to deploy a change to it.

## Server layout

Amazon Linux 2023 on EC2 (2 vCPU, 1.9 GB RAM plus 2 GB swap), behind
Cloudflare at https://hrms.stackpinnacle.com. PostgreSQL 15 runs on the same
host.

| What | Where |
|---|---|
| Code (a checkout of `main`) | `/opt/horilla/app` |
| Python 3.12 virtualenv | `/opt/horilla/venv` |
| Settings | `/etc/horilla.env` (readable by root only) |
| Website | service `horilla-web`: gunicorn on `127.0.0.1:8001` |
| Background jobs | service `horilla-scheduler`: `manage.py run_scheduler` |
| Reverse proxy | nginx, `/etc/nginx/conf.d/00-horilla.conf`, proxies to port 8001 |
| Uploaded files | `/opt/horilla/app/media` (not in git) |
| Database | `horilla_db` on `127.0.0.1:5432` |

Things that are easy to get wrong:

- **The app folder belongs to the `horilla` user**, so git there needs
  `sudo -u horilla`. Plain `git` fails with "dubious ownership".
- **Horilla reads `SECRET_KEY` and `DEBUG`**, not `DJANGO_SECRET_KEY` or
  `DJANGO_DEBUG`. A misnamed setting is ignored silently and the insecure
  default is used instead. With `DEBUG=False`, Horilla refuses to start on a
  placeholder `SECRET_KEY`, an empty or `*` `ALLOWED_HOSTS`, or the default
  `DB_INIT_PASSWORD`.
- **`AXES_PROXY_COUNT=1`** tells Horilla that Cloudflare is the one proxy in
  front of nginx, so it sees each visitor's real address for login lockouts
  and attendance IP rules. Change it if Cloudflare is removed.
- **Exactly one scheduler may run.** Its jobs (payslips, reminders) are not
  safe to run twice.
- **Never print `/etc/horilla.env`.** The commands below load it without
  showing it.
- **Run long commands detached** (see [Server maintenance](#server-maintenance)).

## Deploying a change

Merge to `main` first. Then, on the server:

### 1. Get the code

```bash
cd /opt/horilla/app && sudo -u horilla git pull --ff-only && sudo -u horilla git log -1 --format="%h %s"
```

### 2. Only if `requirements.txt` changed: install packages

```bash
/opt/horilla/venv/bin/pip install -r /opt/horilla/app/requirements.txt
```

### 3. Only if the change adds migrations: back up, then migrate

Migration files are committed to the repo. Do not run `makemigrations` on the
server.

```bash
sudo bash -c 'umask 077; set -a; . /etc/horilla.env; set +a; PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" --format=custom --no-owner --no-privileges -f /home/ec2-user/pre-deploy-$(date +%Y%m%d-%H%M).dump' && sudo chown ec2-user: ~/pre-deploy-*.dump && ls -lh ~/pre-deploy-*.dump
```
```bash
cd /opt/horilla/app && sudo bash -c 'set -a; . /etc/horilla.env; set +a; runuser -u horilla -- /opt/horilla/venv/bin/python manage.py migrate --noinput'
```

### 4. Only if CSS, JavaScript, images or translations changed

```bash
cd /opt/horilla/app && sudo bash -c 'set -a; . /etc/horilla.env; set +a; runuser -u horilla -- /opt/horilla/venv/bin/python manage.py collectstatic --noinput -v 0 && runuser -u horilla -- /opt/horilla/venv/bin/python manage.py compilemessages -v 0'
```

### 5. Restart and check

```bash
sudo systemctl restart horilla-web horilla-scheduler && sleep 10 && systemctl is-active horilla-web horilla-scheduler && curl -s -o /dev/null -w "site: %{http_code}\n" -H "Host: hrms.stackpinnacle.com" -H "X-Forwarded-Proto: https" http://127.0.0.1:8001/login/ && sudo journalctl -u horilla-web -u horilla-scheduler --since "1 min ago" -p err --no-pager | tail -5
```

Expect `active` twice, `site: 200` and `-- No entries --`. The check sends the
real hostname because Horilla answers 400 to any host not in `ALLOWED_HOSTS`,
including `127.0.0.1`.

## Rolling back a deploy

If the change had no migrations, go back to the previous commit and restart:

```bash
cd /opt/horilla/app && sudo -u horilla git log --oneline -5
```
```bash
cd /opt/horilla/app && sudo -u horilla git checkout <previous-commit> && sudo systemctl restart horilla-web horilla-scheduler
```

Then revert the change on `main` and deploy normally, so the server is back on
the branch.

If it had migrations, restoring the code is not enough. Stop both services,
restore the `pre-deploy-*.dump` taken in step 3 with
`pg_restore --clean --if-exists --no-owner --no-privileges`, check out the
previous commit, and start the services again. Anything entered since the
backup is lost.

## Logs

```bash
sudo journalctl -u horilla-web -u horilla-scheduler --since "1 hour ago" --no-pager | tail -50
```

nginx logs are in `/var/log/nginx/`.

## Server maintenance

A dropped terminal session kills whatever it was running. On 2026-10-07 that
interrupted `dnf upgrade` while it was building the new kernel's boot image,
and a reboot at that point would not have come back up. So:

- Run anything long detached from the terminal:
  ```bash
  sudo systemd-run --unit=os-update --wait --collect --pipe dnf upgrade --releasever=latest -y
  ```
  If the session drops, it keeps running; see it with
  `sudo journalctl -u os-update`.
- Take an EBS snapshot and a `pg_dump` first.
- Before rebooting after a kernel update, check that the default kernel has a
  boot image:
  ```bash
  sudo grubby --default-kernel && ls -la /boot | grep initramfs
  ```
  If the image for the default kernel is missing, do not reboot; rebuild it with
  `sudo systemd-run --unit=kernel-fix --wait --collect /bin/kernel-install add <version> /lib/modules/<version>/vmlinuz`.
- After the reboot, run the check from step 5.

## Running locally

```bash
docker compose up --build
```

This starts an empty Horilla at http://localhost:8000 with its own database.
It does not use production data.

## History

The server ran Horilla v1 until 2026-10-05. How it was migrated to v2, and
what that found, is in [upgrade/](upgrade/README.md).
