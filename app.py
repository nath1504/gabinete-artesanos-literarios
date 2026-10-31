import os, sqlite3, uuid
from pathlib import Path
from datetime import datetime
from functools import wraps
from werkzeug.security import check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort, session

ROOT = Path(__file__).resolve().parent
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS = DATA_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)
DB = DATA_DIR / "gabinete.db"
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave")
CSRFProtect(app)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "colectivo")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
CATEGORIES = {"texto":"Textos", "archivo":"Archivos", "imagen":"Imágenes", "sonido":"Sonidos", "video":"Videos"}
ALLOWED = {"pdf","doc","docx","txt","rtf","odt","png","jpg","jpeg","gif","webp","mp3","wav","ogg","m4a","mp4","webm","mov","m4v","ppt","pptx","xls","xlsx","csv"}

def connect():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init_db():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS materials(
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, category TEXT NOT NULL,
        description TEXT DEFAULT '', body TEXT DEFAULT '', stored_name TEXT DEFAULT '',
        original_name TEXT DEFAULT '', created_at TEXT NOT NULL,
        author TEXT DEFAULT '', material_date TEXT DEFAULT '')""")
        # Migración segura para bases de datos creadas con versiones anteriores.
        columns = {row[1] for row in c.execute("PRAGMA table_info(materials)")}
        if "author" not in columns:
            c.execute("ALTER TABLE materials ADD COLUMN author TEXT DEFAULT ''")
        if "material_date" not in columns:
            c.execute("ALTER TABLE materials ADD COLUMN material_date TEXT DEFAULT ''")

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_member"):
            flash("Inicia sesión como integrante del colectivo para realizar esa acción.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def valid_admin_password(candidate):
    if ADMIN_PASSWORD_HASH:
        return check_password_hash(ADMIN_PASSWORD_HASH, candidate)
    return bool(ADMIN_PASSWORD) and candidate == ADMIN_PASSWORD

def allowed(name):
    return "." in name and name.rsplit(".",1)[1].lower() in ALLOWED

@app.route("/ingresar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and valid_admin_password(password):
            session.clear()
            session["is_member"] = True
            session["member_name"] = username
            flash("Sesión iniciada. Ya puedes publicar materiales.", "success")
            return redirect(request.args.get("next") or url_for("index"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")

@app.route("/salir", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("Has cerrado tu sesión.", "success")
    return redirect(url_for("index"))

@app.route("/")
def index():
    category=request.args.get("categoria",""); search=request.args.get("buscar","").strip()
    sort=request.args.get("orden", "reciente")
    sql="SELECT * FROM materials WHERE 1=1"; args=[]
    if category in CATEGORIES: sql+=" AND category=?"; args.append(category)
    if search:
        sql+=" AND (title LIKE ? OR description LIKE ? OR body LIKE ? OR original_name LIKE ?)"
        args += [f"%{search}%"]*4
    if sort == "autor":
        sql += " ORDER BY lower(author) ASC, lower(title) ASC"
    elif sort == "fecha_antigua":
        sql += " ORDER BY CASE WHEN material_date='' THEN 1 ELSE 0 END, material_date ASC, id DESC"
    elif sort == "fecha_reciente":
        sql += " ORDER BY CASE WHEN material_date='' THEN 1 ELSE 0 END, material_date DESC, id DESC"
    else:
        sql += " ORDER BY id DESC"
    with connect() as c:
        items=c.execute(sql,args).fetchall()
        counts={r["category"]:r["n"] for r in c.execute("SELECT category,COUNT(*) n FROM materials GROUP BY category")}
    return render_template("index.html",materials=items,counts=counts,categories=CATEGORIES,selected=category,search=search,sort=sort)

@app.route("/nuevo",methods=["GET","POST"])
@login_required
def nuevo():
    if request.method=="POST":
        title=request.form.get("title","").strip()
        category=request.form.get("category","texto")
        description=request.form.get("description","").strip()
        body=request.form.get("body","").strip()
        author=request.form.get("author", "").strip()
        material_date=request.form.get("material_date", "").strip()
        f=request.files.get("file"); stored=""; original=""
        if not title or category not in CATEGORIES:
            flash("Escribe un título y selecciona una categoría.","error"); return redirect(url_for("nuevo"))
        if f and f.filename:
            original=os.path.basename(f.filename)
            if not allowed(original):
                flash("Tipo de archivo no permitido.","error"); return redirect(url_for("nuevo"))
            stored=uuid.uuid4().hex+"_"+original; f.save(UPLOADS/stored)
            if category=="texto" and original.lower().endswith(".txt") and not body:
                try: body=(UPLOADS/stored).read_text(encoding="utf-8")
                except UnicodeDecodeError: pass
        if category=="texto" and not body and not stored:
            flash("Escribe el texto o adjunta un archivo.","error"); return redirect(url_for("nuevo"))
        if category!="texto" and not stored:
            flash("Adjunta un archivo para esta categoría.","error"); return redirect(url_for("nuevo"))
        with connect() as c:
            c.execute("INSERT INTO materials(title,category,description,body,stored_name,original_name,created_at,author,material_date) VALUES(?,?,?,?,?,?,?,?,?)",
            (title,category,description,body,stored,original,datetime.now().strftime("%d/%m/%Y %H:%M"),author,material_date))
        flash("¡Material guardado en tu gabinete!","success")
        return redirect(url_for("index",categoria=category))
    return render_template("form.html",categories=CATEGORIES,item=None)

@app.route("/material/<int:item_id>")
def detalle(item_id):
    with connect() as c: item=c.execute("SELECT * FROM materials WHERE id=?",(item_id,)).fetchone()
    if not item: abort(404)
    return render_template("detail.html",item=item,categories=CATEGORIES)

@app.route("/material/<int:item_id>/editar",methods=["GET","POST"])
@login_required
def editar(item_id):
    with connect() as c: item=c.execute("SELECT * FROM materials WHERE id=?",(item_id,)).fetchone()
    if not item: abort(404)
    if request.method=="POST":
        title=request.form.get("title","").strip()
        if not title: flash("El título es obligatorio.","error"); return redirect(url_for("editar",item_id=item_id))
        with connect() as c:
            c.execute("UPDATE materials SET title=?,description=?,body=?,author=?,material_date=? WHERE id=?",
            (title,request.form.get("description","").strip(),request.form.get("body","").strip(),request.form.get("author", "").strip(),request.form.get("material_date", "").strip(),item_id))
        flash("Cambios guardados.","success"); return redirect(url_for("detalle",item_id=item_id))
    return render_template("form.html",categories=CATEGORIES,item=item)

@app.route("/material/<int:item_id>/eliminar",methods=["POST"])
@login_required
def eliminar(item_id):
    with connect() as c:
        item=c.execute("SELECT * FROM materials WHERE id=?",(item_id,)).fetchone()
        if item:
            if item["stored_name"] and (UPLOADS/item["stored_name"]).exists(): (UPLOADS/item["stored_name"]).unlink()
            c.execute("DELETE FROM materials WHERE id=?",(item_id,))
    flash("Material eliminado.","success"); return redirect(url_for("index"))

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOADS,filename)

@app.route("/material/<int:item_id>/descargar")
def descargar(item_id):
    with connect() as c: item=c.execute("SELECT * FROM materials WHERE id=?",(item_id,)).fetchone()
    if not item or not item["stored_name"]: abort(404)
    return send_from_directory(UPLOADS,item["stored_name"],as_attachment=True,download_name=item["original_name"])

@app.errorhandler(413)
def too_large(e):
    flash("El archivo supera el límite de 50 MB.","error"); return redirect(url_for("nuevo"))

init_db()
if __name__=="__main__": app.run(debug=True)
