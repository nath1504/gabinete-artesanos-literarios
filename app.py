
import os
import uuid
import hmac
import logging
import mimetypes

from datetime import datetime
from functools import wraps
from urllib.parse import quote

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    session,
)

from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from supabase import create_client


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

app.secret_key = os.environ.get("SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("Falta configurar SECRET_KEY en Render.")

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("COOKIE_SECURE", "true").lower() == "true"
)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

csrf = CSRFProtect(app)


# ============================================================
# CONFIGURACIÓN DE SUPABASE
# ============================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
SUPABASE_BUCKET = os.environ.get(
    "SUPABASE_BUCKET",
    "gabinete-archivos"
).strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_KEY en las variables de Render."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# CONFIGURACIÓN DEL ADMINISTRADOR
# ============================================================

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "colectivo")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")


# ============================================================
# CATEGORÍAS
# ============================================================

CATEGORIES = {
    "texto": "Textos",
    "archivo": "Archivos",
    "imagen": "Imágenes",
    "sonido": "Sonidos",
    "video": "Videos",
}


# ============================================================
# EXTENSIONES PERMITIDAS
# ============================================================

ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "txt", "rtf", "odt",
    "png", "jpg", "jpeg", "gif", "webp",
    "mp3", "wav", "ogg", "m4a",
    "mp4", "webm", "mov", "m4v",
    "ppt", "pptx", "xls", "xlsx", "csv",
}


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def allowed_file(filename):
    """Comprueba que el archivo tenga una extensión permitida."""

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def is_member():
    """Comprueba si existe una sesión de administrador."""

    return session.get("is_member") is True


def login_required(view):
    """Protege las rutas que requieren iniciar sesión."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not is_member():
            flash("Debes iniciar sesión para realizar esta acción.", "warning")
            return redirect(
                url_for("login", next=request.path)
            )

        return view(*args, **kwargs)

    return wrapped_view


def get_file_url(filename):
    """
    Genera una URL temporal de Supabase Storage.

    Permite trabajar con buckets privados sin hacer público
    el contenido de los archivos.
    """

    if not filename:
        return ""

    try:
        result = supabase.storage.from_(
            SUPABASE_BUCKET
        ).create_signed_url(filename, 3600)

        if isinstance(result, dict):
            signed_url = (
                result.get("signedURL")
                or result.get("signedUrl")
                or result.get("signed_url")
            )
        elif isinstance(result, str):
            signed_url = result
        else:
            signed_url = None

        if not signed_url:
            app.logger.error(
                "Supabase no devolvió una URL para: %s",
                filename
            )
            return ""

        if signed_url.startswith("http://") or signed_url.startswith("https://"):
            return signed_url

        if signed_url.startswith("/storage/v1/"):
            return SUPABASE_URL.rstrip("/") + signed_url

        if signed_url.startswith("/"):
            return (
                SUPABASE_URL.rstrip("/")
                + "/storage/v1"
                + signed_url
            )

        return (
            SUPABASE_URL.rstrip("/")
            + "/storage/v1/"
            + signed_url.lstrip("/")
        )

    except Exception:
        app.logger.exception(
            "Error generando URL para el archivo %s",
            filename
        )
        return ""


def upload_file(file):
    """
    Sube un archivo a Supabase Storage.

    Devuelve:
    stored_name: nombre interno del archivo.
    original_name: nombre original del archivo.
    """

    if not file or not file.filename:
        return None, None

    original_name = file.filename.strip()

    if not allowed_file(original_name):
        raise ValueError(
            "El tipo de archivo no está permitido."
        )

    safe_name = secure_filename(original_name)

    if not safe_name:
        raise ValueError(
            "No se pudo procesar el nombre del archivo."
        )

    extension = safe_name.rsplit(".", 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{extension}"

    file_content = file.read()

    if not file_content:
        raise ValueError("El archivo está vacío.")

    mime_type = (
        file.mimetype
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )

    app.logger.info(
        "ARCHIVO RECIBIDO: %s | Tamaño: %s bytes | Bucket: %s",
        original_name,
        len(file_content),
        SUPABASE_BUCKET
    )

    supabase.storage.from_(
        SUPABASE_BUCKET
    ).upload(
        path=stored_name,
        file=file_content,
        file_options={
            "content-type": mime_type,
            "upsert": "false",
        }
    )

    app.logger.info(
        "ARCHIVO SUBIDO CORRECTAMENTE: %s",
        stored_name
    )

    return stored_name, original_name


def delete_file(filename):
    """Elimina un archivo de Supabase Storage."""

    if not filename:
        return

    try:
        supabase.storage.from_(
            SUPABASE_BUCKET
        ).remove([filename])

        app.logger.info(
            "ARCHIVO ELIMINADO: %s",
            filename
        )

    except Exception:
        app.logger.exception(
            "No se pudo eliminar el archivo: %s",
            filename
        )


def prepare_material(item):
    """
    Agrega file_url al material para que las plantillas
    puedan mostrar imágenes, audios y videos.
    """

    if not item:
        return item

    if item.get("stored_name"):
        item["file_url"] = get_file_url(
            item["stored_name"]
        )
    else:
        item["file_url"] = ""

    return item


# ============================================================
# INICIO DE SESIÓN
# ============================================================

@app.route("/ingresar", methods=["GET", "POST"])
def login():

    if is_member():
        return redirect(url_for("index"))

    if request.method == "POST":

        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get(
            "password", ""
        )

        valid_username = hmac.compare_digest(
            username,
            ADMIN_USERNAME
        )

        valid_password = False

        if ADMIN_PASSWORD_HASH:
            try:
                valid_password = check_password_hash(
                    ADMIN_PASSWORD_HASH,
                    password
                )
            except ValueError:
                app.logger.exception(
                    "El hash de ADMIN_PASSWORD_HASH no es válido."
                )

        elif ADMIN_PASSWORD:
            valid_password = hmac.compare_digest(
                password,
                ADMIN_PASSWORD
            )

        if valid_username and valid_password:

            session.clear()
            session["is_member"] = True
            session["username"] = username

            flash(
                "¡Bienvenido al Gabinete Digital!",
                "success"
            )

            next_page = request.args.get("next", "")

            if next_page.startswith("/") and not next_page.startswith("//"):
                return redirect(next_page)

            return redirect(url_for("index"))

        flash(
            "Usuario o contraseña incorrectos.",
            "danger"
        )

    return render_template("login.html")


# ============================================================
# CERRAR SESIÓN
# ============================================================

@app.route("/salir")
def logout():

    session.clear()

    flash(
        "Has cerrado sesión correctamente.",
        "success"
    )

    return redirect(url_for("index"))


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    category = request.args.get(
        "categoria", ""
    ).strip()

    search = request.args.get(
        "buscar", ""
    ).strip()

    sort = request.args.get(
        "orden", "recientes"
    ).strip()

    try:

        # Obtener todos los materiales para calcular estadísticas.
        all_response = (
            supabase.table("materials")
            .select("*")
            .execute()
        )

        all_items = all_response.data or []

        counts = {
            category_key: 0
            for category_key in CATEGORIES
        }

        for material in all_items:
            material_category = material.get("category")

            if material_category in counts:
                counts[material_category] += 1

        # Consulta de materiales.
        query = (
            supabase.table("materials")
            .select("*")
        )

        if category in CATEGORIES:
            query = query.eq("category", category)

        if search:
            safe_search = (
                search
                .replace("%", "")
                .replace(",", " ")
            )

            query = query.ilike(
                "title",
                f"%{safe_search}%"
            )

        if sort == "antiguos":
            query = query.order(
                "created_at",
                desc=False
            )
        else:
            query = query.order(
                "created_at",
                desc=True
            )

        response = query.execute()

        items = response.data or []

        # Generar URL temporal para cada archivo.
        for item in items:
            prepare_material(item)

        return render_template(
            "index.html",
            materials=items,
            counts=counts,
            categories=CATEGORIES,
            selected=category,
            search=search,
            sort=sort
        )

    except Exception:
        app.logger.exception(
            "Error cargando la página principal."
        )

        flash(
            "No se pudieron cargar los materiales.",
            "danger"
        )

        return render_template(
            "index.html",
            materials=[],
            counts={
                category_key: 0
                for category_key in CATEGORIES
            },
            categories=CATEGORIES,
            selected=category,
            search=search,
            sort=sort
        )


# ============================================================
# AGREGAR MATERIAL
# ============================================================

@app.route("/nuevo", methods=["GET", "POST"])
@login_required
def nuevo():

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        category = request.form.get(
            "category", ""
        ).strip()

        description = request.form.get(
            "description", ""
        ).strip()

        body = request.form.get(
            "body", ""
        ).strip()

        author = request.form.get(
            "author", ""
        ).strip()

        material_date = request.form.get(
            "material_date", ""
        ).strip()

        file = request.files.get("file")

        stored_name = None
        original_name = None

        try:

            if not title:
                raise ValueError(
                    "Debes escribir el título del material."
                )

            if category not in CATEGORIES:
                raise ValueError(
                    "Selecciona una categoría válida."
                )

            # Si se trata de un texto y se adjunta un TXT,
            # se incorpora su contenido al campo body.
            if category == "texto" and file and file.filename:
                extension = file.filename.rsplit(".", 1)[-1].lower()

                if extension == "txt" and not body:
                    content = file.read()
                    body = content.decode(
                        "utf-8",
                        errors="replace"
                    )

                    file.stream.seek(0)

            # Subir archivo, si existe.
            if file and file.filename:
                stored_name, original_name = upload_file(file)

            material_data = {
                "title": title,
                "category": category,
                "description": description,
                "body": body,
                "stored_name": stored_name,
                "original_name": original_name,
                "created_at": datetime.now().isoformat(
                    timespec="seconds"
                ),
                "author": author,
                "material_date": material_date,
            }

            supabase.table(
                "materials"
            ).insert(
                material_data
            ).execute()

            flash(
                "El material se agregó correctamente al gabinete.",
                "success"
            )

            return redirect(url_for("index"))

        except ValueError as error:

            if stored_name:
                delete_file(stored_name)

            flash(str(error), "warning")

        except Exception:

            if stored_name:
                delete_file(stored_name)

            app.logger.exception(
                "Error guardando un nuevo material."
            )

            flash(
                "Ocurrió un error al guardar el material. Revisa los registros de Render.",
                "danger"
            )

    return render_template(
        "form.html",
        item=None,
        categories=CATEGORIES
    )


# ============================================================
# DETALLE DEL MATERIAL
# ============================================================

@app.route("/material/<int:item_id>")
def detalle(item_id):

    try:

        response = (
            supabase.table("materials")
            .select("*")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            abort(404)

        item = response.data[0]

        # Generar URL temporal para la plantilla detail.html.
        prepare_material(item)

        return render_template(
            "detail.html",
            item=item,
            categories=CATEGORIES
        )

    except Exception as error:

        if getattr(error, "code", None) == "404":
            abort(404)

        app.logger.exception(
            "Error consultando el material %s.",
            item_id
        )

        raise


# ============================================================
# EDITAR MATERIAL
# ============================================================

@app.route("/material/<int:item_id>/editar", methods=["GET", "POST"])
@login_required
def editar(item_id):

    try:

        response = (
            supabase.table("materials")
            .select("*")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            abort(404)

        item = response.data[0]

        if request.method == "POST":

            title = request.form.get(
                "title", ""
            ).strip()

            description = request.form.get(
                "description", ""
            ).strip()

            body = request.form.get(
                "body", ""
            ).strip()

            author = request.form.get(
                "author", ""
            ).strip()

            material_date = request.form.get(
                "material_date", ""
            ).strip()

            if not title:
                flash(
                    "El título no puede estar vacío.",
                    "warning"
                )

                return render_template(
                    "form.html",
                    item=item,
                    categories=CATEGORIES
                )

            updated_data = {
                "title": title,
                "description": description,
                "body": body,
                "author": author,
                "material_date": material_date,
            }

            supabase.table(
                "materials"
            ).update(
                updated_data
            ).eq(
                "id", item_id
            ).execute()

            flash(
                "El material se actualizó correctamente.",
                "success"
            )

            return redirect(
                url_for("detalle", item_id=item_id)
            )

        return render_template(
            "form.html",
            item=item,
            categories=CATEGORIES
        )

    except Exception:
        app.logger.exception(
            "Error editando el material %s.",
            item_id
        )

        flash(
            "No se pudo editar el material.",
            "danger"
        )

        return redirect(
            url_for("detalle", item_id=item_id)
        )


# ============================================================
# ELIMINAR MATERIAL
# ============================================================

@app.route("/material/<int:item_id>/eliminar", methods=["POST"])
@login_required
def eliminar(item_id):

    try:

        response = (
            supabase.table("materials")
            .select("id, title, stored_name")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            abort(404)

        item = response.data[0]

        # Eliminar primero el registro de la base de datos.
        supabase.table(
            "materials"
        ).delete().eq(
            "id", item_id
        ).execute()

        # Después eliminar el archivo de Storage.
        if item.get("stored_name"):
            delete_file(item["stored_name"])

        flash(
            "El material se eliminó correctamente.",
            "success"
        )

    except Exception:
        app.logger.exception(
            "Error eliminando el material %s.",
            item_id
        )

        flash(
            "No se pudo eliminar el material.",
            "danger"
        )

    return redirect(url_for("index"))


# ============================================================
# VISUALIZAR ARCHIVOS
# ============================================================

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):

    file_url = get_file_url(filename)

    if not file_url:
        abort(404)

    return redirect(file_url)


# ============================================================
# DESCARGAR ARCHIVOS
# ============================================================

@app.route("/material/<int:item_id>/descargar")
def descargar(item_id):

    try:

        response = (
            supabase.table("materials")
            .select("id, stored_name, original_name")
            .eq("id", item_id)
            .limit(1)
            .execute()
        )

        if not response.data:
            abort(404)

        item = response.data[0]

        stored_name = item.get("stored_name")

        if not stored_name:
            abort(404)

        file_url = get_file_url(stored_name)

        if not file_url:
            abort(404)

        return redirect(file_url)

    except Exception:
        app.logger.exception(
            "Error descargando el material %s.",
            item_id
        )
        abort(404)


# ============================================================
# COMPROBACIÓN DEL SERVIDOR
# ============================================================

@app.route("/health")
def health():

    return {
        "status": "ok",
        "database_configured": bool(
            SUPABASE_URL and SUPABASE_KEY
        ),
        "bucket_configured": bool(
            SUPABASE_BUCKET
        ),
    }


# ============================================================
# ERRORES
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return render_template(
        "404.html"
    ), 404


@app.errorhandler(413)
def file_too_large(error):

    flash(
        "El archivo supera el límite permitido de 50 MB.",
        "danger"
    )

    return redirect(url_for("nuevo"))


# ============================================================
# EJECUCIÓN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
