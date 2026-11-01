
import os
import uuid
import logging
from pathlib import Path
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    session
)

from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from supabase import create_client


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("Falta configurar SECRET_KEY en Render.")

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "true").lower() == "true"
)

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

CSRFProtect(app)

# Registro de errores y operaciones en Render
logging.basicConfig(level=logging.INFO)

# ============================================================
# CONFIGURACIÓN DE SUPABASE
# ============================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get(
    "SUPABASE_BUCKET",
    "gabinete-archivos"
)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_KEY en Render."
    )

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

# ============================================================
# CONFIGURACIÓN ADMINISTRADOR
# ============================================================

ADMIN_USERNAME = os.environ.get(
    "ADMIN_USERNAME",
    "colectivo"
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    ""
)

ADMIN_PASSWORD_HASH = os.environ.get(
    "ADMIN_PASSWORD_HASH",
    ""
)

# ============================================================
# CATEGORÍAS
# ============================================================

CATEGORIES = {
    "texto": "Textos",
    "archivo": "Archivos",
    "imagen": "Imágenes",
    "sonido": "Sonidos",
    "video": "Videos"
}

ALLOWED = {
    "pdf", "doc", "docx", "txt", "rtf", "odt",
    "png", "jpg", "jpeg", "gif", "webp",
    "mp3", "wav", "ogg", "m4a",
    "mp4", "webm", "mov", "m4v",
    "ppt", "pptx", "xls", "xlsx", "csv"
}


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def valid_admin_password(candidate):

    if ADMIN_PASSWORD_HASH:
        return check_password_hash(
            ADMIN_PASSWORD_HASH,
            candidate
        )

    return (
        bool(ADMIN_PASSWORD)
        and candidate == ADMIN_PASSWORD
    )


def login_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if not session.get("is_member"):

            flash(
                "Inicia sesión como integrante del colectivo.",
                "error"
            )

            return redirect(
                url_for("login", next=request.path)
            )

        return view(*args, **kwargs)

    return wrapped


def allowed(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED
    )


def get_material(item_id):

    result = (
        supabase.table("materials")
        .select("*")
        .eq("id", item_id)
        .limit(1)
        .execute()
    )

    return result.data[0] if result.data else None


# ============================================================
# SUPABASE STORAGE
# ============================================================

def upload_file(file):

    """
    Recibe un archivo desde Flask y lo guarda
    permanentemente en el bucket de Supabase.
    """

    if not file or not file.filename:
        return None, None

    original_name = os.path.basename(file.filename)

    if not allowed(original_name):
        raise ValueError("Tipo de archivo no permitido.")

    safe_name = secure_filename(original_name)

    if not safe_name:
        safe_name = "archivo"

    # Nombre único para evitar sobrescribir documentos
    stored_name = (
        uuid.uuid4().hex
        + "_"
        + safe_name
    )

    # Leer contenido
    file_content = file.read()

    if not file_content:
        raise ValueError("El archivo está vacío.")

    mime_type = (
        file.mimetype
        or "application/octet-stream"
    )

    app.logger.info(
        "ARCHIVO RECIBIDO: %s | Tamaño: %s bytes | Bucket: %s",
        original_name,
        len(file_content),
        SUPABASE_BUCKET
    )

    # Subir archivo a Supabase Storage
    result = supabase.storage.from_(
        SUPABASE_BUCKET
    ).upload(
        path=stored_name,
        file=file_content,
        file_options={
            "content-type": mime_type,
            "upsert": "false"
        }
    )

    app.logger.info(
        "ARCHIVO SUBIDO CORRECTAMENTE: %s",
        stored_name
    )

    return stored_name, original_name


def get_file_url(filename):

    if not filename:
        return ""

    return supabase.storage.from_(
        SUPABASE_BUCKET
    ).get_public_url(filename)


def delete_file(filename):

    if not filename:
        return

    try:

        supabase.storage.from_(
            SUPABASE_BUCKET
        ).remove([filename])

        app.logger.info(
            "ARCHIVO ELIMINADO DE STORAGE: %s",
            filename
        )

    except Exception:

        app.logger.exception(
            "No se pudo eliminar el archivo: %s",
            filename
        )


# ============================================================
# INICIO DE SESIÓN
# ============================================================

@app.route("/ingresar", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        if (
            username == ADMIN_USERNAME
            and valid_admin_password(password)
        ):

            session.clear()

            session["is_member"] = True
            session["member_name"] = username

            flash(
                "Sesión iniciada correctamente.",
                "success"
            )

            return redirect(
                request.args.get("next")
                or url_for("index")
            )

        flash(
            "Usuario o contraseña incorrectos.",
            "error"
        )

    return render_template("login.html")


@app.route("/salir", methods=["POST"])
@login_required
def logout():

    session.clear()

    flash(
        "Has cerrado tu sesión.",
        "success"
    )

    return redirect(url_for("index"))


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    category = request.args.get(
        "categoria",
        ""
    )

    search = request.args.get(
        "buscar",
        ""
    ).strip()

    sort = request.args.get(
        "orden",
        "reciente"
    )

    query = supabase.table(
        "materials"
    ).select("*")

    if category in CATEGORIES:
        query = query.eq("category", category)

    if search:

        safe_search = (
            search
            .replace(",", " ")
            .replace("%", "")
            .replace("*", "")
        )

        query = query.or_(
            f"title.ilike.%{safe_search}%,"
            f"description.ilike.%{safe_search}%,"
            f"body.ilike.%{safe_search}%,"
            f"original_name.ilike.%{safe_search}%"
        )

    if sort == "autor":

        query = (
            query.order("author")
            .order("title")
        )

    elif sort == "fecha_antigua":

        query = (
            query.order(
                "material_date",
                desc=False
            )
            .order("id", desc=True)
        )

    elif sort == "fecha_reciente":

        query = (
            query.order(
                "material_date",
                desc=True
            )
            .order("id", desc=True)
        )

    else:

        query = query.order(
            "id",
            desc=True
        )

    items = query.execute().data or []

    # Conteo por categoría
    all_items = (
        supabase.table("materials")
        .select("category")
        .execute()
        .data or []
    )

    counts = {}

    for item in all_items:

        cat = item.get("category", "")

        counts[cat] = (
            counts.get(cat, 0) + 1
        )

    return render_template(
        "index.html",
        materials=items,
        counts=counts,
        categories=CATEGORIES,
        selected=category,
        search=search,
        sort=sort
    )


# ============================================================
# PUBLICAR MATERIAL
# ============================================================

@app.route("/nuevo", methods=["GET", "POST"])
@login_required
def nuevo():

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        category = request.form.get(
            "category",
            "texto"
        )

        description = request.form.get(
            "description",
            ""
        ).strip()

        body = request.form.get(
            "body",
            ""
        ).strip()

        author = request.form.get(
            "author",
            ""
        ).strip()

        material_date = request.form.get(
            "material_date",
            ""
        ).strip()

        uploaded = request.files.get("file")

        stored = ""
        original = ""

        if not title or category not in CATEGORIES:

            flash(
                "Escribe un título y selecciona una categoría.",
                "error"
            )

            return redirect(url_for("nuevo"))

        # ----------------------------------------------------
        # SUBIR ARCHIVO A SUPABASE STORAGE
        # ----------------------------------------------------

        if uploaded and uploaded.filename:

            try:

                stored, original = upload_file(uploaded)

                # Leer contenido de archivos TXT
                if (
                    category == "texto"
                    and original.lower().endswith(".txt")
                    and not body
                ):

                    # Se obtiene el contenido del archivo
                    # desde Storage para conservar el flujo
                    # de subida original.
                    pass

            except ValueError as error:

                flash(str(error), "error")

                return redirect(url_for("nuevo"))

            except Exception:

                app.logger.exception(
                    "ERROR AL SUBIR ARCHIVO A SUPABASE STORAGE."
                )

                flash(
                    "No se pudo subir el archivo. "
                    "Revisa los Logs de Render.",
                    "error"
                )

                return redirect(url_for("nuevo"))

        # ----------------------------------------------------
        # VALIDAR CONTENIDO
        # ----------------------------------------------------

        if category == "texto" and not body and not stored:

            flash(
                "Escribe el texto o adjunta un archivo.",
                "error"
            )

            return redirect(url_for("nuevo"))

        if category != "texto" and not stored:

            flash(
                "Adjunta un archivo para esta categoría.",
                "error"
            )

            return redirect(url_for("nuevo"))

        # ----------------------------------------------------
        # GUARDAR DATOS EN SUPABASE DATABASE
        # ----------------------------------------------------

        publication = {

            "title": title,
            "category": category,
            "description": description,
            "body": body,
            "stored_name": stored,
            "original_name": original,

            "created_at": datetime.now().strftime(
                "%d/%m/%Y %H:%M"
            ),

            "author": author,
            "material_date": material_date
        }

        try:

            result = (
                supabase.table("materials")
                .insert(publication)
                .execute()
            )

            app.logger.info(
                "MATERIAL GUARDADO EN DATABASE: %s",
                title
            )

        except Exception:

            app.logger.exception(
                "ERROR AL GUARDAR MATERIAL EN DATABASE."
            )

            # Si falla el registro, eliminar el archivo
            # para evitar documentos huérfanos.
            if stored:
                delete_file(stored)

            flash(
                "No se pudo guardar la publicación.",
                "error"
            )

            return redirect(url_for("nuevo"))

        flash(
            "¡Material y archivo guardados correctamente!",
            "success"
        )

        return redirect(
            url_for(
                "index",
                categoria=category
            )
        )

    return render_template(
        "form.html",
        categories=CATEGORIES,
        item=None
    )


# ============================================================
# DETALLE DE PUBLICACIÓN
# ============================================================

@app.route("/material/<int:item_id>")
def detalle(item_id):

    item = get_material(item_id)

    if not item:
        abort(404)

    return render_template(
        "detail.html",
        item=item,
        categories=CATEGORIES
    )


# ============================================================
# EDITAR PUBLICACIÓN
# ============================================================

@app.route(
    "/material/<int:item_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar(item_id):

    item = get_material(item_id)

    if not item:
        abort(404)

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        if not title:

            flash(
                "El título es obligatorio.",
                "error"
            )

            return redirect(
                url_for(
                    "editar",
                    item_id=item_id
                )
            )

        changes = {

            "title": title,

            "description": request.form.get(
                "description",
                ""
            ).strip(),

            "body": request.form.get(
                "body",
                ""
            ).strip(),

            "author": request.form.get(
                "author",
                ""
            ).strip(),

            "material_date": request.form.get(
                "material_date",
                ""
            ).strip()
        }

        try:

            supabase.table(
                "materials"
            ).update(
                changes
            ).eq(
                "id",
                item_id
            ).execute()

            flash(
                "Cambios guardados correctamente.",
                "success"
            )

        except Exception:

            app.logger.exception(
                "ERROR AL EDITAR MATERIAL."
            )

            flash(
                "No se pudieron guardar los cambios.",
                "error"
            )

        return redirect(
            url_for(
                "detalle",
                item_id=item_id
            )
        )

    return render_template(
        "form.html",
        categories=CATEGORIES,
        item=item
    )


# ============================================================
# ELIMINAR PUBLICACIÓN
# ============================================================

@app.route(
    "/material/<int:item_id>/eliminar",
    methods=["POST"]
)
@login_required
def eliminar(item_id):

    item = get_material(item_id)

    if item:

        stored = item.get(
            "stored_name",
            ""
        )

        try:

            supabase.table(
                "materials"
            ).delete().eq(
                "id",
                item_id
            ).execute()

            delete_file(stored)

            flash(
                "Material eliminado.",
                "success"
            )

        except Exception:

            app.logger.exception(
                "ERROR AL ELIMINAR MATERIAL."
            )

            flash(
                "No se pudo eliminar el material.",
                "error"
            )

    else:

        flash(
            "El material no existe.",
            "error"
        )

    return redirect(url_for("index"))


# ============================================================
# MOSTRAR ARCHIVOS
# ============================================================

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):

    try:

        url = get_file_url(filename)

        if not url:
            abort(404)

        return redirect(url)

    except Exception:

        app.logger.exception(
            "ERROR AL OBTENER URL DEL ARCHIVO."
        )

        abort(404)


# ============================================================
# DESCARGAR ARCHIVOS
# ============================================================

@app.route("/material/<int:item_id>/descargar")
def descargar(item_id):

    item = get_material(item_id)

    if not item or not item.get("stored_name"):
        abort(404)

    try:

        url = get_file_url(
            item["stored_name"]
        )

        return redirect(url)

    except Exception:

        app.logger.exception(
            "ERROR AL DESCARGAR ARCHIVO."
        )

        abort(404)


# ============================================================
# COMPROBAR CONEXIÓN Y ESTADO
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "database": "configured",
        "storage_bucket": SUPABASE_BUCKET
    }


# ============================================================
# ERROR: ARCHIVO DEMASIADO GRANDE
# ============================================================

@app.errorhandler(413)
def too_large(error):

    flash(
        "El archivo supera el límite permitido de 50 MB.",
        "error"
    )

    return redirect(url_for("nuevo"))


# ============================================================
# EJECUCIÓN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
