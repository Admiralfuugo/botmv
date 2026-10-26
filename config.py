import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


BOT_TOKEN = os.environ["BOT_TOKEN"]
REQUIRED_CHANNELS = _list("REQUIRED_CHANNELS")
RESULTS_CHANNEL = os.getenv("RESULTS_CHANNEL", "")
# REQUIRED_CHANNELS / FB_URL / X_URL / I_URL — faqat birinchi ishga tushirishda
# admin paneldagi «Obunalar» bo'limiga ko'chiriladi, keyin paneldan boshqariladi.
FB_URL = os.getenv("FB_URL", "")
X_URL = os.getenv("X_URL", "")
I_URL = os.getenv("I_URL", "")
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "15"))
POLL_DAYS = int(os.getenv("POLL_DAYS", "15"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://votes:votes@localhost:5432/votes")

# birinchi bosh admin — faqat adminlar jadvali bo'sh bo'lsa ishlatiladi
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
# admin panelning tashqi manzili (https://admin.fuugo.uz): post rasmlarini Telegram shu yerdan oladi
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
# admin panelda vaqtlar shu UTC siljishida ko'rsatiladi (Toshkent = +5)
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "5"))
