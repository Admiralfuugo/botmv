import asyncpg

from config import DATABASE_URL

pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    message_id BIGINT,
    ends_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS candidates (
    id SERIAL PRIMARY KEY,
    poll_id INT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    user_id BIGINT NOT NULL,
    poll_id INT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    candidate_id INT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, poll_id)
);
CREATE INDEX IF NOT EXISTS votes_candidate_idx ON votes(candidate_id);
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    social_confirmed BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE polls ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS votes_poll_created_idx ON votes(poll_id, created_at);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- obuna bo'linadigan joylar: kind='telegram' — bot tekshiradi, qolganlari — «obuna bo'ldim» tugmasi
CREATE TABLE IF NOT EXISTS channels (
    id SERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    position INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_super BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login TIMESTAMPTZ
);
-- «obuna bo'ldim» vaqti: undan keyin yangi havola qo'shilsa, qayta tasdiqlash so'raladi
ALTER TABLE users ADD COLUMN IF NOT EXISTS social_at TIMESTAMPTZ;
UPDATE users SET social_at = '2000-01-01' WHERE social_confirmed AND social_at IS NULL;
-- kanal posti matni (admin yozadi); bo'sh bo'lsa sarlavha ishlatiladi
ALTER TABLE polls ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
-- post rasmi (JPEG); image_ver har almashtirishda oshadi — Telegram yangi URL ni qayta yuklaydi
ALTER TABLE polls ADD COLUMN IF NOT EXISTS image BYTEA;
ALTER TABLE polls ADD COLUMN IF NOT EXISTS image_ver INT NOT NULL DEFAULT 0;
-- «Ulashish» orqali yuborilgan nusxalar: bot ularni ham jonli yangilaydi
CREATE TABLE IF NOT EXISTS shared_posts (
    inline_message_id TEXT PRIMARY KEY,
    poll_id INT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Premium konkursi: post, muddat va kanal posti
CREATE TABLE IF NOT EXISTS giveaways (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    button_text TEXT NOT NULL DEFAULT '🚀 Boshlash',
    image BYTEA,
    image_ver INT NOT NULL DEFAULT 0,
    file_id TEXT,
    ends_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- ishtirokchilar: status 'started' — Boshlashni bosgan, 'joined' — raqam olgan, 'left' — kanaldan chiqqan
CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    number INT,
    full_name TEXT NOT NULL DEFAULT '',
    username TEXT,
    phone TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    is_winner BOOLEAN NOT NULL DEFAULT FALSE,
    won_at TIMESTAMPTZ,
    notified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    joined_at TIMESTAMPTZ,
    PRIMARY KEY (giveaway_id, user_id),
    UNIQUE (giveaway_id, number)
);
CREATE INDEX IF NOT EXISTS giveaway_entries_status_idx ON giveaway_entries(giveaway_id, status);
-- botda kimga post yuborilgani (message_id NULL — yuborib bo'lmadi, masalan botni bloklagan)
CREATE TABLE IF NOT EXISTS giveaway_messages (
    giveaway_id INT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (giveaway_id, user_id)
);-- Premium va Reklama uchun qo'lda qo'shilgan kanal/guruhlar (Obunalar ro'yxatidan alohida)
CREATE TABLE IF NOT EXISTS post_targets (
    id SERIAL PRIMARY KEY,
    chat_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Premium postining kanal/guruhlardagi nusxalari (tahrirda hammasi yangilanadi)
CREATE TABLE IF NOT EXISTS giveaway_posts (
    giveaway_id INT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (giveaway_id, chat_id, message_id)
);
-- Reklama: status draft | scheduled | sending | paused | done | stopped | deleted
CREATE TABLE IF NOT EXISTS ads (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    media BYTEA,
    media_type TEXT,
    media_name TEXT,
    media_ver INT NOT NULL DEFAULT 0,
    file_id TEXT,
    buttons TEXT NOT NULL DEFAULT '',
    to_bot BOOLEAN NOT NULL DEFAULT TRUE,
    audience TEXT NOT NULL DEFAULT 'all',
    audience_days INT NOT NULL DEFAULT 7,
    audience_users TEXT NOT NULL DEFAULT '',
    targets TEXT[] NOT NULL DEFAULT '{}',
    silent BOOLEAN NOT NULL DEFAULT FALSE,
    no_preview BOOLEAN NOT NULL DEFAULT FALSE,
    protect BOOLEAN NOT NULL DEFAULT FALSE,
    pin BOOLEAN NOT NULL DEFAULT FALSE,
    delete_after INT,
    status TEXT NOT NULL DEFAULT 'draft',
    scheduled_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- har bir qabul qiluvchi: queued | sent | blocked | failed | skipped | deleted
CREATE TABLE IF NOT EXISTS ad_deliveries (
    ad_id INT NOT NULL REFERENCES ads(id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    message_id BIGINT,
    status TEXT NOT NULL DEFAULT 'queued',
    sent_at TIMESTAMPTZ,
    PRIMARY KEY (ad_id, chat_id)
);
CREATE INDEX IF NOT EXISTS ad_deliveries_status_idx ON ad_deliveries(ad_id, status);
CREATE INDEX IF NOT EXISTS ad_deliveries_sent_idx ON ad_deliveries(status, sent_at);
CREATE TABLE IF NOT EXISTS ad_clicks (
    ad_id INT NOT NULL REFERENCES ads(id) ON DELETE CASCADE,
    button INT NOT NULL,
    clicks INT NOT NULL DEFAULT 0,
    PRIMARY KEY (ad_id, button)
);
"""


def _local(col: str, tz_arg: str) -> str:
    """timestamptz ustunini mahalliy vaqtga (UTC + N soat) o'tkazadigan SQL ifoda."""
    return f"({col} AT TIME ZONE 'UTC' + make_interval(hours => {tz_arg}))"


async def init(retries: int = 30):
    """Pool yaratadi va jadvallarni tayyorlaydi (baza ishga tushishini kutadi)."""
    import asyncio

    global pool
    for i in range(retries):
        try:
            pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=20)
            break
        except (OSError, asyncpg.PostgresError):
            if i == retries - 1:
                raise
            await asyncio.sleep(2)
    async with pool.acquire() as c:
        await c.execute(SCHEMA)


async def _all(sql, *args):
    return [tuple(r) for r in await pool.fetch(sql, *args)]


async def _one(sql, *args):
    r = await pool.fetchrow(sql, *args)
    return tuple(r) if r else None


# --- users ---
async def upsert_user(user_id, username):
    await pool.execute(
        "INSERT INTO users(user_id, username) VALUES($1, $2) "
        "ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username",
        user_id, username,
    )


async def confirm_social(user_id):
    await pool.execute(
        "UPDATE users SET social_confirmed=TRUE, social_at=now() WHERE user_id=$1", user_id
    )


async def is_social_confirmed(user_id) -> bool:
    """Oxirgi faol havola qo'shilganidan keyin «obuna bo'ldim» bosilganmi."""
    return bool(await pool.fetchval(
        "SELECT social_at IS NOT NULL AND social_at >= COALESCE("
        "(SELECT MAX(created_at) FROM channels WHERE kind<>'telegram' AND is_active), '-infinity') "
        "FROM users WHERE user_id=$1",
        user_id,
    ))


# --- settings ---
async def get_setting(key):
    return await pool.fetchval("SELECT value FROM settings WHERE key=$1", key)


async def set_setting(key, value):
    await pool.execute(
        "INSERT INTO settings(key, value) VALUES($1, $2) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        key, value,
    )


# --- channels (obunalar) ---
async def channels(active_only=False):
    """[(id, kind, title, target, url, position, is_active), ...] tartib bo'yicha"""
    return await _all(
        "SELECT id, kind, title, target, url, position, is_active FROM channels "
        + ("WHERE is_active " if active_only else "")
        + "ORDER BY position, id"
    )


async def add_channel(kind, title, target, url, created_at=None):
    return await pool.fetchval(
        "INSERT INTO channels(kind, title, target, url, position, created_at) "
        "VALUES($1, $2, $3, $4, (SELECT COALESCE(MAX(position), 0) + 1 FROM channels), "
        "COALESCE($5::timestamptz, now())) RETURNING id",
        kind, title, target, url, created_at,
    )


async def set_channel_active(channel_id, active: bool):
    await pool.execute("UPDATE channels SET is_active=$2 WHERE id=$1", channel_id, active)


async def delete_channel(channel_id):
    await pool.execute("DELETE FROM channels WHERE id=$1", channel_id)


async def move_channel(channel_id, step: int):
    """step=-1 — yuqoriga, +1 — pastga."""
    async with pool.acquire() as c, c.transaction():
        ids = [r[0] for r in await c.fetch("SELECT id FROM channels ORDER BY position, id FOR UPDATE")]
        if channel_id not in ids:
            return
        i = ids.index(channel_id)
        j = i + step
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        await c.executemany("UPDATE channels SET position=$2 WHERE id=$1",
                            [(cid, pos) for pos, cid in enumerate(ids, 1)])


async def channels_count():
    return await pool.fetchval("SELECT COUNT(*) FROM channels")


# --- admins ---
async def admins():
    """[(id, username, is_super, created_at, last_login), ...]"""
    return await _all(
        "SELECT id, username, is_super, created_at, last_login FROM admins ORDER BY is_super DESC, id"
    )


async def admin_by_name(username):
    """(id, username, password_hash, is_super) yoki None"""
    return await _one(
        "SELECT id, username, password_hash, is_super FROM admins WHERE username=$1", username.lower()
    )


async def admin_by_id(admin_id):
    return await _one("SELECT id, username, password_hash, is_super FROM admins WHERE id=$1", admin_id)


async def add_admin(username, password_hash, is_super) -> int | None:
    """None — bunday login band."""
    return await pool.fetchval(
        "INSERT INTO admins(username, password_hash, is_super) VALUES($1, $2, $3) "
        "ON CONFLICT(username) DO NOTHING RETURNING id",
        username.lower(), password_hash, is_super,
    )


async def set_admin_password(admin_id, password_hash):
    await pool.execute("UPDATE admins SET password_hash=$2 WHERE id=$1", admin_id, password_hash)


async def set_admin_role(admin_id, is_super: bool):
    await pool.execute("UPDATE admins SET is_super=$2 WHERE id=$1", admin_id, is_super)


async def delete_admin(admin_id):
    await pool.execute("DELETE FROM admins WHERE id=$1", admin_id)


async def touch_admin_login(admin_id):
    await pool.execute("UPDATE admins SET last_login=now() WHERE id=$1", admin_id)


async def admins_count(super_only=False):
    return await pool.fetchval(
        "SELECT COUNT(*) FROM admins" + (" WHERE is_super" if super_only else "")
    )


# --- polls ---
async def active_poll():
    """(id, title, message_id, ends_at) yoki None"""
    return await _one(
        "SELECT id, title, message_id, ends_at FROM polls WHERE is_active ORDER BY id DESC LIMIT 1"
    )


async def create_poll(title, ends_at, description=""):
    async with pool.acquire() as c, c.transaction():
        await c.execute("UPDATE polls SET is_active=FALSE WHERE is_active")
        return await c.fetchval(
            "INSERT INTO polls(title, ends_at, description) VALUES($1, $2, $3) RETURNING id",
            title, ends_at, description,
        )


async def set_ends_at(poll_id, ends_at):
    await pool.execute("UPDATE polls SET ends_at=$1 WHERE id=$2", ends_at, poll_id)


async def poll_description(poll_id) -> str:
    return await pool.fetchval("SELECT description FROM polls WHERE id=$1", poll_id) or ""


async def set_description(poll_id, text):
    await pool.execute("UPDATE polls SET description=$1 WHERE id=$2", text, poll_id)


async def image_ver(poll_id) -> int | None:
    """Rasm bo'lsa uning versiyasi, bo'lmasa None"""
    return await pool.fetchval(
        "SELECT CASE WHEN image IS NULL THEN NULL ELSE image_ver END FROM polls WHERE id=$1", poll_id
    )


async def get_image(poll_id, ver):
    return await pool.fetchval("SELECT image FROM polls WHERE id=$1 AND image_ver=$2", poll_id, ver)


async def set_image(poll_id, data: bytes | None):
    await pool.execute(
        "UPDATE polls SET image=$1, image_ver=image_ver+1 WHERE id=$2", data, poll_id
    )


async def close_poll(poll_id):
    await pool.execute("UPDATE polls SET is_active=FALSE WHERE id=$1", poll_id)


async def last_poll():
    """Faol bo'lmasa ham oxirgi so'rovnoma: (id, title, message_id, ends_at, is_active)"""
    return await _one(
        "SELECT id, title, message_id, ends_at, is_active FROM polls ORDER BY id DESC LIMIT 1"
    )


async def get_poll(poll_id):
    """(id, title, message_id, ends_at, is_active, created_at) yoki None"""
    return await _one(
        "SELECT id, title, message_id, ends_at, is_active, created_at FROM polls WHERE id=$1",
        poll_id,
    )


async def polls_overview():
    """[(id, title, is_active, created_at, ends_at, jami_ovoz, peshqadam), ...] yangidan eskiga"""
    return await _all(
        "SELECT p.id, p.title, p.is_active, p.created_at, p.ends_at, "
        "(SELECT COUNT(*) FROM votes v WHERE v.poll_id=p.id), "
        "(SELECT c.name FROM candidates c JOIN votes v ON v.candidate_id=c.id "
        " WHERE c.poll_id=p.id GROUP BY c.id ORDER BY COUNT(*) DESC, c.id LIMIT 1) "
        "FROM polls p ORDER BY p.id DESC"
    )


async def set_message_id(poll_id, message_id):
    await pool.execute("UPDATE polls SET message_id=$1 WHERE id=$2", message_id, poll_id)


# --- candidates ---
async def add_candidate(poll_id, name):
    return await pool.fetchval(
        "INSERT INTO candidates(poll_id, name) VALUES($1, $2) RETURNING id", poll_id, name
    )


async def rename_candidate(candidate_id, name):
    await pool.execute("UPDATE candidates SET name=$1 WHERE id=$2", name, candidate_id)


async def delete_candidate(candidate_id):
    await pool.execute("DELETE FROM candidates WHERE id=$1", candidate_id)


async def candidates(poll_id):
    return await _all("SELECT id, name FROM candidates WHERE poll_id=$1 ORDER BY id", poll_id)


# --- votes ---
async def add_vote(user_id, poll_id, candidate_id) -> bool:
    """True — ovoz qabul qilindi, False — avval ovoz bergan yoki nomzod yo'q."""
    try:
        res = await pool.execute(
            "INSERT INTO votes(user_id, poll_id, candidate_id) "
            "SELECT $1, $2, c.id FROM candidates c WHERE c.id=$3 AND c.poll_id=$2 "
            "ON CONFLICT DO NOTHING",
            user_id, poll_id, candidate_id,
        )
    except asyncpg.PostgresError:
        return False
    return res.endswith(" 1")


async def remove_user_votes(user_id, poll_id):
    await pool.execute("DELETE FROM votes WHERE user_id=$1 AND poll_id=$2", user_id, poll_id)


async def results(poll_id):
    """[(id, name, count), ...] ko'pdan kamga"""
    return await _all(
        "SELECT c.id, c.name, COUNT(v.user_id) AS n FROM candidates c "
        "LEFT JOIN votes v ON v.candidate_id=c.id "
        "WHERE c.poll_id=$1 GROUP BY c.id ORDER BY n DESC, c.id",
        poll_id,
    )


async def counts_by_id(poll_id):
    """[(id, name, count), ...] qo'shilgan tartibda (post tugmalari uchun)"""
    return await _all(
        "SELECT c.id, c.name, COUNT(v.user_id) AS n FROM candidates c "
        "LEFT JOIN votes v ON v.candidate_id=c.id "
        "WHERE c.poll_id=$1 GROUP BY c.id ORDER BY c.id",
        poll_id,
    )


# --- ulashilgan nusxalar ---
async def add_shared(inline_message_id, poll_id):
    await pool.execute(
        "INSERT INTO shared_posts(inline_message_id, poll_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
        inline_message_id, poll_id,
    )


async def shared_posts(poll_id, limit=300):
    """Eng yangi nusxalarning inline_message_id lari"""
    rows = await pool.fetch(
        "SELECT inline_message_id FROM shared_posts WHERE poll_id=$1 ORDER BY created_at DESC LIMIT $2",
        poll_id, limit,
    )
    return [r[0] for r in rows]


async def delete_shared(inline_message_id):
    await pool.execute("DELETE FROM shared_posts WHERE inline_message_id=$1", inline_message_id)


async def votes_per_day(poll_id, tz_offset):
    """[(sana, soni), ...] mahalliy vaqt bo'yicha"""
    return await _all(
        f"SELECT {_local('created_at', '$2')}::date, COUNT(*) FROM votes "
        "WHERE poll_id=$1 GROUP BY 1 ORDER BY 1",
        poll_id, tz_offset,
    )


async def votes_per_hour(poll_id, tz_offset):
    """[(soat 0..23, soni), ...] — kunning qaysi soatlarida ovoz berilgani"""
    return await _all(
        f"SELECT EXTRACT(HOUR FROM {_local('created_at', '$2')})::int, COUNT(*) FROM votes "
        "WHERE poll_id=$1 GROUP BY 1 ORDER BY 1",
        poll_id, tz_offset,
    )


async def votes_since(poll_id, since=None) -> int:
    """since (timestamptz) dan beri berilgan ovozlar; since=None — hammasi."""
    return await pool.fetchval(
        "SELECT COUNT(*) FROM votes WHERE poll_id=$1 AND created_at >= COALESCE($2::timestamptz, '-infinity')",
        poll_id, since,
    )


# --- users (admin panel) ---
async def users_stats(since):
    """(jami, since dan beri yangi, ijtimoiy tarmoqni tasdiqlaganlar)"""
    return await _one(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE created_at >= $1), "
        "COUNT(*) FILTER (WHERE social_confirmed) FROM users",
        since,
    )


async def users_list(poll_id, q="", voted="all", limit=50, offset=0):
    """([(user_id, username, social_confirmed, created_at, nomzod, ovoz_vaqti), ...], jami_mos)

    voted: 'all' | 'voted' | 'not' — poll_id so'rovnomasida ovoz berganlik bo'yicha filtr.
    """
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await pool.fetch(
        "SELECT u.user_id, u.username, u.social_confirmed, u.created_at, c.name, v.created_at, "
        "COUNT(*) OVER() FROM users u "
        "LEFT JOIN votes v ON v.user_id=u.user_id AND v.poll_id=$1 "
        "LEFT JOIN candidates c ON c.id=v.candidate_id "
        "WHERE ($2='' OR u.username ILIKE '%'||$2||'%' OR u.user_id::text LIKE $2||'%') "
        "AND ($3='all' OR ($3='voted') = (v.user_id IS NOT NULL)) "
        "ORDER BY u.created_at DESC, u.user_id DESC LIMIT $4 OFFSET $5",
        poll_id, q, voted, limit, offset,
    )
    return [tuple(r)[:6] for r in rows], (rows[0][6] if rows else 0)


async def export_votes(poll_id):
    return await _all(
        "SELECT v.user_id, u.username, c.name, v.created_at FROM votes v "
        "JOIN candidates c ON c.id=v.candidate_id "
        "LEFT JOIN users u ON u.user_id=v.user_id WHERE v.poll_id=$1 ORDER BY v.created_at",
        poll_id,
    )


# --- premium (Telegram Premium konkursi) ---
GW_FIRST_NUMBER = 70001
_GW_COLS = ("id, title, description, button_text, image_ver, ends_at, is_active, message_id, created_at, "
            "image IS NOT NULL AS has_image")


async def gw_create(title, description, button_text, ends_at) -> int:
    """Yangi konkurs; avvalgi faol konkurs yopiladi."""
    async with pool.acquire() as c, c.transaction():
        await c.execute("UPDATE giveaways SET is_active=FALSE WHERE is_active")
        return await c.fetchval(
            "INSERT INTO giveaways(title, description, button_text, ends_at) VALUES($1, $2, $3, $4) RETURNING id",
            title, description, button_text, ends_at,
        )


async def gw_get(gid):
    return await pool.fetchrow(f"SELECT {_GW_COLS} FROM giveaways WHERE id=$1", gid)


async def gw_active():
    return await pool.fetchrow(f"SELECT {_GW_COLS} FROM giveaways WHERE is_active ORDER BY id DESC LIMIT 1")


async def gw_last():
    return await pool.fetchrow(f"SELECT {_GW_COLS} FROM giveaways ORDER BY id DESC LIMIT 1")


async def gw_list():
    """Barcha konkurslar yangidan eskiga, ishtirokchilar soni bilan"""
    return await pool.fetch(
        "SELECT g.id, g.title, g.is_active, g.created_at, g.ends_at, "
        "(SELECT COUNT(*) FROM giveaway_entries e WHERE e.giveaway_id=g.id AND e.number IS NOT NULL) AS joined "
        "FROM giveaways g ORDER BY g.id DESC"
    )


async def gw_update(gid, title, description, button_text, ends_at):
    await pool.execute(
        "UPDATE giveaways SET title=$2, description=$3, button_text=$4, ends_at=$5 WHERE id=$1",
        gid, title, description, button_text, ends_at,
    )


async def gw_set_ends(gid, ends_at):
    await pool.execute("UPDATE giveaways SET ends_at=$2 WHERE id=$1", gid, ends_at)


async def gw_set_image(gid, data: bytes | None):
    await pool.execute(
        "UPDATE giveaways SET image=$2, image_ver=image_ver+1, file_id=NULL WHERE id=$1", gid, data
    )


async def gw_get_image(gid, ver):
    return await pool.fetchval("SELECT image FROM giveaways WHERE id=$1 AND image_ver=$2", gid, ver)


async def gw_file(gid):
    """(image, image_ver, file_id) — post rasmi va uning Telegramdagi file_id si"""
    return await pool.fetchrow("SELECT image, image_ver, file_id FROM giveaways WHERE id=$1", gid)


async def gw_set_file_id(gid, ver, file_id):
    await pool.execute("UPDATE giveaways SET file_id=$3 WHERE id=$1 AND image_ver=$2", gid, ver, file_id)


async def gw_set_message_id(gid, message_id):
    await pool.execute("UPDATE giveaways SET message_id=$2 WHERE id=$1", gid, message_id)


async def gw_entry(gid, user_id):
    return await pool.fetchrow(
        "SELECT user_id, number, full_name, username, phone, status, is_winner FROM giveaway_entries "
        "WHERE giveaway_id=$1 AND user_id=$2",
        gid, user_id,
    )


async def gw_touch(gid, user_id, full_name, username):
    """Boshlashni bosgan foydalanuvchini yozib qo'yadi (hali raqamsiz)."""
    await pool.execute(
        "INSERT INTO giveaway_entries(giveaway_id, user_id, full_name, username) VALUES($1, $2, $3, $4) "
        "ON CONFLICT(giveaway_id, user_id) DO UPDATE SET full_name=EXCLUDED.full_name, username=EXCLUDED.username",
        gid, user_id, full_name, username,
    )


async def gw_join(gid, user_id, full_name, username) -> int:
    """Ishtirok raqamini beradi (avval berilgan bo'lsa, o'shani qaytaradi) va holatni 'joined' qiladi."""
    async with pool.acquire() as c, c.transaction():
        # raqamlar ketma-ket va takrorlanmasligi uchun konkurs qatori qulflanadi
        await c.execute("SELECT 1 FROM giveaways WHERE id=$1 FOR UPDATE", gid)
        number = await c.fetchval(
            "SELECT number FROM giveaway_entries WHERE giveaway_id=$1 AND user_id=$2", gid, user_id
        )
        if number is None:
            number = await c.fetchval(
                "SELECT COALESCE(MAX(number), $2) + 1 FROM giveaway_entries WHERE giveaway_id=$1",
                gid, GW_FIRST_NUMBER - 1,
            )
        await c.execute(
            "INSERT INTO giveaway_entries(giveaway_id, user_id, number, full_name, username, status, joined_at) "
            "VALUES($1, $2, $3, $4, $5, 'joined', now()) ON CONFLICT(giveaway_id, user_id) DO UPDATE SET "
            "number=EXCLUDED.number, full_name=EXCLUDED.full_name, username=EXCLUDED.username, status='joined', "
            "joined_at=COALESCE(giveaway_entries.joined_at, now())",
            gid, user_id, number, full_name, username,
        )
        return number


async def gw_set_phone(gid, user_id, phone) -> bool:
    res = await pool.execute(
        "UPDATE giveaway_entries SET phone=$3 WHERE giveaway_id=$1 AND user_id=$2", gid, user_id, phone
    )
    return res.endswith(" 1")


async def gw_mark_left(gid, user_id):
    await pool.execute(
        "UPDATE giveaway_entries SET status='left' WHERE giveaway_id=$1 AND user_id=$2 AND status='joined'",
        gid, user_id,
    )


async def gw_mark_back(gid, user_id):
    await pool.execute(
        "UPDATE giveaway_entries SET status='joined' WHERE giveaway_id=$1 AND user_id=$2 AND status='left'",
        gid, user_id,
    )


async def gw_stats(gid, since):
    """started — hammasi (Boshlashni bosganlar), joined/left/pending — holatlar, today — bugun raqam olganlar"""
    return await pool.fetchrow(
        "SELECT COUNT(*) AS started, COUNT(*) FILTER (WHERE status='joined') AS joined, "
        "COUNT(*) FILTER (WHERE status='left') AS left_n, COUNT(*) FILTER (WHERE status='started') AS pending, "
        "COUNT(*) FILTER (WHERE joined_at >= $2) AS today, COUNT(*) FILTER (WHERE phone IS NOT NULL) AS phones "
        "FROM giveaway_entries WHERE giveaway_id=$1",
        gid, since,
    )


async def gw_entries(gid, q="", status="all", limit=50, offset=0):
    """(qatorlar, jami_mos). q — ism, username, ID, telefon yoki ishtirok raqami bo'yicha qidiruv."""
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await pool.fetch(
        "SELECT user_id, number, full_name, username, phone, status, is_winner, "
        "COALESCE(joined_at, created_at) AS at, COUNT(*) OVER() AS total FROM giveaway_entries "
        "WHERE giveaway_id=$1 AND ($2='' OR full_name ILIKE '%'||$2||'%' "
        "OR username ILIKE '%'||ltrim($2, '@')||'%' OR user_id::text LIKE $2||'%' "
        "OR phone LIKE '%'||$2||'%' OR number::text LIKE ltrim($2, '#')||'%') "
        "AND ($3='all' OR status=$3) "
        "ORDER BY COALESCE(joined_at, created_at) DESC, user_id DESC LIMIT $4 OFFSET $5",
        gid, q, status, limit, offset,
    )
    return rows, (rows[0]["total"] if rows else 0)


async def gw_random_entry(gid):
    """Obunada qolgan, hali g'olib bo'lmagan tasodifiy ishtirokchi"""
    return await pool.fetchrow(
        "SELECT user_id, number, full_name, username, phone FROM giveaway_entries "
        "WHERE giveaway_id=$1 AND status='joined' AND NOT is_winner ORDER BY random() LIMIT 1",
        gid,
    )


async def gw_mark_winner(gid, user_id):
    await pool.execute(
        "UPDATE giveaway_entries SET is_winner=TRUE, won_at=now() WHERE giveaway_id=$1 AND user_id=$2",
        gid, user_id,
    )


async def gw_winners(gid):
    return await pool.fetch(
        "SELECT user_id, number, full_name, username, phone, status, won_at, notified_at FROM giveaway_entries "
        "WHERE giveaway_id=$1 AND is_winner ORDER BY won_at",
        gid,
    )


async def gw_set_notified(gid, user_id):
    await pool.execute(
        "UPDATE giveaway_entries SET notified_at=now() WHERE giveaway_id=$1 AND user_id=$2", gid, user_id
    )


async def gw_unsent_users(gid) -> list[int]:
    """Bot foydalanuvchilaridan hali bu post yuborilmaganlar"""
    rows = await pool.fetch(
        "SELECT u.user_id FROM users u WHERE NOT EXISTS (SELECT 1 FROM giveaway_messages m "
        "WHERE m.giveaway_id=$1 AND m.user_id=u.user_id) ORDER BY u.created_at",
        gid,
    )
    return [r[0] for r in rows]


async def gw_unsent_count(gid) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM users u WHERE NOT EXISTS (SELECT 1 FROM giveaway_messages m "
        "WHERE m.giveaway_id=$1 AND m.user_id=u.user_id)",
        gid,
    )


async def gw_add_message(gid, user_id, message_id):
    await pool.execute(
        "INSERT INTO giveaway_messages(giveaway_id, user_id, message_id) VALUES($1, $2, $3) "
        "ON CONFLICT DO NOTHING",
        gid, user_id, message_id,
    )


async def gw_messages(gid):
    """[(user_id, message_id), ...] — botda yuborilgan nusxalar (tahrirlash uchun)"""
    rows = await pool.fetch(
        "SELECT user_id, message_id FROM giveaway_messages WHERE giveaway_id=$1 AND message_id IS NOT NULL", gid
    )
    return [tuple(r) for r in rows]


async def gw_drop_message(gid, user_id):
    """Nusxa o'chirilgan yoki foydalanuvchi botni bloklagan — endi tahrirlanmaydi."""
    await pool.execute(
        "UPDATE giveaway_messages SET message_id=NULL WHERE giveaway_id=$1 AND user_id=$2", gid, user_id
    )


async def gw_message_stats(gid):
    """(yetkazilgan, yuborib bo'lmagan)"""
    r = await pool.fetchrow(
        "SELECT COUNT(*) FILTER (WHERE message_id IS NOT NULL), COUNT(*) FILTER (WHERE message_id IS NULL) "
        "FROM giveaway_messages WHERE giveaway_id=$1",
        gid,
    )
    return r[0], r[1]


# --- joylash joylari (qo'lda qo'shilgan kanal/guruhlar) ---
async def targets_list():
    """[(id, chat_id, title), ...]"""
    return await _all("SELECT id, chat_id, title FROM post_targets ORDER BY id")


async def target_add(chat_id, title):
    return await pool.fetchval(
        "INSERT INTO post_targets(chat_id, title) VALUES($1, $2) ON CONFLICT(chat_id) DO NOTHING RETURNING id",
        chat_id, title,
    )


async def target_delete(target_id):
    await pool.execute("DELETE FROM post_targets WHERE id=$1", target_id)


async def gw_add_post(gid, chat_id, message_id):
    await pool.execute(
        "INSERT INTO giveaway_posts(giveaway_id, chat_id, message_id) VALUES($1, $2, $3) ON CONFLICT DO NOTHING",
        gid, chat_id, message_id,
    )


async def gw_posts(gid):
    """[(chat_id, message_id), ...] — Premium postining kanal/guruhlardagi nusxalari"""
    return await _all(
        "SELECT chat_id, message_id FROM giveaway_posts WHERE giveaway_id=$1 ORDER BY created_at", gid
    )


# --- reklama ---
_AD_COLS = ("id, title, text, media_type, media_name, media_ver, media IS NOT NULL AS has_media, buttons, to_bot, "
            "audience, audience_days, audience_users, targets, silent, no_preview, protect, pin, delete_after, "
            "status, scheduled_at, started_at, finished_at, created_by, created_at")
_AD_COPY = ("title, text, media, media_type, media_name, buttons, to_bot, audience, audience_days, audience_users, "
            "targets, silent, no_preview, protect, pin, delete_after")

# auditoriya: $1 — N kun, $2 — aniq akkauntlar ID lari
_AUDIENCE = {
    "all": "TRUE",
    "voted": "EXISTS (SELECT 1 FROM votes v WHERE v.user_id=u.user_id AND v.poll_id=(SELECT MAX(id) FROM polls))",
    "not_voted": "NOT EXISTS (SELECT 1 FROM votes v WHERE v.user_id=u.user_id "
                 "AND v.poll_id=(SELECT MAX(id) FROM polls))",
    "premium": "EXISTS (SELECT 1 FROM giveaway_entries e WHERE e.user_id=u.user_id AND e.number IS NOT NULL "
               "AND e.giveaway_id=(SELECT MAX(id) FROM giveaways))",
    "not_premium": "NOT EXISTS (SELECT 1 FROM giveaway_entries e WHERE e.user_id=u.user_id AND e.number IS NOT NULL "
                   "AND e.giveaway_id=(SELECT MAX(id) FROM giveaways))",
    "new": "u.created_at >= now() - make_interval(days => $1)",
    "users": "u.user_id = ANY($2::bigint[])",
}


def _audience_sql(select: str, kind: str) -> str:
    where = _AUDIENCE.get(kind, "FALSE")
    # ikkala parametr ham har doim ishlatilsin (PostgreSQL turini aniqlay olishi uchun)
    return f"SELECT {select} FROM users u WHERE ({where}) AND $1::int IS NOT NULL AND $2::bigint[] IS NOT NULL"


async def ad_audience(kind, days=7, ids=()) -> list[int]:
    rows = await pool.fetch(_audience_sql("u.user_id", kind), days, list(ids))
    return [r[0] for r in rows]


async def ad_audience_count(kind, days=7, ids=()) -> int:
    return await pool.fetchval(_audience_sql("COUNT(*)", kind), days, list(ids))


async def users_existing(ids) -> set[int]:
    rows = await pool.fetch("SELECT user_id FROM users WHERE user_id = ANY($1::bigint[])", list(ids))
    return {r[0] for r in rows}


async def users_by_usernames(names) -> dict[str, int]:
    """{username (kichik harfda): user_id}"""
    rows = await pool.fetch(
        "SELECT lower(username), user_id FROM users WHERE lower(username) = ANY($1::text[])", list(names)
    )
    return {r[0]: r[1] for r in rows}


async def ad_create(title, created_by) -> int:
    return await pool.fetchval(
        "INSERT INTO ads(title, created_by) VALUES($1, $2) RETURNING id", title, created_by
    )


async def ad_copy(ad_id, created_by) -> int | None:
    return await pool.fetchval(
        f"INSERT INTO ads({_AD_COPY}, created_by) SELECT {_AD_COPY}, $2 FROM ads WHERE id=$1 RETURNING id",
        ad_id, created_by,
    )


async def ad_get(ad_id):
    return await pool.fetchrow(f"SELECT {_AD_COLS} FROM ads WHERE id=$1", ad_id)


async def ad_list():
    """Reklamalar yangidan eskiga: yuborish va bosish sonlari bilan"""
    return await pool.fetch(
        "SELECT a.id, a.title, a.status, a.to_bot, a.audience, a.audience_days, cardinality(a.targets) AS n_targets, "
        "a.scheduled_at, a.started_at, a.finished_at, a.created_at, a.created_by, "
        "COALESCE(d.total, 0) AS total, COALESCE(d.sent, 0) AS sent, COALESCE(c.clicks, 0) AS clicks FROM ads a "
        "LEFT JOIN (SELECT ad_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE status IN ('sent', 'deleted')) AS sent "
        "FROM ad_deliveries GROUP BY ad_id) d ON d.ad_id=a.id "
        "LEFT JOIN (SELECT ad_id, SUM(clicks) AS clicks FROM ad_clicks GROUP BY ad_id) c ON c.ad_id=a.id "
        "ORDER BY a.id DESC"
    )


async def ad_update(ad_id, *, title, text, buttons, to_bot, audience, audience_days, audience_users, targets,
                    silent, no_preview, protect, pin, delete_after):
    await pool.execute(
        "UPDATE ads SET title=$2, text=$3, buttons=$4, to_bot=$5, audience=$6, audience_days=$7, audience_users=$8, "
        "targets=$9::text[], silent=$10, no_preview=$11, protect=$12, pin=$13, delete_after=$14 WHERE id=$1",
        ad_id, title, text, buttons, to_bot, audience, audience_days, audience_users, list(targets),
        silent, no_preview, protect, pin, delete_after,
    )


async def ad_set_media(ad_id, data, media_type, name):
    await pool.execute(
        "UPDATE ads SET media=$2, media_type=$3, media_name=$4, media_ver=media_ver+1, file_id=NULL WHERE id=$1",
        ad_id, data, media_type, name,
    )


async def ad_get_media(ad_id, ver):
    """(media, media_type, media_name) yoki None"""
    return await pool.fetchrow(
        "SELECT media, media_type, media_name FROM ads WHERE id=$1 AND media_ver=$2 AND media IS NOT NULL",
        ad_id, ver,
    )


async def ad_file(ad_id):
    return await pool.fetchrow("SELECT media, media_type, media_name, media_ver, file_id FROM ads WHERE id=$1", ad_id)


async def ad_set_file_id(ad_id, ver, file_id):
    await pool.execute("UPDATE ads SET file_id=$3 WHERE id=$1 AND media_ver=$2", ad_id, ver, file_id)


async def ad_set_status(ad_id, status):
    await pool.execute("UPDATE ads SET status=$2 WHERE id=$1", ad_id, status)


async def ad_schedule(ad_id, at):
    await pool.execute("UPDATE ads SET status='scheduled', scheduled_at=$2 WHERE id=$1", ad_id, at)


async def ad_set_scheduled_at(ad_id, at):
    await pool.execute("UPDATE ads SET scheduled_at=$2 WHERE id=$1", ad_id, at)


async def ad_delete(ad_id):
    await pool.execute("DELETE FROM ads WHERE id=$1", ad_id)


async def ad_queue(ad_id, recipients) -> bool:
    """Qabul qiluvchilarni navbatga yozadi va holatni 'sending' qiladi (faqat qoralama/rejalashtirilgan bo'lsa)."""
    async with pool.acquire() as c, c.transaction():
        ok = await c.fetchval(
            "UPDATE ads SET status='sending', started_at=now() WHERE id=$1 AND status IN ('draft', 'scheduled') "
            "RETURNING TRUE", ad_id,
        )
        if not ok:
            return False
        await c.execute(
            "INSERT INTO ad_deliveries(ad_id, chat_id) SELECT $1, unnest($2::text[]) ON CONFLICT DO NOTHING",
            ad_id, list(recipients),
        )
        return True


async def ad_finish(ad_id, status):
    await pool.execute("UPDATE ads SET status=$2, finished_at=now() WHERE id=$1", ad_id, status)


async def ad_next_batch(ad_id, limit=100) -> list[str]:
    rows = await pool.fetch(
        "SELECT chat_id FROM ad_deliveries WHERE ad_id=$1 AND status='queued' LIMIT $2", ad_id, limit
    )
    return [r[0] for r in rows]


async def ad_mark(ad_id, chat_id, status, message_id=None):
    await pool.execute(
        "UPDATE ad_deliveries SET status=$3, message_id=COALESCE($4, message_id), "
        "sent_at=CASE WHEN $3='sent' THEN now() ELSE sent_at END WHERE ad_id=$1 AND chat_id=$2",
        ad_id, chat_id, status, message_id,
    )


async def ad_skip_rest(ad_id):
    await pool.execute("UPDATE ad_deliveries SET status='skipped' WHERE ad_id=$1 AND status='queued'", ad_id)


async def ad_counts(ad_id) -> dict[str, int]:
    rows = await pool.fetch("SELECT status, COUNT(*) FROM ad_deliveries WHERE ad_id=$1 GROUP BY status", ad_id)
    return {r[0]: r[1] for r in rows}


async def ad_sent_messages(ad_id):
    """[(chat_id, message_id), ...] — yetkazilgan va hali o'chirilmagan xabarlar"""
    return await _all(
        "SELECT chat_id, message_id FROM ad_deliveries WHERE ad_id=$1 AND status='sent' AND message_id IS NOT NULL",
        ad_id,
    )


async def ad_last_sent_at(ad_id):
    return await pool.fetchval("SELECT MAX(sent_at) FROM ad_deliveries WHERE ad_id=$1", ad_id)


async def ad_due_deletes(limit=200):
    """[(ad_id, chat_id, message_id), ...] — avto-o'chirish vaqti kelgan xabarlar"""
    return await _all(
        "SELECT d.ad_id, d.chat_id, d.message_id FROM ad_deliveries d JOIN ads a ON a.id=d.ad_id "
        "WHERE d.status='sent' AND d.message_id IS NOT NULL AND a.delete_after IS NOT NULL "
        "AND d.sent_at <= now() - make_interval(hours => a.delete_after) LIMIT $1",
        limit,
    )


async def ad_due_scheduled() -> list[int]:
    rows = await pool.fetch(
        "SELECT id FROM ads WHERE status='scheduled' AND scheduled_at <= now() ORDER BY scheduled_at"
    )
    return [r[0] for r in rows]


async def ad_sending_ids() -> list[int]:
    """Yuborilishi kerak bo'lganlar navbat tartibida"""
    rows = await pool.fetch("SELECT id FROM ads WHERE status='sending' ORDER BY started_at, id")
    return [r[0] for r in rows]


async def ad_click(ad_id, button):
    await pool.execute(
        "INSERT INTO ad_clicks(ad_id, button, clicks) SELECT $1, $2, 1 WHERE EXISTS (SELECT 1 FROM ads WHERE id=$1) "
        "ON CONFLICT(ad_id, button) DO UPDATE SET clicks=ad_clicks.clicks+1",
        ad_id, button,
    )


async def ad_clicks(ad_id) -> dict[int, int]:
    rows = await pool.fetch("SELECT button, clicks FROM ad_clicks WHERE ad_id=$1", ad_id)
    return {r[0]: r[1] for r in rows}
