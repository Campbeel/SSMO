from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import os
import re
import base64
import secrets
import enum
import functools
import smtplib
from email.message import EmailMessage

from flask import Flask, flash, redirect, render_template, request, url_for, jsonify, abort, session, make_response, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey
from argon2 import PasswordHasher
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from io import BytesIO

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "ssmo.db"

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", "cambio-esto-en-produccion"),
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DATABASE_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
app.config["DEV_SHOW_USER"] = os.environ.get("DEV_SHOW_USER", "0") in {"1", "true", "TRUE", "yes", "on"}

db = SQLAlchemy(app)


class UserRole(enum.Enum):
    admin = "admin"
    cosam = "cosam"
    centro = "centro"


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Super administrador (puede gestionar usuarios de todos los dominios)
    is_master_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    _ph = PasswordHasher()

    def set_password(self, password: str) -> None:
        self.password_hash = self._ph.hash(password)

    def verify_password(self, password: str) -> bool:
        try:
            return self._ph.verify(self.password_hash, password)
        except Exception:
            return False


class GESCondition(db.Model):
    __tablename__ = "ges_conditions"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Case(db.Model):
    __tablename__ = "cases"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    form_id = db.Column(db.Integer, ForeignKey("medical_forms.id"), nullable=False, index=True)
    status = db.Column(db.String(20), default="enviado", index=True)  # enviado|aceptado|devuelto|atendido
    prioridad = db.Column(db.String(10), nullable=True, index=True)     # bajo|medio|alto
    atendido = db.Column(db.Boolean, default=False, nullable=False, index=True)
    sender_center_user_id = db.Column(db.Integer, ForeignKey("users.id"), nullable=True, index=True)
    accepted_by_cosam_user_id = db.Column(db.Integer, ForeignKey("users.id"), nullable=True, index=True)


class Appointment(db.Model):
    __tablename__ = "appointments"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, ForeignKey("cases.id"), nullable=False, index=True)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    place = db.Column(db.String(160))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ReturnEvent(db.Model):
    __tablename__ = "return_events"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, ForeignKey("cases.id"), nullable=False, index=True)
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# -------------------- Utilidades --------------------

def _send_email(to_email: str, subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASSWORD")
    port = int(os.environ.get("SMTP_PORT", "587"))
    sender = os.environ.get("MAIL_FROM", user or "no-reply@example.local")
    if not (host and sender and to_email):
        return False
    try:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            if user and pwd:
                s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception:
        return False


# Decorador de autorización (definición temprana para evitar NameError en import)
def login_required(roles: Optional[List[UserRole]] = None):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            cu = globals().get("_current_user")
            user = cu() if callable(cu) else None
            if not user:
                return redirect(url_for("login", next=request.path))
            if roles:
                allowed = [r.value if isinstance(r, UserRole) else str(r) for r in roles]
                if getattr(user, "role", None) not in allowed:
                    abort(403)
            g.current_user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator

@dataclass
class MedicalForm(db.Model):
    """Modelo que representa un formulario médico almacenado."""

    __tablename__ = "medical_forms"

    id: int = db.Column(db.Integer, primary_key=True)
    created_at: datetime = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    servicio_salud: str = db.Column(db.String(120), nullable=False, default="Metropolitano Oriente")
    establecimiento: str = db.Column(db.String(120))
    especialidad: str = db.Column(db.String(120))
    unidad: str = db.Column(db.String(120))
    nombre: str = db.Column(db.String(160), nullable=False)
    historia_clinica: str = db.Column(db.String(120))
    rut: str = db.Column(db.String(20))
    rut_padre: str = db.Column(db.String(20))
    sexo: str = db.Column(db.String(20))
    fecha_nacimiento: str = db.Column(db.String(20))
    edad: str = db.Column(db.String(20))
    domicilio: str = db.Column(db.String(160))
    comuna: str = db.Column(db.String(80))
    telefono1: str = db.Column(db.String(40))
    telefono2: str = db.Column(db.String(40))
    correo1: str = db.Column(db.String(120))
    correo2: str = db.Column(db.String(120))
    establecimiento_derivacion: str = db.Column(db.String(160))
    grupo_poblacional: str = db.Column(db.String(40))
    tipo_consulta: str = db.Column(db.String(40))
    tiene_terapias: str = db.Column(db.String(10))
    terapias_otro: str = db.Column(db.Text)
    hipotesis_diagnostico: str = db.Column(db.Text)
    es_ges: str = db.Column(db.String(10))
    fundamento_diagnostico: str = db.Column(db.Text)
    examenes_realizados: str = db.Column(db.Text)
    nombre_medico: str = db.Column(db.String(160))
    rut_medico: str = db.Column(db.String(20))
    patologias_ges: str = db.Column(db.Text)

    def patologias_ges_lista(self) -> List[str]:
        if not self.patologias_ges:
            return []
        return [item.strip() for item in self.patologias_ges.split(";") if item.strip()]

    def resumen_texto(self) -> str:
        """Genera un texto de resumen con los datos del formulario."""

        patologias = ", ".join(self.patologias_ges_lista()) or "Sin patologí­as GES registradas"
        return (
            "FORMULARIO GUARDADO\n"
            "===================\n\n"
            f"Fecha de registro: {self.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            "DATOS PERSONALES\n"
            f"- Nombre: {self.nombre or 'No especificado'}\n"
            f"- RUT: {self.rut or 'No especificado'}\n"
            f"- Fecha de nacimiento: {self.fecha_nacimiento or 'No especificada'}\n"
            f"- Edad: {self.edad or 'No especificada'}\n"
            f"- Comuna: {self.comuna or 'No especificada'}\n\n"
            "DATOS MÉDICOS\n"
            f"- Especialidad: {self.especialidad or 'No especificada'}\n"
            f"- Tipo de consulta: {self.tipo_consulta or 'No especificado'}\n"
            f"- Hipótesis diagnóstica: {self.hipotesis_diagnostico or 'No especificada'}\n"
            f"- Exámenes realizados: {self.examenes_realizados or 'No especificados'}\n"
            f"- Médico responsable: {self.nombre_medico or 'No especificado'}\n\n"
            "GES\n"
            f"- Patología GES: {patologias}\n"
            f"- \n"
        )

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "servicio_salud": self.servicio_salud,
            "establecimiento": self.establecimiento,
            "especialidad": self.especialidad,
            "unidad": self.unidad,
            "nombre": self.nombre,
            "historia_clinica": self.historia_clinica,
            "rut": self.rut,
            "rut_padre": self.rut_padre,
            "sexo": self.sexo,
            "fecha_nacimiento": self.fecha_nacimiento,
            "edad": self.edad,
            "domicilio": self.domicilio,
            "comuna": self.comuna,
            "telefono1": self.telefono1,
            "telefono2": self.telefono2,
            "correo1": self.correo1,
            "correo2": self.correo2,
            "establecimiento_derivacion": self.establecimiento_derivacion,
            "grupo_poblacional": self.grupo_poblacional,
            "tipo_consulta": self.tipo_consulta,
            "tiene_terapias": self.tiene_terapias,
            "terapias_otro": self.terapias_otro,
            "hipotesis_diagnostico": self.hipotesis_diagnostico,
            "es_ges": self.es_ges,
            "fundamento_diagnostico": self.fundamento_diagnostico,
            "examenes_realizados": self.examenes_realizados,
            "nombre_medico": self.nombre_medico,
            "rut_medico": self.rut_medico,
            "patologias_ges": self.patologias_ges_lista(),
        }


FORM_FIELDS: List[str] = [
    "servicio_salud",
    "establecimiento",
    "especialidad",
    "unidad",
    "nombre",
    "historia_clinica",
    "rut",
    "rut_padre",
    "sexo",
    "fecha_nacimiento",
    "edad",
    "domicilio",
    "comuna",
    "telefono1",
    "telefono2",
    "correo1",
    "correo2",
    "establecimiento_derivacion",
    "grupo_poblacional",
    "tipo_consulta",
    "tiene_terapias",
    "terapias_otro",
    "hipotesis_diagnostico",
    "es_ges",
    "fundamento_diagnostico",
    "examenes_realizados",
    "nombre_medico",
    "rut_medico",
]

PATOLOGIAS_GES: List[str] = [
    "Esquizofrenia",
    "Depresión (para mayores de 15 años)",
    "Trastorno bipolar (para mayores de 15 años)",
    "Demencia (incluida la enfermedad de Alzheimer)",
    "Consumo problemático de alcohol y drogas en menores de 20 años (riesgo bajo a moderado)",
    "Tratamiento de hospitalización para menores de 15 años con depresión grave",
]

COMUNAS: List[str] = [
    "Las Condes",
    "Lo Barnechea",
    "La Reina",
    "Macul",
    "Ñuñoa",
    "Peñalolén",
    "Providencia",
    "Vitacura",
    "Isla de Pascua",
]

TIPOS_CONSULTA: List[str] = [
    "Presencial",
    "Telemedicina",
    "Otro",
]

# -------------------- API para Postman (rama main) --------------------
@app.route("/api/forms", methods=["GET"])
@login_required([UserRole.centro, UserRole.cosam])
def api_forms_list():
    forms = MedicalForm.query.order_by(MedicalForm.id.desc()).all()
    return jsonify([f.to_dict() for f in forms])


@app.route("/api/forms/<int:form_id>", methods=["GET"])
@login_required([UserRole.centro, UserRole.cosam])
def api_forms_detail(form_id: int):
    form = MedicalForm.query.get(form_id)
    if not form:
        abort(404)
    return jsonify(form.to_dict())


@app.route("/formularios/<int:form_id>/pdf", methods=["GET"])
@login_required([UserRole.centro, UserRole.cosam])
def form_pdf(form_id: int):
    f = MedicalForm.query.get_or_404(form_id)
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 50
    # Logo
    try:
        logo_path = BASE_DIR / 'static' / 'img' / 'logo-minsal.png'
        c.drawImage(ImageReader(str(logo_path)), 40, y-40, width=100, height=30, preserveAspectRatio=True, mask='auto')
    except Exception:
        pass
    c.setFont("Helvetica-Bold", 14)
    c.drawString(160, y-20, "Solicitud de Interconsulta o Derivación")
    y -= 60
    c.setFont("Helvetica", 10)
    lines = f.resumen_texto().splitlines()
    for line in lines:
        if y < 60:
            c.showPage(); y = h - 40; c.setFont("Helvetica", 10)
        c.drawString(40, y, line)
        y -= 14
    c.showPage(); c.save(); buf.seek(0)
    from flask import send_file
    filename = f"ficha_{f.id}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype='application/pdf')


# -------------------- Autenticación y administración --------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    # Si ya está autenticado, envía al inicio de su rol
    if getattr(g, "current_user", None) and request.method == "GET":
        return redirect(_role_default_target(g.current_user.role))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        u = User.query.filter_by(username=username).first()
        if not u or not u.verify_password(password) or not u.is_active:
            flash("Credenciales inválidas", "error")
            return render_template("login.html")
        token = _issue_jwt(u)
        # Redirección por rol, ignorando "next" si no es accesible para el rol
        requested = request.args.get("next")
        target = requested if _is_next_allowed_for_role(requested, u.role) else _role_default_target(u.role)
        # Persistir identidad en sesión del servidor (además del JWT)
        session["uid"] = u.id
        session["role"] = u.role
        resp = make_response(redirect(target))
        resp.set_cookie(_AUTH_COOKIE, token, **_cookie_kwargs())
        flash(f"Sesión iniciada como {u.username} ({u.role})", "success")
        return resp
    return render_template("login.html")


@app.route("/logout", methods=["POST"]) 
def logout():
    resp = make_response(redirect(url_for("login")))
    resp.delete_cookie(_AUTH_COOKIE, path="/")
    session.clear()
    return resp


@app.cli.command("create-user")
def create_user_cli():
    import getpass
    db.create_all()
    # asegurar columna is_master_admin para CLI
    try:
        conn = db.engine.raw_connection(); cur = conn.cursor()
        cur.execute("PRAGMA table_info('users')"); cols = [r[1] for r in cur.fetchall()]
        if 'is_master_admin' not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN is_master_admin BOOLEAN NOT NULL DEFAULT 0")
        conn.commit(); conn.close()
    except Exception:
        pass
    username = input("Usuario: ").strip()
    role = (input("Rol [admin|cosam|centro]: ").strip() or "centro").lower()
    is_master_raw = (input("¿Admin maestro? [s/N]: ").strip() or "n").lower()
    is_master = is_master_raw in {"s", "si", "sí", "y", "yes", "1", "true"}
    pw = getpass.getpass("Contraseña: ")
    if not username or role not in {"admin", "cosam", "centro"} or len(pw) < 8:
        print("Datos inválidos")
        return
    if User.query.filter_by(username=username).first():
        print("Usuario ya existe")
        return
    u = User(username=username, role=role)
    try:
        setattr(u, 'is_master_admin', bool(is_master))
    except Exception:
        pass
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()
    print(f"Usuario creado: {username} ({role}) | master={'sí' if is_master else 'no'}")


@app.route("/admin/users", methods=["GET", "POST"])
@login_required([UserRole.admin])
def admin_users():
    def _domain(email: str) -> str:
        try:
            return (email or '').split('@', 1)[1].lower()
        except Exception:
            return ''
    current = g.current_user
    is_master = bool(getattr(current, 'is_master_admin', False))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        role = (request.form.get("role") or "centro").strip().lower()
        password = request.form.get("password") or ""
        if not _is_valid_email(username) or role not in {"admin", "cosam", "centro"} or len(password) < 8:
            flash("Datos inválidos (rol o contraseña)", "error")
        elif not is_master and _domain(username) != _domain(current.username):
            flash("Solo puede crear usuarios de su propio dominio.", "error")
        elif User.query.filter_by(username=username).first():
            flash("El usuario ya existe", "error")
        else:
            u = User(username=username, role=role)
            u.set_password(password)
            # Solo master puede marcar como master (no se expone en UI de no-master)
            if is_master and (request.form.get("is_master_admin") == "on"):
                try:
                    u.is_master_admin = True
                except Exception:
                    pass
            db.session.add(u)
            db.session.commit()
            flash("Usuario creado", "success")
    # Listado: master ve todos; no-master solo su dominio
    if is_master:
        users = User.query.order_by(User.created_at.desc()).all()
    else:
        from sqlalchemy import func
        dom = _domain(current.username)
        users = (
            User.query
            .filter(func.lower(User.username).like(f"%@@{dom}".replace('@@','@')))
            .order_by(User.created_at.desc())
            .all()
        )
    return render_template("admin_users.html", users=users, is_master=is_master)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required([UserRole.admin])
def admin_user_edit(user_id: int):
    u = User.query.get_or_404(user_id)
    def _domain(email: str) -> str:
        try:
            return (email or '').split('@', 1)[1].lower()
        except Exception:
            return ''
    current = g.current_user
    is_master = bool(getattr(current, 'is_master_admin', False))
    if not is_master and _domain(u.username) != _domain(current.username):
        abort(403)
    if request.method == "POST":
        email = (request.form.get("username") or "").strip()
        role = (request.form.get("role") or u.role).strip().lower()
        active = True if request.form.get("is_active") == "on" else False
        newpass = request.form.get("password") or ""
        if not _is_valid_email(email) or role not in {"admin", "cosam", "centro"}:
            flash("Datos inválidos", "error")
        else:
            if not is_master and _domain(email) != _domain(current.username):
                flash("Solo puede actualizar usuarios de su propio dominio.", "error")
                return render_template("admin_user_edit.html", user=u, is_master=is_master)
            if email != u.username and User.query.filter_by(username=email).first():
                flash("Ya existe un usuario con ese correo", "error")
            else:
                u.username = email
                u.role = role
                u.is_active = active
                if is_master:
                    u.is_master_admin = True if (request.form.get("is_master_admin") == "on") else False
                if newpass:
                    if len(newpass) < 8:
                        flash("La contraseña debe tener al menos 8 caracteres", "error")
                        return render_template("admin_user_edit.html", user=u, is_master=is_master)
                    u.set_password(newpass)
                db.session.commit()
                flash("Usuario actualizado", "success")
                return redirect(url_for("admin_users"))
    return render_template("admin_user_edit.html", user=u, is_master=is_master)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"]) 
@login_required([UserRole.admin])
def admin_user_delete(user_id: int):
    u = User.query.get_or_404(user_id)
    if u.id == g.current_user.id:
        flash("No puede eliminar su propio usuario", "error")
        return redirect(url_for("admin_users"))
    def _domain(email: str) -> str:
        try:
            return (email or '').split('@', 1)[1].lower()
        except Exception:
            return ''
    current = g.current_user
    if not getattr(current, 'is_master_admin', False):
        # no-master: prohibido borrar masters o fuera de su dominio
        try:
            if getattr(u, 'is_master_admin', False):
                abort(403)
        except Exception:
            pass
        if _domain(u.username) != _domain(current.username):
            abort(403)
    db.session.delete(u)
    db.session.commit()
    flash("Usuario eliminado", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/ges", methods=["GET", "POST"]) 
@login_required([UserRole.admin])
def admin_ges():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        action = (request.form.get("action") or "").strip()
        cond_id = request.form.get("id")
        if action == "add" and name:
            if not GESCondition.query.filter_by(name=name).first():
                db.session.add(GESCondition(name=name, active=True))
                db.session.commit()
                flash("Patología GES agregada", "success")
        elif action in {"toggle", "delete"} and cond_id:
            cond = GESCondition.query.get(int(cond_id))
            if cond:
                if action == "toggle":
                    cond.active = not cond.active
                    db.session.commit()
                    flash("Estado actualizado", "success")
                else:
                    db.session.delete(cond)
                    db.session.commit()
                    flash("Eliminado", "success")
        return redirect(url_for("admin_ges"))
    items = GESCondition.query.order_by(GESCondition.active.desc(), GESCondition.name.asc()).all()
    return render_template("admin_ges.html", items=items)


@app.cli.command("list-users")
def list_users_cli():
    """Lista usuarios existentes (username, rol, activo, creado)."""
    db.create_all()
    users = User.query.order_by(User.created_at.asc()).all()
    if not users:
        print("No hay usuarios.")
        return
    for u in users:
        is_master = getattr(u, 'is_master_admin', False)
        print(f"- {u.id}: {u.username} | rol={u.role} | master={'sí' if is_master else 'no'} | activo={'sí' if u.is_active else 'no'} | creado={u.created_at:%Y-%m-%d %H:%M}")


@app.cli.command("promote-master")
def promote_master_cli():
    """Promueve un usuario existente a admin maestro."""
    username = input("Usuario (correo) a promover: ").strip()
    u = User.query.filter_by(username=username).first()
    if not u:
        print("Usuario no encontrado")
        return
    u.is_master_admin = True
    db.session.commit()
    print(f"Usuario {username} promovido a admin maestro")


@app.cli.command("demote-master")
def demote_master_cli():
    """Quita privilegios de admin maestro a un usuario."""
    username = input("Usuario (correo) a despromover: ").strip()
    u = User.query.filter_by(username=username).first()
    if not u:
        print("Usuario no encontrado")
        return
    u.is_master_admin = False
    db.session.commit()
    print(f"Usuario {username} ya no es admin maestro")


# -------------------- Bandejas COSAM / Centro --------------------

@app.route("/cosam/inbox")
@login_required([UserRole.cosam])
def cosam_inbox():
    items = (
        Case.query.filter(Case.status == "enviado")
        .order_by(Case.created_at.desc())
        .all()
    )
    # join simple: obtener formularios
    by_form = {c.form_id: c for c in items}
    forms = MedicalForm.query.filter(MedicalForm.id.in_(list(by_form.keys()))).all()
    pares = [(by_form[f.id], f) for f in forms]
    return render_template("cosam_inbox.html", casos=pares)


@app.route("/cosam/accept/<int:case_id>", methods=["GET", "POST"])
@login_required([UserRole.cosam])
def cosam_accept(case_id: int):
    c: Optional[Case] = Case.query.get_or_404(case_id)
    f: Optional[MedicalForm] = MedicalForm.query.get_or_404(c.form_id)
    if request.method == "POST":
        prioridad = (request.form.get("prioridad") or "").lower()
        if prioridad not in {"bajo", "medio", "alto"}:
            flash("Prioridad inválida", "error")
        else:
            c.prioridad = prioridad
            c.status = "aceptado"
            c.accepted_by_cosam_user_id = g.current_user.id
            db.session.commit()
            flash("Ficha aceptada", "success")
            return redirect(url_for("cosam_inbox"))
    return render_template("cosam_accept.html", caso=c, form=f)


@app.route("/cosam/pacientes")
@login_required([UserRole.cosam])
def cosam_pacientes():
    from sqlalchemy import case
    porder = case((Case.prioridad == "alto", 0), (Case.prioridad == "medio", 1), else_=2)
    casos = (
        Case.query.filter(Case.status == "aceptado", Case.atendido == False)
        .order_by(porder, Case.created_at.desc())
        .all()
    )
    forms = {f.id: f for f in MedicalForm.query.filter(MedicalForm.id.in_([c.form_id for c in casos])).all()}
    appts = {a.case_id: a for a in Appointment.query.filter(Appointment.case_id.in_([c.id for c in casos])).all()}
    triples = [(c, forms.get(c.form_id), appts.get(c.id)) for c in casos]
    return render_template("patients_list.html", casos=triples)


@app.route("/cosam/agenda")
@login_required([UserRole.cosam])
def cosam_agenda():
    # próximas 30 días
    now = datetime.utcnow()
    horizon = now + timedelta(days=30)
    aps = (Appointment.query
           .filter(Appointment.scheduled_at >= now, Appointment.scheduled_at <= horizon)
           .order_by(Appointment.scheduled_at.asc())
           .all())
    case_map = {c.id: c for c in Case.query.filter(Case.id.in_([a.case_id for a in aps])).all()}
    form_map = {f.id: f for f in MedicalForm.query.filter(MedicalForm.id.in_([case_map[a.case_id].form_id for a in aps if a.case_id in case_map])).all()}
    items = []
    for a in aps:
        c = case_map.get(a.case_id)
        f = form_map.get(c.form_id) if c else None
        if f:
            items.append((a, c, f))
    return render_template("cosam_agenda.html", items=items)


@app.route("/cosam/reschedule/<int:case_id>", methods=["GET", "POST"]) 
@login_required([UserRole.cosam])
def cosam_reschedule(case_id: int):
    caso: Optional[Case] = Case.query.get_or_404(case_id)
    ap = Appointment.query.filter_by(case_id=caso.id).first()
    form: Optional[MedicalForm] = MedicalForm.query.get_or_404(caso.form_id)
    if not ap and request.method == "GET":
        # si no existe, manda a crear
        return redirect(url_for("cosam_schedule", case_id=case_id))
    if request.method == "POST":
        date_str = (request.form.get("date") or "").strip()
        time_str = (request.form.get("time") or "").strip()
        place = (request.form.get("place") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        try:
            when = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            flash("Fecha/hora inválida", "error")
            return render_template("cosam_schedule.html", caso=caso, form=form)
        if ap:
            ap.scheduled_at = when
            ap.place = place
            ap.notes = notes
        else:
            ap = Appointment(case_id=caso.id, scheduled_at=when, place=place, notes=notes)
            db.session.add(ap)
        db.session.commit()
        destinatario = form.correo1 or form.correo2
        if destinatario:
            _send_email(destinatario, "Reagendamiento de hora",
                        f"Estimado/a {form.nombre}, su hora fue reagendada para {when.strftime('%d/%m/%Y %H:%M')} en {place or 'COSAM'}.")
        flash("Hora actualizada", "success")
        return redirect(url_for("cosam_pacientes"))
    # GET con ap existente: prellenar
    return render_template("cosam_schedule.html", caso=caso, form=form)


@app.route("/cosam/attend/<int:case_id>", methods=["POST"]) 
@login_required([UserRole.cosam])
def cosam_attend(case_id: int):
    c: Optional[Case] = Case.query.get_or_404(case_id)
    c.atendido = True
    c.status = "atendido"
    db.session.commit()
    flash("Caso marcado como atendido", "success")
    return redirect(url_for("cosam_pacientes"))


@app.route("/cosam/return/<int:case_id>", methods=["GET", "POST"]) 
@login_required([UserRole.cosam])
def cosam_return(case_id: int):
    c: Optional[Case] = Case.query.get_or_404(case_id)
    f: Optional[MedicalForm] = MedicalForm.query.get_or_404(c.form_id)
    if request.method == "POST":
        c.status = "devuelto"
        # registrar evento de devolución con motivo opcional
        reason = (request.form.get("reason") or "").strip()
        db.session.add(ReturnEvent(case_id=c.id, reason=reason or None))
        db.session.commit()
        flash("Ficha devuelta al centro de origen", "success")
        return redirect(url_for("cosam_pacientes"))
    return render_template("cosam_return.html", caso=c, form=f)


@app.route("/cosam/schedule/<int:case_id>", methods=["GET", "POST"]) 
@login_required([UserRole.cosam])
def cosam_schedule(case_id: int):
    caso: Optional[Case] = Case.query.get_or_404(case_id)
    form: Optional[MedicalForm] = MedicalForm.query.get_or_404(caso.form_id)
    # Evitar doble agendamiento
    existing = Appointment.query.filter_by(case_id=caso.id).first()
    if existing and request.method == "GET":
        flash("Este paciente ya tiene hora agendada.", "error")
        return redirect(url_for("cosam_pacientes"))
    if request.method == "POST":
        date_str = (request.form.get("date") or "").strip()
        time_str = (request.form.get("time") or "").strip()
        place = (request.form.get("place") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        try:
            when = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            flash("Fecha/hora inválida", "error")
            return render_template("cosam_schedule.html", caso=caso, form=form)
        if existing:
            flash("Ya tenía hora agendada.", "error")
            return redirect(url_for("cosam_pacientes"))
        ap = Appointment(case_id=caso.id, scheduled_at=when, place=place, notes=notes)
        db.session.add(ap)
        db.session.commit()
        # Email al paciente (si existe correo)
        destinatario = form.correo1 or form.correo2
        if destinatario:
            _send_email(destinatario, "Confirmación de hora",
                        f"Estimado/a {form.nombre}, su hora fue agendada para {when.strftime('%d/%m/%Y %H:%M')} en {place or 'COSAM'}.\nSaludos.")
        flash("Hora agendada", "success")
        return redirect(url_for("cosam_pacientes"))
    return render_template("cosam_schedule.html", caso=caso, form=form)


@app.route("/cosam/return/<int:case_id>/prefill")
@login_required([UserRole.cosam])
def cosam_return_prefill(case_id: int):
    caso: Optional[Case] = Case.query.get_or_404(case_id)
    return redirect(url_for('formulario', prefill_from=caso.form_id))


@app.route("/centro/inbox")
@login_required([UserRole.centro])
def centro_inbox():
    user = g.current_user
    enviados = (
        Case.query.filter(Case.sender_center_user_id == user.id, Case.status != "devuelto")
        .order_by(Case.created_at.desc())
        .all()
    )
    recibidos = (
        Case.query.filter(Case.sender_center_user_id == user.id, Case.status == "devuelto")
        .order_by(Case.created_at.desc())
        .all()
    )
    forms_map = {f.id: f for f in MedicalForm.query.filter(MedicalForm.id.in_([c.form_id for c in enviados + recibidos])).all()}
    # razones de devolución (última por caso)
    reasons = {}
    if recibidos:
        last_events = (
            ReturnEvent.query
            .filter(ReturnEvent.case_id.in_([c.id for c in recibidos]))
            .order_by(ReturnEvent.case_id.asc(), ReturnEvent.created_at.asc())
            .all()
        )
        for ev in last_events:
            reasons[ev.case_id] = ev  # queda el último por orden ascendente
    enviados_pares = [(c, forms_map.get(c.form_id)) for c in enviados]
    recibidos_pares = [(c, forms_map.get(c.form_id), reasons.get(c.id)) for c in recibidos]
    return render_template("centro_inbox.html", enviados=enviados_pares, recibidos=recibidos_pares)


@app.cli.command("seed-db")
def seed_db():
    """Crea la base y agrega un registro de ejemplo alineado al formulario actual."""
    db.drop_all()
    db.create_all()

    ejemplo = MedicalForm(
        servicio_salud="Metropolitano Oriente",
        establecimiento="CESFAM Ejemplo",
        especialidad="Psicología",
        unidad="Unidad A",
        nombre="Paciente Demo",
        historia_clinica="12345",
        rut="12.345.678-9",
        sexo="Femenino",
        fecha_nacimiento="1990-05-15",
        edad="35",
        domicilio="Av. Siempre Viva 742",
        comuna="Ñuñoa",
        telefono1="987654321",
        correo1="paciente@example.com",
        establecimiento_derivacion="COSAM SSMO",
        tipo_consulta="Confirmación diagnóstica",
        hipotesis_diagnostico="Ansiedad",
        es_ges="No",
        fundamento_diagnostico="Clínica compatible",
        examenes_realizados="Sin exámenes",
        nombre_medico="Dra. Prueba",
        rut_medico="20.123.456-7",
        patologias_ges="Depresión; Esquizofrenia",
    )
    db.session.add(ejemplo)
    db.session.commit()
    print("Base creada y sembrada (main): 1 formulario de ejemplo.")
def _limpiar_rut(rut: str) -> str:
    return "".join(ch for ch in rut if ch.isdigit() or ch in {"K", "k"})


def _digito_verificador(cuerpo: str) -> str:
    suma = 0
    factor = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = suma % 11
    if resto == 0:
        return "0"
    if resto == 1:
        return "K"
    return str(11 - resto)


def _normalizar_rut(rut: str) -> str:
    if not rut:
        return ""
    limpio = _limpiar_rut(rut)
    if len(limpio) < 2 or not limpio[:-1].isdigit():
        return rut.strip()
    cuerpo = limpio[:-1]
    dv = limpio[-1].upper()
    esperado = _digito_verificador(cuerpo)
    if dv == "0" and esperado == "K":
        dv = "K"
    cuerpo_formateado = f"{int(cuerpo):,}".replace(",", ".")
    return f"{cuerpo_formateado}-{dv}"


def _calcular_edad(fecha_nacimiento: str) -> str:
    if not fecha_nacimiento:
        return ""
    try:
        nacimiento = datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date()
    except ValueError:
        return ""
    hoy: date = datetime.utcnow().date()
    edad = hoy.year - nacimiento.year - (
        (hoy.month, hoy.day) < (nacimiento.month, nacimiento.day)
    )
    return str(max(0, edad))


def _extraer_datos_formulario(form_data) -> Dict[str, Optional[str]]:
    datos = {campo: form_data.get(campo) or "" for campo in FORM_FIELDS}
    patologias = form_data.getlist("patologias_ges")
    datos["patologias_ges"] = ";".join(patologias)
    datos["edad"] = _calcular_edad(datos.get("fecha_nacimiento", ""))
    tipo_consulta = form_data.get("tipo_consulta") or ""
    detalle_otro = form_data.get("tipo_consulta_otro", "").strip()
    datos["tipo_consulta_detalle"] = detalle_otro if tipo_consulta == "Otro" else ""
    datos["tipo_consulta"] = tipo_consulta
    for rut_field in ("rut", "rut_padre", "rut_medico"):
        datos[rut_field] = _normalizar_rut(datos.get(rut_field, ""))
    # Servicio de Salud fijo según requerimiento
    datos["servicio_salud"] = "Metropolitano Oriente"
    return datos


def _validar_datos(datos: Dict[str, str]) -> List[str]:
    errores: List[str] = []
    if not datos["nombre"].strip():
        errores.append("El nombre del paciente es obligatorio.")
    if not datos["servicio_salud"].strip():
        errores.append("Debe indicar el servicio de salud.")

    # Reglas adicionales solicitadas
    if not (datos.get("rut", "").strip()):
        errores.append("El RUT del paciente es obligatorio.")
    if not (datos.get("fecha_nacimiento", "").strip()):
        errores.append("La fecha de nacimiento es obligatoria.")
    if not (datos.get("telefono1", "").strip()):
        errores.append("El teléfono 1 es obligatorio.")
    correo1 = (datos.get("correo1", "").strip())
    if not correo1:
        errores.append("El correo 1 es obligatorio.")

    # Email válido (básico)
    def _email_valido(correo: str) -> bool:
        return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", correo or "", re.IGNORECASE))

    if correo1 and not _email_valido(correo1):
        errores.append("El correo 1 no es válido.")
    correo2 = (datos.get("correo2", "").strip())
    if correo2 and not _email_valido(correo2):
        errores.append("El correo 2 no es válido.")
    # Teléfonos: solo dí­gitos y '+' inicial opcional
    telefono1 = (datos.get("telefono1", "").strip())
    telefono2 = (datos.get("telefono2", "").strip())
    telefono_pat = re.compile(r"^\+?\d+$")
    if telefono1 and not telefono_pat.match(telefono1):
        errores.append("El teléfono 1 solo puede contener números y un '+' inicial.")
    if telefono2 and not telefono_pat.match(telefono2):
        errores.append("El teléfono 2 solo puede contener números y un '+' inicial.")

    for rut_field in ("rut", "rut_padre", "rut_medico"):
        rut = datos.get(rut_field, "").strip()
        if rut and not _rut_valido(rut):
            errores.append(f"El RUT ingresado en '{rut_field}' no es válido.")
    # Datos médicos obligatorios mínimos
    if not (datos.get("tipo_consulta") or "").strip():
        errores.append("El tipo de consulta es obligatorio.")
    if not (datos.get("establecimiento_derivacion") or "").strip():
        errores.append("Debe indicar el establecimiento de derivación.")
    # Límites de texto (1000)
    for campo in ("hipotesis_diagnostico", "fundamento_diagnostico", "examenes_realizados"):
        if len((datos.get(campo) or "")) > 1000:
            errores.append(f"El campo '{campo}' excede el máximo de 1000 caracteres.")
    return errores


def _rut_valido(rut: str) -> bool:
    """Valida RUT chileno considerando dí­gito verificador."""

    limpio = _limpiar_rut(rut).upper()
    if len(limpio) < 2 or not limpio[:-1].isdigit():
        return False
    cuerpo = limpio[:-1]
    dv = limpio[-1]
    esperado = _digito_verificador(cuerpo)
    if dv == "0" and esperado == "K":
        dv = "K"
    return esperado == dv


@app.route("/", methods=["GET", "POST"])
@login_required([UserRole.centro, UserRole.cosam])
def formulario():
    if request.method == "POST":
        datos = _extraer_datos_formulario(request.form)
        errores = _validar_datos(datos)

        if errores:
            for error in errores:
                flash(error, "error")
            return render_template(
                "form.html",
                campos=datos,
                errores=errores,
            )

        detalle_otro = datos.pop("tipo_consulta_detalle", "")
        if datos.get("tipo_consulta") == "Otro" and detalle_otro:
            datos["tipo_consulta"] = f"Otro - {detalle_otro}"
        registro = MedicalForm(**datos)
        db.session.add(registro)
        db.session.commit()
        # Si hay un usuario centro autenticado, creamos un Case enlazado
        try:
            user = g.current_user
            role = getattr(user, "role", None)
        except Exception:
            user = None
            role = None
        if user and role == UserRole.centro.value:
            prio = (request.form.get("prioridad_sugerida") or "").lower()
            c = Case(form_id=registro.id, status="enviado", sender_center_user_id=user.id)
            if prio in {"bajo", "medio", "alto"}:
                c.prioridad = prio
            db.session.add(c)
            db.session.commit()
        flash("Formulario guardado correctamente.", "success")
        return redirect(url_for("ver_formulario", form_id=registro.id))

    valores_iniciales = {campo: "" for campo in FORM_FIELDS}
    valores_iniciales["servicio_salud"] = "Metropolitano Oriente"
    valores_iniciales["tipo_consulta_detalle"] = ""
    # Prefill desde ficha existente (solo COSAM)
    try:
        prefill_id = request.args.get("prefill_from")
        user = getattr(g, "current_user", None)
        if prefill_id and user and getattr(user, "role", None) == UserRole.cosam.value:
            src = MedicalForm.query.get(int(prefill_id))
            if src:
                # Datos personales
                valores_iniciales.update({
                    "nombre": src.nombre or "",
                    "rut": src.rut or "",
                    "sexo": src.sexo or "",
                    "fecha_nacimiento": src.fecha_nacimiento or "",
                    "edad": src.edad or "",
                    "domicilio": src.domicilio or "",
                    "comuna": src.comuna or "",
                    "telefono1": src.telefono1 or "",
                    "telefono2": src.telefono2 or "",
                    "correo1": src.correo1 or "",
                    "correo2": src.correo2 or "",
                    # Profesionales
                    "nombre_medico": src.nombre_medico or "",
                    "rut_medico": src.rut_medico or "",
                    # Derivación de vuelta al establecimiento origen
                    "establecimiento_derivacion": src.establecimiento or "",
                })
    except Exception:
        pass
    return render_template(
        "form.html",
        campos=valores_iniciales,
        errores=[],
    )


@app.route("/formularios")
@login_required([UserRole.cosam])
def listar_formularios():
    registros = MedicalForm.query.order_by(MedicalForm.created_at.desc()).all()
    return render_template("entries.html", registros=registros)


@app.route("/formularios/<int:form_id>")
@login_required([UserRole.centro, UserRole.cosam])
def ver_formulario(form_id: int):
    registro: Optional[MedicalForm] = MedicalForm.query.get_or_404(form_id)
    return render_template("summary.html", registro=registro)


# Catálogo de establecimientos de Atencií³n Primaria (CESFAM) por comuna
ESTABLECIMIENTOS_POR_COMUNA: Dict[str, List[str]] = {
    "Peñalolén": [
        "Cesfam Carol Urzúa",
        "Cesfam La Faena",
        "Cesfam Lo Hermida",
        "Cesfam San Luis",
        "Cesfam Cardenal Silva Henríquez",
        "Cesfam Padre Whelan",
        "Cesfam Las Torres",
    ],
    "Macul": [
        "Cesfam Félix de Amesti",
        "Cesfam Santa Julia",
        "Cesfam Alberto Hurtado",
    ],
    "Ñuñoa": [
        "Cesfam Rosita Renard",
        "Cesfam Salvador Bustos",
    ],
    "La Reina": [
        "Cesfam Ossandón",
        "Cesfam Juan Pablo II",
    ],
    "Providencia": [
        "Cesfam Hernán Alessandri",
        "Cesfam El Aguilucho",
        "Cesfam Dr. Alfonso Leng",
    ],
    "Las Condes": [
        "Cesfam Apoquindo",
        "Cesfam Aníbal Ariztía",
    ],
    "Vitacura": [
        "Cesfam Vitacura",
    ],
    "Lo Barnechea": [
        "Cesfam Lo Barnechea",
    ],
}# Catálogo de especialidades
ESPECIALIDADES: List[str] = [
    "Psiquiatrí­a Adulto",
    "Psiquiatrí­a Infanto Adolescente",
    "Quí­mico Farmacéutico",
    "Psicílogo(a)",
    "Trabajador(a) Social",
    "Terapeuta Ocupacional",
    "Enfermera(o)",
    "Psicopedagogo(a)",
]

# Normalización de textos con acentos (forzamos valores correctos)
COMUNAS = [
    "Las Condes",
    "Lo Barnechea",
    "La Reina",
    "Macul",
    "\u00d1u\u00f1oa",
    "Pe\u00f1alol\u00e9n",
    "Providencia",
    "Vitacura",
    "Isla de Pascua",
]

TIPOS_CONSULTA = [
    "Presencial",
    "Telemedicina",
    "Otro",
]

ESTABLECIMIENTOS_POR_COMUNA = {
    "Pe\u00f1alol\u00e9n": [
        "Cesfam Carol Urz\u00faa",
        "Cesfam La Faena",
        "Cesfam Lo Hermida",
        "Cesfam San Luis",
        "Cesfam Cardenal Silva Henr\u00edquez",
        "Cesfam Padre Whelan",
        "Cesfam Las Torres",
    ],
    "Macul": [
        "Cesfam F\u00e9lix de Amesti",
        "Cesfam Santa Julia",
        "Cesfam Alberto Hurtado",
    ],
    "\u00d1u\u00f1oa": [
        "Cesfam Rosita Renard",
        "Cesfam Salvador Bustos",
    ],
    "La Reina": [
        "Cesfam Ossand\u00f3n",
        "Cesfam Juan Pablo II",
    ],
    "Providencia": [
        "Cesfam Hern\u00e1n Alessandri",
        "Cesfam El Aguilucho",
        "Cesfam Dr. Alfonso Leng",
    ],
    "Las Condes": [
        "Cesfam Apoquindo",
        "Cesfam An\u00edbal Arizt\u00eda",
    ],
    "Vitacura": [
        "Cesfam Vitacura",
    ],
    "Lo Barnechea": [
        "Cesfam Lo Barnechea",
    ],
}

@app.context_processor
def inject_globals():
    # Cargar patologías GES desde DB si existen; si no, usar constante.
    try:
        items = GESCondition.query.filter_by(active=True).order_by(GESCondition.name.asc()).all()
        patologias = [it.name for it in items] if items else PATOLOGIAS_GES
    except Exception:
        patologias = PATOLOGIAS_GES
    return {
        "patologias_catalogo": patologias,
        "comunas_catalogo": COMUNAS,
        "tipos_consulta_catalogo": TIPOS_CONSULTA,
        "especialidades_catalogo": ESPECIALIDADES,
        "establecimientos_catalogo": ESTABLECIMIENTOS_POR_COMUNA,
    }


_db_initialized = False


@app.before_request
def inicializar_db():
    global _db_initialized
    if not _db_initialized:
        db.create_all()
        _db_initialized = True




# -------------------- JWT (Ed25519) y CSRF --------------------

_AUTH_COOKIE = "ssmo_auth"


def _load_jwt_keys() -> Tuple[bytes, bytes]:
    priv = os.environ.get("JWT_PRIVATE_KEY_PEM")
    pub = os.environ.get("JWT_PUBLIC_KEY_PEM")
    if priv and pub:
        return priv.encode("utf-8"), pub.encode("utf-8")
    sk = ed25519.Ed25519PrivateKey.generate()
    pk = sk.public_key()
    priv_pem = sk.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pk.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


_JWT_PRIV_PEM, _JWT_PUB_PEM = _load_jwt_keys()
_JWT_ALG = "EdDSA"


def _issue_jwt(user: User, ttl_minutes: int = 480) -> str:
    now = datetime.utcnow()
    payload: Dict[str, Any] = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    token = jwt.encode(payload, _JWT_PRIV_PEM, algorithm=_JWT_ALG)
    return token


def _decode_jwt(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, _JWT_PUB_PEM, algorithms=[_JWT_ALG])
    except Exception:
        return None


def _current_user() -> Optional[User]:
    # 1) Preferir sesión del servidor si existe
    uid = session.get("uid")
    if uid:
        try:
            u = User.query.get(int(uid))
            if u and u.is_active:
                return u
        except Exception:
            pass
    # 2) Fallback a cookie JWT
    token = request.cookies.get(_AUTH_COOKIE)
    if not token:
        return None
    data = _decode_jwt(token)
    if not data:
        return None
    uid = data.get("sub")
    if not uid:
        return None
    u = User.query.get(int(uid))
    if not u or not u.is_active:
        return None
    # Sincroniza a sesión para próximas peticiones
    session["uid"] = u.id
    session["role"] = u.role
    return u


def _role_default_target(role: str) -> str:
    if role == UserRole.admin.value:
        return url_for("admin_users")
    if role == UserRole.cosam.value:
        return url_for("cosam_inbox")
    if role == UserRole.centro.value:
        return url_for("centro_inbox")
    return url_for("formulario")


def _is_next_allowed_for_role(next_path: Optional[str], role: str) -> bool:
    if not next_path or not isinstance(next_path, str):
        return False
    if not next_path.startswith("/"):
        return False
    allowed = {
        UserRole.admin.value: ("/admin/", "/formularios", "/api/"),
        UserRole.cosam.value: ("/cosam/", "/formularios", "/api/"),
        UserRole.centro.value: ("/centro/", "/", "/formularios", "/api/"),
    }.get(role, ("/",))
    return next_path == "/" or any(next_path.startswith(p) for p in allowed)


def _cookie_kwargs():
    # Para facilitar dev, solo marcamos secure si la conexión es HTTPS
    return {"httponly": True, "secure": request.is_secure, "samesite": "Strict", "path": "/"}


def login_required(roles: Optional[List[UserRole]] = None):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*args, **kwargs):
            user = _current_user()
            if not user:
                return redirect(url_for("login", next=request.path))
            if roles:
                allowed = [r.value if isinstance(r, UserRole) else str(r) for r in roles]
                if user.role not in allowed:
                    abort(403)
            g.current_user = user
            return fn(*args, **kwargs)
        return wrap
    return deco


_db_initialized = False


@app.before_request
def _security_and_csrf():
    global _db_initialized
    if not _db_initialized:
        db.create_all()
        # Pequeña migración runtime: asegurar columna de super admin
        try:
            conn = db.engine.raw_connection(); cur = conn.cursor()
            cur.execute("PRAGMA table_info('users')"); cols = [r[1] for r in cur.fetchall()]
            if 'is_master_admin' not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN is_master_admin BOOLEAN NOT NULL DEFAULT 0")
            conn.commit(); conn.close()
        except Exception:
            pass
        _db_initialized = True
    # Semilla inicial de GES si la tabla está vacía
    try:
        if GESCondition.query.count() == 0:
            for name in PATOLOGIAS_GES:
                db.session.add(GESCondition(name=name, active=True))
            db.session.commit()
    except Exception:
        pass
    # usuario para plantillas
    g.current_user = _current_user()
    # CSRF token por sesión (double submit)
    if "csrf_token" not in session:
        session["csrf_token"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if request.method in {"POST", "PUT", "DELETE"}:
        if request.endpoint not in {"login"}:  # permitir login sin token previo
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not sent or sent != session.get("csrf_token"):
                abort(400)


@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "script-src 'self' 'unsafe-inline'"
    )
    if request.is_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return resp


@app.context_processor
def _inject_auth_ctx():
    # Garantiza disponibilidad en todas las plantillas
    return {
        "current_user": getattr(g, "current_user", None),
        "csrf_token": session.get("csrf_token"),
        "DEV_SHOW_USER": app.config.get("DEV_SHOW_USER", False),
    }


def _is_valid_email(email: str) -> bool:
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", (email or "").strip(), re.IGNORECASE))


if __name__ == "__main__":
    app.run(debug=True)
