"""Personal Study Library - a small Flask + SQLite study organizer.

Run:  python app.py   then open http://127.0.0.1:5000
"""
import io
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from database import close_db, execute, execute_many, init_db, query

# ---------------------------------------------------------------------------
# Paths and settings
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_ROOT = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "study_library.db"
MB = 1024 * 1024

# Uploadable resource types: folder inside uploads/, allowed extensions, size limit.
FILE_TYPES = {
    "pdf":      {"folder": "pdfs",      "exts": ["pdf"],                        "max_mb": 50},
    "ppt":      {"folder": "ppts",      "exts": ["ppt", "pptx"],                "max_mb": 100},
    "document": {"folder": "documents", "exts": ["doc", "docx", "txt"],         "max_mb": 25},
    "image":    {"folder": "images",    "exts": ["jpg", "jpeg", "png", "webp"], "max_mb": 10},
}
LINK_TYPES = ("video", "link", "ai")
RESOURCE_TYPES = ("pdf", "ppt", "document", "video", "link", "image", "ai", "note")
TYPE_LABELS = {
    "pdf": "PDF", "ppt": "Presentation", "document": "Document", "video": "Video",
    "link": "Website", "image": "Image", "ai": "AI Link", "note": "Note",
}
TYPE_PLURALS = {
    "pdf": "PDFs", "ppt": "Presentations", "document": "Documents", "video": "Videos",
    "link": "Websites", "image": "Images", "ai": "AI Links", "note": "Notes",
}
AI_PROVIDERS = {"chatgpt": "ChatGPT", "gemini": "Gemini", "other": "Other AI"}
MIME_TYPES = {
    "pdf": "application/pdf",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
}
INLINE_EXTS = {"pdf", "txt", "jpg", "jpeg", "png", "webp"}  # browser can show these
SUBJECT_ICONS = [
    "book", "code", "network", "cpu", "database", "terminal", "flask", "atom", "calculator",
    "chart", "globe", "pen", "art", "music", "briefcase", "heart", "feather", "shield",
]
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def load_secret_key():
    """Use STUDY_LIBRARY_SECRET if set, else a random key saved next to app.py."""
    env_key = os.environ.get("STUDY_LIBRARY_SECRET")
    if env_key:
        return env_key
    key_file = BASE_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    DATABASE=str(DB_PATH),
    MAX_CONTENT_LENGTH=250 * MB,       # total size of one upload request
    MAX_FORM_MEMORY_SIZE=5 * MB,       # text fields (large notes)
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
)
app.teardown_appcontext(close_db)


def setup_storage():
    for cfg in FILE_TYPES.values():
        (UPLOAD_ROOT / cfg["folder"]).mkdir(parents=True, exist_ok=True)
    init_db(app.config["DATABASE"])


setup_storage()


# ---------------------------------------------------------------------------
# Request hooks: current user, CSRF protection, security headers
# ---------------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@app.before_request
def load_user():
    g.user = None
    if request.endpoint == "static":
        return
    user_id = session.get("user_id")
    if user_id is not None:
        g.user = query("SELECT id, username FROM users WHERE id = ?", (user_id,), one=True)
        if g.user is None:
            session.clear()


@app.before_request
def csrf_protect():
    if request.method != "POST":
        return
    expected = session.get("_csrf")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not expected or not sent or not secrets.compare_digest(expected, sent):
        abort(400, description="Your form expired or is invalid (CSRF check failed). "
                               "Please go back, refresh the page and try again.")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if resp.mimetype == "text/html":
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https://i.ytimg.com; media-src 'self' https: http:; "
            "frame-src 'self' https://www.youtube-nocookie.com https://www.youtube.com; "
            "object-src 'none'; base-uri 'self'; form-action 'self'",
        )
    if request.endpoint != "static":
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def clean_text(value, max_len, required=False, label="This field", strip=True):
    value = value or ""
    if strip:
        value = value.strip()
    if required and not value.strip():
        raise ValueError(f"{label} is required.")
    if len(value) > max_len:
        raise ValueError(f"{label} must be at most {max_len:,} characters.")
    return value


def clean_url(raw):
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Please enter a URL.")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(raw) > 2000:
        raise ValueError("Please enter a valid web address starting with http:// or https://")
    if any(ch.isspace() for ch in raw):
        raise ValueError("URLs cannot contain spaces.")
    return raw


def youtube_id(url):
    """Return the 11-character YouTube video id for a YouTube URL, else None."""
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    vid = None
    if host == "youtu.be":
        vid = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
                vid = parts[1]
    return vid if vid and YOUTUBE_ID_RE.match(vid) else None


def direct_video(url):
    path = urlparse(url or "").path.lower()
    return path.endswith((".mp4", ".webm", ".ogg", ".ogv"))


def detect_subtype(rtype, url, provider=None):
    if rtype == "video":
        return "youtube" if youtube_id(url) else "external"
    if rtype == "ai":
        if provider in AI_PROVIDERS:
            return provider
        host = (urlparse(url).hostname or "").lower()
        if host.endswith(("chatgpt.com", "openai.com")):
            return "chatgpt"
        if host.endswith(("gemini.google.com", "aistudio.google.com", "g.co")) or host == "gemini.google":
            return "gemini"
        return "other"
    return ""


def host_of(url):
    host = (urlparse(url or "").hostname or "")
    return host[4:] if host.startswith("www.") else host


def default_link_title(rtype, subtype, url):
    if rtype == "video" and subtype == "youtube":
        return "YouTube video"
    if rtype == "ai" and subtype == "chatgpt":
        return "ChatGPT conversation"
    if rtype == "ai" and subtype == "gemini":
        return "Gemini conversation"
    return host_of(url) or "Link"


def file_ext(res):
    name = res["file_path"] or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_previewable(res):
    t = res["type"]
    if t in ("pdf", "image", "note"):
        return True
    if t == "document":
        return file_ext(res) == "txt"
    if t == "video":
        return res["subtype"] == "youtube" or direct_video(res["url"])
    return False


def looks_like(ext, head):
    """Check the first bytes of a file so a renamed file can't pretend to be a PDF etc."""
    if ext == "pdf":
        return b"%PDF" in head[:1024]
    if ext in ("docx", "pptx"):
        return head.startswith(b"PK\x03\x04")
    if ext in ("doc", "ppt"):
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == "txt":
        return head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" not in head
    return False


def display_filename(raw):
    """A readable original file name (no folders, no control characters)."""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    return name.strip()[:200]


def save_upload(storage, rtype, user_id):
    """Validate and store one uploaded file. Returns (relative_path, original_name, size)."""
    cfg = FILE_TYPES[rtype]
    original = display_filename(storage.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    label = TYPE_LABELS[rtype]
    if ext not in cfg["exts"]:
        allowed = ", ".join("." + e for e in cfg["exts"])
        raise ValueError(f'"{original or "file"}" is not an allowed {label} file. Allowed: {allowed}')

    storage.stream.seek(0, os.SEEK_END)
    size = storage.stream.tell()
    storage.stream.seek(0)
    if size == 0:
        raise ValueError(f'"{original}" is empty.')
    if size > cfg["max_mb"] * MB:
        raise ValueError(f'"{original}" is too large. {label} files can be up to {cfg["max_mb"]} MB.')
    head = storage.stream.read(2048)
    storage.stream.seek(0)
    if not looks_like(ext, head):
        raise ValueError(f'"{original}" does not look like a real .{ext} file.')

    # Stored name is random + safe; the original name is only kept in the database.
    stored_name = secure_filename(f"{uuid.uuid4().hex}.{ext}")
    rel_dir = Path(cfg["folder"]) / str(int(user_id))
    (UPLOAD_ROOT / rel_dir).mkdir(parents=True, exist_ok=True)
    rel_path = (rel_dir / stored_name).as_posix()
    storage.save(UPLOAD_ROOT / rel_path)
    return rel_path, original or stored_name, size


def stored_path(rel_path):
    """Resolve a stored relative path, refusing anything outside uploads/."""
    if not rel_path:
        return None
    root = UPLOAD_ROOT.resolve()
    full = (root / rel_path).resolve()
    if root not in full.parents:
        return None
    return full


def existing_file(res):
    path = stored_path(res["file_path"])
    return path if path and path.is_file() else None


def remove_files(rel_paths):
    for rel in rel_paths:
        path = stored_path(rel)
        if path and path.is_file():
            try:
                path.unlink()
            except OSError:
                app.logger.warning("Could not delete file %s", path)


def safe_next(default):
    nxt = request.form.get("next") or request.args.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return default


def same_origin_referrer():
    ref = request.referrer or ""
    parsed = urlparse(ref)
    if parsed.netloc == request.host:
        return parsed.path + (("?" + parsed.query) if parsed.query else "")
    return None


def like_pattern(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def read_text_preview(path, limit=300_000):
    with open(path, "rb") as fh:
        data = fh.read(limit)
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    truncated = path.stat().st_size > limit
    return text, truncated


def touch_resource(resource_id):
    execute("UPDATE resources SET last_opened = datetime('now', 'localtime') WHERE id = ?",
            (resource_id,))


# ---------------------------------------------------------------------------
# Ownership checks - every lookup is restricted to the logged-in user
# ---------------------------------------------------------------------------
def owned_subject(subject_id):
    row = query("SELECT * FROM subjects WHERE id = ? AND user_id = ?",
                (subject_id, g.user["id"]), one=True)
    if row is None:
        abort(404)
    return row


def owned_unit(unit_id):
    row = query(
        """SELECT u.*, s.name AS subject_name, s.icon AS subject_icon
           FROM units u JOIN subjects s ON s.id = u.subject_id
           WHERE u.id = ? AND s.user_id = ?""",
        (unit_id, g.user["id"]), one=True)
    if row is None:
        abort(404)
    return row


def owned_resource(resource_id):
    row = query(
        """SELECT r.*, u.name AS unit_name, u.subject_id, s.name AS subject_name,
                  EXISTS(SELECT 1 FROM favorites f
                         WHERE f.resource_id = r.id AND f.user_id = ?) AS is_fav
           FROM resources r
           JOIN units u ON u.id = r.unit_id
           JOIN subjects s ON s.id = u.subject_id
           WHERE r.id = ? AND s.user_id = ?""",
        (g.user["id"], resource_id, g.user["id"]), one=True)
    if row is None:
        abort(404)
    return row


def units_of(subject_id):
    return query(
        """SELECT u.*, (SELECT COUNT(*) FROM resources r WHERE r.unit_id = u.id) AS resource_count
           FROM units u WHERE u.subject_id = ? ORDER BY u.position, u.id""",
        (subject_id,))


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------
@app.template_filter("filesize")
def filesize_filter(num):
    if not num:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return ""


@app.template_filter("nicedate")
def nicedate_filter(value):
    if not value:
        return ""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value
    today = datetime.now().date()
    if dt.date() == today:
        return "Today, " + dt.strftime("%H:%M")
    if dt.date() == today - timedelta(days=1):
        return "Yesterday, " + dt.strftime("%H:%M")
    return dt.strftime("%d %b %Y")


app.jinja_env.globals.update(
    csrf_token=csrf_token,
    SUBJECT_ICONS=SUBJECT_ICONS,
    TYPE_LABELS=TYPE_LABELS,
    TYPE_PLURALS=TYPE_PLURALS,
    RESOURCE_TYPES=RESOURCE_TYPES,
    FILE_TYPES=FILE_TYPES,
    LINK_TYPES=LINK_TYPES,
    AI_PROVIDERS=AI_PROVIDERS,
    is_previewable=is_previewable,
    file_ext=file_ext,
    host_of=host_of,
    youtube_id=youtube_id,
    direct_video=direct_video,
)


@app.context_processor
def inject_sidebar():
    subjects = []
    if getattr(g, "user", None):
        subjects = query("SELECT id, name, icon FROM subjects WHERE user_id = ? "
                         "ORDER BY name COLLATE NOCASE", (g.user["id"],))
    return {"sidebar_subjects": subjects}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("dashboard" if g.user else "login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        error = None
        if not USERNAME_RE.match(username):
            error = "Username must be 3-32 characters: letters, numbers, dot, dash or underscore."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif len(password) > 128:
            error = "Password is too long."
        elif password != confirm:
            error = "Passwords do not match."
        elif query("SELECT 1 FROM users WHERE username = ?", (username,), one=True):
            error = "That username is already taken."
        if error:
            flash(error, "error")
        else:
            user_id = execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                              (username, generate_password_hash(password)))
            session.clear()
            session.permanent = True
            session["user_id"] = user_id
            flash(f"Welcome, {username}! Your library is ready.", "success")
            return redirect(url_for("dashboard"))
    return render_template("register.html", username=username)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = query("SELECT * FROM users WHERE username = ?", (username,), one=True)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()  # new session on login (prevents session fixation)
            session.permanent = True
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html", username=username)


@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    uid = g.user["id"]
    q = (request.args.get("q") or "").strip()[:100]
    sql = """SELECT s.*,
                (SELECT COUNT(*) FROM units u WHERE u.subject_id = s.id) AS unit_count,
                (SELECT COUNT(*) FROM resources r JOIN units u ON u.id = r.unit_id
                 WHERE u.subject_id = s.id) AS resource_count
             FROM subjects s WHERE s.user_id = ?"""
    params = [uid]
    if q:
        sql += " AND (s.name LIKE ? ESCAPE '\\' OR s.description LIKE ? ESCAPE '\\')"
        params += [like_pattern(q), like_pattern(q)]
    sql += " ORDER BY s.name COLLATE NOCASE"
    subjects = query(sql, params)

    stats = query(
        """SELECT
             (SELECT COUNT(*) FROM subjects WHERE user_id = :u) AS subjects,
             (SELECT COUNT(*) FROM units u JOIN subjects s ON s.id = u.subject_id
              WHERE s.user_id = :u) AS units,
             (SELECT COUNT(*) FROM resources r JOIN units u ON u.id = r.unit_id
              JOIN subjects s ON s.id = u.subject_id WHERE s.user_id = :u) AS resources,
             (SELECT COUNT(*) FROM favorites WHERE user_id = :u) AS favorites""",
        {"u": uid}, one=True)

    material_sql = """SELECT r.id, r.title, r.type, r.subtype, r.last_opened,
                         u.id AS unit_id, u.name AS unit_name, s.name AS subject_name
                      FROM resources r JOIN units u ON u.id = r.unit_id
                      JOIN subjects s ON s.id = u.subject_id"""
    recent = query(material_sql + """ WHERE s.user_id = ? AND r.last_opened IS NOT NULL
                   ORDER BY r.last_opened DESC, r.id DESC LIMIT 8""", (uid,))
    favorites = query(material_sql + """ JOIN favorites f ON f.resource_id = r.id
                      WHERE s.user_id = ? AND f.user_id = ?
                      ORDER BY f.created_at DESC, f.id DESC LIMIT 12""", (uid, uid))
    return render_template("dashboard.html", subjects=subjects, stats=stats, q=q,
                           recent=recent, favorites=favorites)


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
def subject_form():
    name = clean_text(request.form.get("name"), 120, required=True, label="Subject name")
    description = clean_text(request.form.get("description"), 1000, label="Description")
    icon = request.form.get("icon")
    return name, description, icon if icon in SUBJECT_ICONS else "book"


@app.post("/subjects/create")
@login_required
def subject_create():
    try:
        name, description, icon = subject_form()
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(safe_next(url_for("dashboard")))
    subject_id = execute(
        "INSERT INTO subjects (user_id, name, description, icon) VALUES (?, ?, ?, ?)",
        (g.user["id"], name, description, icon))
    flash(f'Subject "{name}" created. Add your first unit below.', "success")
    return redirect(url_for("subject_view", subject_id=subject_id))


@app.route("/subjects/<int:subject_id>")
@login_required
def subject_view(subject_id):
    subject = owned_subject(subject_id)
    execute("UPDATE subjects SET last_opened = datetime('now', 'localtime') WHERE id = ?",
            (subject_id,))
    units = units_of(subject_id)
    total = sum(u["resource_count"] for u in units)
    return render_template("subject.html", subject=subject, units=units, total=total,
                           nav_units=units, current_subject_id=subject_id)


@app.post("/subjects/<int:subject_id>/edit")
@login_required
def subject_edit(subject_id):
    owned_subject(subject_id)
    try:
        name, description, icon = subject_form()
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        execute("UPDATE subjects SET name = ?, description = ?, icon = ? WHERE id = ?",
                (name, description, icon, subject_id))
        flash("Subject updated.", "success")
    return redirect(safe_next(url_for("subject_view", subject_id=subject_id)))


@app.post("/subjects/<int:subject_id>/delete")
@login_required
def subject_delete(subject_id):
    subject = owned_subject(subject_id)
    files = [r["file_path"] for r in query(
        """SELECT r.file_path FROM resources r JOIN units u ON u.id = r.unit_id
           WHERE u.subject_id = ? AND r.file_path IS NOT NULL""", (subject_id,))]
    execute("DELETE FROM subjects WHERE id = ?", (subject_id,))  # cascades to units/resources
    remove_files(files)
    flash(f'Subject "{subject["name"]}" deleted.', "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def save_unit_order(ids):
    execute_many("UPDATE units SET position = ? WHERE id = ?",
                 [(pos, unit_id) for pos, unit_id in enumerate(ids, start=1)])


@app.post("/subjects/<int:subject_id>/units/create")
@login_required
def unit_create(subject_id):
    owned_subject(subject_id)
    try:
        name = clean_text(request.form.get("name"), 120, required=True, label="Unit name")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("subject_view", subject_id=subject_id))
    row = query("SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM units WHERE subject_id = ?",
                (subject_id,), one=True)
    execute("INSERT INTO units (subject_id, name, position) VALUES (?, ?, ?)",
            (subject_id, name, row["pos"]))
    flash(f'Unit "{name}" added.', "success")
    return redirect(url_for("subject_view", subject_id=subject_id))


@app.route("/units/<int:unit_id>")
@login_required
def unit_view(unit_id):
    unit = owned_unit(unit_id)
    resources = query(
        """SELECT r.*, EXISTS(SELECT 1 FROM favorites f
                              WHERE f.resource_id = r.id AND f.user_id = ?) AS is_fav
           FROM resources r WHERE r.unit_id = ? ORDER BY r.created_at DESC, r.id DESC""",
        (g.user["id"], unit_id))
    counts = {t: 0 for t in RESOURCE_TYPES}
    for r in resources:
        counts[r["type"]] += 1
    nav_units = units_of(unit["subject_id"])
    ids = [u["id"] for u in nav_units]
    idx = ids.index(unit_id)
    prev_unit = nav_units[idx - 1] if idx > 0 else None
    next_unit = nav_units[idx + 1] if idx + 1 < len(nav_units) else None
    return render_template("unit.html", unit=unit, resources=resources, counts=counts,
                           unit_number=idx + 1, prev_unit=prev_unit, next_unit=next_unit,
                           nav_units=nav_units, current_subject_id=unit["subject_id"],
                           current_unit_id=unit_id)


@app.post("/units/<int:unit_id>/rename")
@login_required
def unit_rename(unit_id):
    unit = owned_unit(unit_id)
    try:
        name = clean_text(request.form.get("name"), 120, required=True, label="Unit name")
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        execute("UPDATE units SET name = ? WHERE id = ?", (name, unit_id))
        flash("Unit renamed.", "success")
    return redirect(safe_next(url_for("subject_view", subject_id=unit["subject_id"])))


@app.post("/units/<int:unit_id>/delete")
@login_required
def unit_delete(unit_id):
    unit = owned_unit(unit_id)
    files = [r["file_path"] for r in query(
        "SELECT file_path FROM resources WHERE unit_id = ? AND file_path IS NOT NULL", (unit_id,))]
    execute("DELETE FROM units WHERE id = ?", (unit_id,))
    remove_files(files)
    save_unit_order([u["id"] for u in units_of(unit["subject_id"])])
    flash(f'Unit "{unit["name"]}" deleted.', "success")
    return redirect(url_for("subject_view", subject_id=unit["subject_id"]))


@app.post("/units/<int:unit_id>/move")
@login_required
def unit_move(unit_id):
    unit = owned_unit(unit_id)
    ids = [u["id"] for u in units_of(unit["subject_id"])]
    i = ids.index(unit_id)
    j = i - 1 if request.form.get("direction") == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        save_unit_order(ids)
    return redirect(url_for("subject_view", subject_id=unit["subject_id"]))


@app.post("/subjects/<int:subject_id>/units/reorder")
@login_required
def units_reorder(subject_id):
    owned_subject(subject_id)
    data = request.get_json(silent=True) or {}
    try:
        order = [int(x) for x in data.get("order", [])]
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid unit order."), 400
    existing = [u["id"] for u in units_of(subject_id)]
    if sorted(order) != sorted(existing):
        return jsonify(ok=False, error="The unit list has changed. Please refresh."), 400
    save_unit_order(order)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
@app.post("/units/<int:unit_id>/resources/add")
@login_required
def resource_add(unit_id):
    owned_unit(unit_id)
    back = url_for("unit_view", unit_id=unit_id)
    rtype = request.form.get("type", "")
    if rtype not in RESOURCE_TYPES:
        flash("Please choose a valid resource type.", "error")
        return redirect(back)
    try:
        title = clean_text(request.form.get("title"), 200, label="Title")
        description = clean_text(request.form.get("description"), 1000, label="Description")

        if rtype in FILE_TYPES:
            files = [f for f in request.files.getlist("files") if f and f.filename]
            if not files:
                raise ValueError("Please choose a file to upload.")
            saved = 0
            for storage in files:
                try:
                    rel_path, original, size = save_upload(storage, rtype, g.user["id"])
                except ValueError as exc:
                    flash(str(exc), "error")
                    continue
                except OSError:
                    flash(f'Could not save "{storage.filename}". Please try again.', "error")
                    continue
                name = title if (title and len(files) == 1) else (original.rsplit(".", 1)[0] or original)
                try:
                    execute(
                        """INSERT INTO resources (unit_id, type, title, description,
                                                  file_path, original_name, file_size)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (unit_id, rtype, name[:200], description, rel_path, original, size))
                except Exception:
                    remove_files([rel_path])
                    flash(f'Could not save "{original}" to the database.', "error")
                    continue
                saved += 1
            if saved:
                flash(f"{saved} {TYPE_LABELS[rtype].lower()} file{'s' if saved > 1 else ''} uploaded.",
                      "success")
            return redirect(back)

        if rtype in LINK_TYPES:
            url = clean_url(request.form.get("url"))
            subtype = detect_subtype(rtype, url, request.form.get("provider"))
            title = title or default_link_title(rtype, subtype, url)
            execute("""INSERT INTO resources (unit_id, type, subtype, title, description, url)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (unit_id, rtype, subtype, title, description, url))
        else:  # note
            content = clean_text(request.form.get("content"), 1_000_000, label="Note", strip=False)
            title = title or "Untitled note"
            execute("""INSERT INTO resources (unit_id, type, title, description, content)
                       VALUES (?, ?, ?, ?, ?)""",
                    (unit_id, rtype, title, description, content))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(back)
    flash(f'{TYPE_LABELS[rtype]} "{title}" added.', "success")
    return redirect(back)


@app.route("/resources/<int:resource_id>")
@login_required
def resource_view(resource_id):
    res = owned_resource(resource_id)
    touch_resource(resource_id)
    path = existing_file(res)
    missing = bool(res["file_path"]) and path is None
    text, truncated = None, False
    if path and file_ext(res) == "txt":
        try:
            text, truncated = read_text_preview(path)
        except OSError:
            missing = True
    nav_units = units_of(res["subject_id"])
    return render_template("resource.html", res=res, missing=missing, text=text,
                           truncated=truncated, edit_note=request.args.get("edit") == "1",
                           nav_units=nav_units, current_subject_id=res["subject_id"],
                           current_unit_id=res["unit_id"])


def send_resource_file(res, as_attachment):
    path = existing_file(res)
    if path is None:
        return None
    ext = file_ext(res)
    return send_file(path, mimetype=MIME_TYPES.get(ext, "application/octet-stream"),
                     as_attachment=as_attachment or ext not in INLINE_EXTS,
                     download_name=res["original_name"] or path.name, max_age=0)


def missing_file_redirect(res):
    flash(f'The file for "{res["title"]}" is missing from the uploads folder. '
          "You can delete this entry and upload it again.", "error")
    return redirect(url_for("unit_view", unit_id=res["unit_id"]))


@app.route("/resources/<int:resource_id>/file")
@login_required
def resource_file(resource_id):
    """Inline file used for previews and thumbnails (does not count as 'opened')."""
    res = owned_resource(resource_id)
    resp = send_resource_file(res, as_attachment=False)
    if resp is None:
        abort(404)
    return resp


@app.route("/resources/<int:resource_id>/open")
@login_required
def resource_open(resource_id):
    res = owned_resource(resource_id)
    if res["type"] in LINK_TYPES:
        touch_resource(resource_id)
        return redirect(res["url"])
    if res["type"] == "note":
        return redirect(url_for("resource_view", resource_id=resource_id))
    resp = send_resource_file(res, as_attachment=False)
    if resp is None:
        return missing_file_redirect(res)
    touch_resource(resource_id)
    return resp


@app.route("/resources/<int:resource_id>/download")
@login_required
def resource_download(resource_id):
    res = owned_resource(resource_id)
    if res["type"] == "note":
        touch_resource(resource_id)
        name = display_filename(res["title"]) or "note"
        return send_file(io.BytesIO((res["content"] or "").encode("utf-8")),
                         mimetype="text/plain; charset=utf-8", as_attachment=True,
                         download_name=f"{name}.txt")
    if res["type"] in LINK_TYPES:
        return redirect(url_for("resource_open", resource_id=resource_id))
    resp = send_resource_file(res, as_attachment=True)
    if resp is None:
        return missing_file_redirect(res)
    touch_resource(resource_id)
    return resp


@app.post("/resources/<int:resource_id>/edit")
@login_required
def resource_edit(resource_id):
    res = owned_resource(resource_id)
    back = safe_next(url_for("unit_view", unit_id=res["unit_id"]))
    try:
        title = clean_text(request.form.get("title"), 200, required=True, label="Title")
        description = clean_text(request.form.get("description"), 1000, label="Description")
        url, subtype, content = res["url"], res["subtype"], res["content"]
        if res["type"] in LINK_TYPES and "url" in request.form:
            url = clean_url(request.form.get("url"))
            if url != res["url"]:
                subtype = detect_subtype(res["type"], url)
        if res["type"] == "note" and "content" in request.form:
            content = clean_text(request.form.get("content"), 1_000_000, label="Note", strip=False)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(back)
    execute("""UPDATE resources SET title = ?, description = ?, url = ?, subtype = ?, content = ?,
                      updated_at = datetime('now', 'localtime') WHERE id = ?""",
            (title, description, url, subtype, content, resource_id))
    flash("Changes saved.", "success")
    return redirect(back)


@app.post("/resources/<int:resource_id>/delete")
@login_required
def resource_delete(resource_id):
    res = owned_resource(resource_id)
    execute("DELETE FROM resources WHERE id = ?", (resource_id,))
    if res["file_path"]:
        remove_files([res["file_path"]])
    flash(f'"{res["title"]}" deleted.', "success")
    return redirect(url_for("unit_view", unit_id=res["unit_id"]))


@app.post("/resources/<int:resource_id>/favorite")
@login_required
def resource_favorite(resource_id):
    res = owned_resource(resource_id)
    if res["is_fav"]:
        execute("DELETE FROM favorites WHERE user_id = ? AND resource_id = ?",
                (g.user["id"], resource_id))
    else:
        execute("INSERT OR IGNORE INTO favorites (user_id, resource_id) VALUES (?, ?)",
                (g.user["id"], resource_id))
    return redirect(safe_next(url_for("unit_view", unit_id=res["unit_id"])))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
@app.errorhandler(RequestEntityTooLarge)
def too_large(_exc):
    flash(f"Upload too large. The maximum total size per upload is "
          f"{app.config['MAX_CONTENT_LENGTH'] // MB} MB.", "error")
    return redirect(same_origin_referrer() or url_for("index"))


@app.errorhandler(HTTPException)
def http_error(exc):
    return render_template("error.html", error=exc), exc.code


@app.errorhandler(Exception)
def server_error(exc):
    if isinstance(exc, HTTPException):
        return http_error(exc)
    app.logger.exception("Unexpected error")
    return render_template("error.html", error=None), 500


if __name__ == "__main__":
    print("\n  Personal Study Library is running at  http://127.0.0.1:5000\n"
          "  Press CTRL+C to stop.\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
