"""
scheduler.py — INSIDEX Auto Backup
รัน APScheduler แยกต่างหากจาก Flask
ส่ง backup CSV ไปที่ Discord channel ทุกเที่ยงคืน (BKK time)
"""
import os, io, requests
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler

BKK            = timezone(timedelta(hours=7))
BACKUP_SECRET  = os.environ.get("BACKUP_SECRET", "")
DISCORD_WEBHOOK= os.environ.get("BACKUP_DISCORD_WEBHOOK", "")
APP_URL        = os.environ.get("APP_URL", "http://localhost:3000")  # Railway internal URL หรือ public URL

def do_backup():
    now = datetime.now(BKK)
    print(f"[{now}] 🕛 Starting midnight backup...")

    if not BACKUP_SECRET:
        print("⚠️  BACKUP_SECRET ไม่ได้ตั้งค่า — ข้าม")
        return

    # ── ดึง CSV จาก app ──────────────────────────────────────────
    try:
        r = requests.get(
            f"{APP_URL}/api/backup/csv",
            headers={"X-Backup-Secret": BACKUP_SECRET},
            timeout=30,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"❌ ดึง backup CSV ล้มเหลว: {e}")
        return

    csv_bytes = r.content
    fname     = f"insidex_backup_{now.strftime('%Y-%m-%d')}.csv"

    # ── ส่ง Discord webhook ──────────────────────────────────────
    if DISCORD_WEBHOOK:
        try:
            requests.post(
                DISCORD_WEBHOOK,
                data={"content": f"📦 **INSIDEX Auto Backup** `{fname}`\n🕛 {now.strftime('%d/%m/%Y %H:%M')} (BKK)"},
                files={"file": (fname, io.BytesIO(csv_bytes), "text/csv")},
                timeout=30,
            )
            print(f"✅ ส่ง Discord สำเร็จ: {fname}")
        except Exception as e:
            print(f"⚠️  Discord webhook ล้มเหลว: {e}")
    else:
        print("ℹ️  BACKUP_DISCORD_WEBHOOK ไม่ได้ตั้งค่า — ข้ามการส่ง Discord")

    print(f"✅ Backup เสร็จ: {len(csv_bytes):,} bytes")

if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone=BKK)
    # ทุกเที่ยงคืน (00:00 BKK)
    scheduler.add_job(do_backup, "cron", hour=0, minute=0, id="midnight_backup")
    print("🕛 Backup scheduler เริ่มทำงาน — จะ backup ทุกเที่ยงคืน BKK")
    # รัน 1 ครั้งตอนเริ่มต้น (optional — ถ้าต้องการ backup ทันทีที่ deploy)
    # do_backup()
    scheduler.start()