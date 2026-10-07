# Production upgrade runbook: Horilla v1 → v2

> **Completed on 2026-10-05.** This is the record of a one-time procedure; it
> is not how to deploy. For the current server layout and deploy steps, see
> [../DEPLOY.md](../DEPLOY.md).
>
> The steps below use the names v2 had while it ran beside v1. v1 held
> `/opt/horilla`, `/etc/horilla.env` and the `horilla-gunicorn` service at the
> time, so v2 needed its own. After v1 was removed on 2026-10-07, v2 was
> renamed:
>
> | During the upgrade | Now |
> |---|---|
> | `/opt/horilla-v2/app`, `/opt/horilla-v2/venv` | `/opt/horilla/app`, `/opt/horilla/venv` |
> | `/etc/horilla-v2.env` | `/etc/horilla.env` |
> | `horilla-v2-web` | `horilla-web` |
> | `horilla-v2-scheduler` | `horilla-scheduler` |
> | branch `upgrade/v2` | `main` |
>
> A virtualenv cannot be moved, so the rename built a new one at
> `/opt/horilla/venv` and pinned it to the versions the old one had.

Server: Amazon Linux 2023, t3.small class (2 vCPU, 1.9 GB RAM), PostgreSQL 15
on the same host, nginx in front of gunicorn, service `horilla-gunicorn`
running v1 from `/opt/horilla/app`, settings in `/etc/horilla.env`.

v2 is installed **beside** v1, in `/opt/horilla-v2`, and v1 is left untouched.
Rolling back means restoring the database and pointing nginx at v1 again.

Background and findings: [README.md](README.md).

Conventions: run every command on the EC2 server as `ec2-user` unless it says
otherwise. Never paste the contents of an env file anywhere.

---

## Part A — preparation (any day before; no downtime)

### A1. Grow the disk to 16 GB

v2 needs about 1.1 GB, and part A2 adds a 2 GB swap file. There are 3.9 GB free.

1. EC2 console → the instance → **Storage** tab → the root volume → **Actions → Modify volume** → size **16** → Modify. Wait until the volume's state shows `optimizing` or `completed`.
2. On the server:
   ```bash
   sudo growpart /dev/nvme0n1 1 && sudo xfs_growfs -d / && df -h /
   ```
   `df` should show about 16G.

### A2. Add 2 GB of swap

1.9 GB of RAM is already mostly used by v1, PostgreSQL and nginx.

```bash
swapon --show
```
If that prints nothing:
```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab && free -h
```

### A3. Install Python 3.12 and tools

```bash
sudo dnf install -y python3.12 python3.12-pip git gettext patch
```

### A4. Get the v2 code

The branch `upgrade/v2` must be on GitHub first. It is pushed from the
developer machine; see part D for how `main` is updated.

```bash
sudo mkdir -p /opt/horilla-v2 && sudo chown ec2-user: /opt/horilla-v2 && git clone --branch upgrade/v2 https://github.com/hamzayaqoob1509/StackPinnacle-HRMS.git /opt/horilla-v2/app
```

### A5. Create the virtualenv and install

```bash
python3.12 -m venv /opt/horilla-v2/venv && /opt/horilla-v2/venv/bin/pip install --upgrade pip && /opt/horilla-v2/venv/bin/pip install -r /opt/horilla-v2/app/requirements.txt
```

Install the migration tool, pinned to the rehearsed version, and apply the patch:
```bash
/opt/horilla-v2/venv/bin/pip install horillasetup==1.1.5 && cd "$(/opt/horilla-v2/venv/bin/python -c 'import horillasetup,os;print(os.path.dirname(os.path.dirname(horillasetup.__file__)))')" && patch -p1 --dry-run < /opt/horilla-v2/app/docs/upgrade/horillasetup-1.1.5-hybrid-fingerprint.patch && patch -p1 < /opt/horilla-v2/app/docs/upgrade/horillasetup-1.1.5-hybrid-fingerprint.patch
```

### A6. Create the v2 settings file

v2 reads `SECRET_KEY` and `DEBUG`, not the `DJANGO_*` names in the current
file. This copies the database and host settings from the v1 file, adds a new
secret key and init password, and never prints a value.

```bash
sudo bash -c 'umask 077; set -a; . /etc/horilla.env; set +a; {
  echo "DEBUG=False"
  echo "SECRET_KEY=$(/opt/horilla-v2/venv/bin/python -c "import secrets;print(secrets.token_urlsafe(50))")"
  echo "DB_INIT_PASSWORD=$(/opt/horilla-v2/venv/bin/python -c "import secrets;print(secrets.token_urlsafe(24))")"
  echo "ALLOWED_HOSTS=$ALLOWED_HOSTS"
  echo "DATABASE_URL=$DATABASE_URL"
  echo "DB_ENGINE=django.db.backends.postgresql"
  echo "DB_NAME=$DB_NAME"
  echo "DB_USER=$DB_USER"
  echo "DB_PASSWORD=$DB_PASSWORD"
  echo "DB_HOST=$DB_HOST"
  echo "DB_PORT=$DB_PORT"
} > /etc/horilla-v2.env' && sudo sed -E 's/=.*/=<set>/' /etc/horilla-v2.env
```
Then add the site's address for CSRF. Replace the URL with the address people
use to open Horilla:
```bash
echo 'CSRF_TRUSTED_ORIGINS=https://hr.example.com' | sudo tee -a /etc/horilla-v2.env >/dev/null
```

The new `SECRET_KEY` signs everyone out once, on switch-over.

### A7. Static files and translations

These do not touch the database.
```bash
cd /opt/horilla-v2/app && sudo bash -c 'set -a; . /etc/horilla-v2.env; set +a; /opt/horilla-v2/venv/bin/python manage.py compilemessages -v 0 && /opt/horilla-v2/venv/bin/python manage.py collectstatic --noinput -v 0' && echo done
```

> **Do not run `migrate`, and do not start any v2 process, against the
> production database before part B.** A plain `migrate` on the v1 database
> permanently deletes the holidays and company-leave rules.

### A8. Create the v2 services (do not start them)

```bash
sudo tee /etc/systemd/system/horilla-v2-web.service >/dev/null <<'EOF'
[Unit]
Description=Horilla v2 (gunicorn)
After=network.target postgresql.service

[Service]
User=horilla
Group=horilla
WorkingDirectory=/opt/horilla-v2/app
EnvironmentFile=/etc/horilla-v2.env
ExecStart=/opt/horilla-v2/venv/bin/gunicorn horilla.wsgi:application --bind 127.0.0.1:8001 --workers 2 --timeout 120
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/horilla-v2-scheduler.service >/dev/null <<'EOF'
[Unit]
Description=Horilla v2 background jobs
After=network.target postgresql.service

[Service]
User=horilla
Group=horilla
WorkingDirectory=/opt/horilla-v2/app
EnvironmentFile=/etc/horilla-v2.env
ExecStart=/opt/horilla-v2/venv/bin/python manage.py run_scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo chown -R horilla: /opt/horilla-v2/app && echo ok
```

Exactly one scheduler must ever run: its jobs (payslips, backups) are not
idempotent.

### A9. Find the nginx line to change

```bash
sudo nginx -T 2>/dev/null | grep -n -E 'server_name|proxy_pass|location /static|location /media|alias|root '
```
Note the file and the `proxy_pass http://127.0.0.1:8000` line (or similar).
v2 serves its own static files, so if nginx has a `/static/` or `/media/`
block pointing at `/opt/horilla/app`, it changes to `/opt/horilla-v2/app` in B8.

### A10. Readiness check

```bash
df -h / && free -h && ls /opt/horilla-v2/venv/bin/horillasetup /etc/horilla-v2.env && systemctl is-enabled horilla-v2-web horilla-v2-scheduler
```
Expect free disk, swap present, both files present, and both services
`disabled`.

### A11. Final rehearsal on a fresh backup (go / no-go)

Requested by Horilla before production: rehearse once more on a fresh restore
of a recent backup.

1. On the server, take a backup as in the earlier transfer (no downtime):
   ```bash
   sudo bash -c 'umask 077; set -a; . /etc/horilla.env; set +a; PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" --format=custom --no-owner --no-privileges -f /home/ec2-user/v1-backup.dump' && sudo chown ec2-user: ~/v1-backup.dump && sha256sum ~/v1-backup.dump
   ```
2. On the developer machine, download it and check the checksum matches:
   ```bash
   scp -i ~/.ssh/horilla-ec2 ec2-user@SERVER:~/v1-backup.dump ~/horilla-backups/v1-backup.dump && sha256sum ~/horilla-backups/v1-backup.dump
   ```
3. Run the rehearsal:
   ```bash
   docs/upgrade/rehearse.sh ~/horilla-backups/v1-backup.dump
   ```

Go ahead with part B only if it ends with `REHEARSAL PASSED`. If it reports
duplicates other than payslips 127/128, resolve them first and add them to B4.

---

## Part B — the maintenance window (about 60 minutes)

Write down the start time. From B1 to B9, nobody can use Horilla.

### B1. Take v1 offline

```bash
sudo systemctl stop horilla-gunicorn && systemctl is-active horilla-gunicorn
```
Expect `inactive`. v1 ran its background jobs inside gunicorn, so they stop too.

### B2. Snapshot the server

EC2 console → the instance → **Storage** → root volume → **Actions → Create
snapshot**, description `pre-v2-upgrade`. Continue; it completes in the
background.

### B3. Back up the database and media

```bash
STAMP=$(date +%Y%m%d-%H%M) && mkdir -p ~/upgrade && sudo bash -c "umask 077; set -a; . /etc/horilla.env; set +a; PGPASSWORD=\"\$DB_PASSWORD\" pg_dump -h \"\$DB_HOST\" -p \"\$DB_PORT\" -U \"\$DB_USER\" -d \"\$DB_NAME\" --format=custom --no-owner --no-privileges -f /home/ec2-user/upgrade/pre-v2-$STAMP.dump; tar czf /home/ec2-user/upgrade/pre-v2-media-$STAMP.tar.gz -C /opt/horilla/app media" && sudo chown -R ec2-user: ~/upgrade && ls -lh ~/upgrade && pg_restore -l ~/upgrade/pre-v2-$STAMP.dump | grep -c "TABLE DATA"
```
Expect a dump of about 24 MB and `344`.

### B4. Remove the duplicate payslip

Deletes payslip 128 only if it is still an unsent draft identical to 127.
```bash
sudo bash -c 'set -a; . /etc/horilla.env; set +a; PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -tAc "
delete from payroll_payslip d using payroll_payslip k
 where d.id = 128 and k.id = 127 and d.status = '\''draft'\''
   and d.employee_id_id = k.employee_id_id and d.start_date = k.start_date and d.end_date = k.end_date
   and d.net_pay = k.net_pay and d.pay_head_data::text = k.pay_head_data::text
   and not exists (select 1 from payroll_payslip_installment_ids i where i.payslip_id = 128)
 returning '\''deleted payslip '\'' || d.id"'
```
Expect `deleted payslip 128`. **If it prints nothing, stop** and check the two
payslips before continuing.

### B5. Run the migration tool

```bash
sudo install -d -o horilla -g horilla -m 700 /opt/horilla-v2/upgrade-backups && cd /opt/horilla-v2/app && sudo bash -c 'set -a; . /etc/horilla-v2.env; set +a; export HORILLASETUP_REHEARSAL_ASSUME_15_PLUS=1; runuser -u horilla -- /opt/horilla-v2/venv/bin/horillasetup migrate hrms-v2 --from-v1 --backup-dir /opt/horilla-v2/upgrade-backups'
```
Expected, as in the rehearsal:
- Stage 1: `Horilla v1 (1.5.0-1.6.1 schema)` ✅
- Stage 2: `no blocking data found`, `40 users to migrate` (the current user count)
- Answer **y** at `Continue?`
- Stage 6: `✅ 40 users, no orphaned records, password hashes unchanged`

The tool runs as `horilla`, which cannot write into `/home/ec2-user`, so its
own backup goes to `/opt/horilla-v2/upgrade-backups`.

**If any stage fails, stop and go to part C.** Do not re-run the tool on a
partly migrated database.

### B6. Align the adopted columns

```bash
sudo bash -c 'set -a; . /etc/horilla-v2.env; set +a; PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f /opt/horilla-v2/app/docs/upgrade/post_migrate_align.sql' && echo aligned
```

### B7. Copy uploaded files and check

```bash
sudo cp -a /opt/horilla/app/media/. /opt/horilla-v2/app/media/ && sudo chown -R horilla: /opt/horilla-v2/app/media && echo "v1 files: $(sudo find /opt/horilla/app/media -type f | wc -l)  v2 files: $(sudo find /opt/horilla-v2/app/media -type f | wc -l)"
```
The two counts must match.

```bash
cd /opt/horilla-v2/app && sudo bash -c 'set -a; . /etc/horilla-v2.env; set +a; runuser -u horilla -- /opt/horilla-v2/venv/bin/python manage.py migrate --check' && echo "no pending migrations"
```

### B8. Start v2 and switch nginx

```bash
sudo systemctl enable --now horilla-v2-web horilla-v2-scheduler && sleep 10 && systemctl is-active horilla-v2-web horilla-v2-scheduler && curl -s -o /dev/null -w "health: %{http_code}\n" http://127.0.0.1:8001/health/
```
Expect two `active` and `health: 200`.

In the nginx file from A9, change the proxy target from port `8000` to `8001`,
and any `/static/` or `/media/` paths from `/opt/horilla/app` to
`/opt/horilla-v2/app`. Then:
```bash
sudo nginx -t && sudo systemctl reload nginx
```
Stop v1 from starting again on reboot:
```bash
sudo systemctl disable horilla-gunicorn
```

### B9. Verify in the browser

Open the usual address and check:

- [ ] An existing user logs in with their current password (everyone is signed out once).
- [ ] Employee count matches: 41 in total, 30 active (or today's numbers).
- [ ] Holidays: Configuration → Holidays shows all of them.
- [ ] Leave: balances and requests look right for two or three employees.
- [ ] Payroll: open a payslip and download its PDF.
- [ ] Company logo and any uploaded documents display.
- [ ] Custom features: Employee Handbook in the sidebar, "Approved By" on an approved leave, probation date on a profile, "Pro-rate on confirmation" on a leave type.
- [ ] `sudo journalctl -u horilla-v2-web -u horilla-v2-scheduler --since "15 min ago" -p err` shows no errors.

If all pass, the window is over. Note the end time.

---

## Part C — rollback

Use this if part B fails, or if a problem is found before anyone has entered new
data in v2. Anything entered in v2 after B9 is lost by a rollback.

```bash
sudo systemctl disable --now horilla-v2-web horilla-v2-scheduler
```
Restore the pre-upgrade dump from B3 over the database. Replace `STAMP` with
the timestamp in the file name:
```bash
sudo bash -c 'set -a; . /etc/horilla.env; set +a; PGPASSWORD="$DB_PASSWORD" pg_restore -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner --no-privileges /home/ec2-user/upgrade/pre-v2-STAMP.dump'
```
Undo the nginx change from B8 (back to port 8000 and `/opt/horilla/app`), then:
```bash
sudo nginx -t && sudo systemctl reload nginx && sudo systemctl enable --now horilla-gunicorn && sleep 5 && systemctl is-active horilla-gunicorn
```
If the database restore itself fails, restore the whole volume from the B2
snapshot instead.

---

## Part D — after the upgrade

Done:

- [x] `main` is the v2 code (2026-10-07, PR #8). v1 and v2 have unrelated
      histories; they were joined with
      `git merge --allow-unrelated-histories -s ours main` on `upgrade/v2`,
      which keeps both histories and v2's files, and merged with a merge
      commit. The server checkout tracks `main`.
- [x] v1 removed from the server (2026-10-07): `/opt/horilla`, the
      `horilla-gunicorn` service, the old `/etc/horilla.env`, the backups in
      `~/upgrade`, and the `pre-v2-upgrade` EBS snapshot. The pre-upgrade dump
      is kept off the server.
- [x] v2 renamed to the plain `horilla` names (see the table at the top).
- [x] Temporary SSH key for the backup transfer removed.
- [x] Amazon Linux updated to the latest 2023 release and rebooted
      (kernel 6.1.188, PostgreSQL 15.19, Python 3.12.14). The first attempt
      was cut off by a dropped session; see "Server maintenance" in
      [../DEPLOY.md](../DEPLOY.md).

Still open:

- [ ] Delete the `pre-os-update` EBS snapshot and `~/pre-os-update.dump` on
      the server, once the updated server has proven stable.
- [ ] Scheduled backups (nightly `pg_dump` off the server, and EBS snapshots).
      Nothing backs up the database automatically yet.
- [ ] A lock file with exact package versions. `requirements.txt` allows
      ranges, so two installs days apart differed (Django 5.2.17 and 5.2.18).
- [ ] Decide on hosting (EC2 only, or EC2 + RDS).
