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
