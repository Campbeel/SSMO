from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Callable, Mapping
import os
import re
import base64
import secrets
import enum
import functools
import smtplib
import random
from email.message import EmailMessage
import click
from werkzeug.middleware.proxy_fix import ProxyFix

from flask import Flask, flash, redirect, render_template, request, url_for, jsonify, abort, session, make_response, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, func, case
from argon2 import PasswordHasher
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from io import BytesIO

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "ssmo.db"))
DATABASE_URL = os.environ.get("DATABASE_URL")

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", "cambio-esto-en-produccion"),
    SQLALCHEMY_DATABASE_URI=DATABASE_URL or f"sqlite:///{DATABASE_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
app.config["DEV_SHOW_USER"] = os.environ.get("DEV_SHOW_USER", "0") in {"1", "true", "TRUE", "yes", "on"}
# Flags de despliegue
_force_https_raw = os.environ.get("FORCE_HTTPS", "0")  # por defecto no forzamos HTTPS en entornos LAN
app.config["FORCE_HTTPS"] = (_force_https_raw or "").lower() in {"1", "true", "yes", "on"}
app.config["TRUST_PROXY_HEADERS"] = (os.environ.get("TRUST_PROXY_HEADERS", "1") or "").lower() in {"1", "true", "yes", "on"}
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Strict")
app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
if app.config["FORCE_HTTPS"]:
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PREFERRED_URL_SCHEME"] = "https"
else:
    # En HTTP explícito evitamos marcar la cookie como Secure para no perder la sesión
    app.config.setdefault("SESSION_COOKIE_SECURE", False)
if app.config["TRUST_PROXY_HEADERS"]:
    # Respeta X-Forwarded-* cuando corremos detrás de Nginx/Apache
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

db = SQLAlchemy(app)

APPOINTMENT_DOCTORS = ["Dr. A", "Dr. B", "Dr. C", "Dr. D", "Dr. E"]
APPOINTMENT_PLACES = ["Box 1", "Box 2", "Box 3", "Box 4", "Box 5"]
APPOINTMENT_START_TIME = "08:00"
APPOINTMENT_END_TIME = "19:00"
APPOINTMENT_SLOT_MINUTES = 15


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
    doctor_name = db.Column(db.String(160))
    doctor_rut = db.Column(db.String(20))

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
    doctor = db.Column("professional", db.String(160))
    place = db.Column(db.String(160))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ReturnEvent(db.Model):
    __tablename__ = "return_events"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, ForeignKey("cases.id"), nullable=False, index=True)
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Rol(db.Model):
    __tablename__ = "roles"
    id_rol = db.Column(db.Integer, primary_key=True)
    nombre_rol = db.Column(db.String(80), unique=True, nullable=False)
    usuarios = db.relationship("Usuario", back_populates="rol", lazy="dynamic")


class Usuario(db.Model):
    __tablename__ = "usuarios"
    id_usuario = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    dominio = db.Column(db.String(120), nullable=True, index=True)
    rol_id = db.Column("rol", db.Integer, db.ForeignKey("roles.id_rol"), nullable=False, index=True)
    es_medico = db.Column(db.Boolean, default=False, nullable=False, index=True)

    rol = db.relationship("Rol", back_populates="usuarios")
    fichas_profesional = db.relationship(
        "FichaSIC",
        foreign_keys="FichaSIC.id_profesional",
        back_populates="profesional",
        lazy="dynamic",
    )
    fichas_centro = db.relationship(
        "FichaSIC",
        foreign_keys="FichaSIC.id_usuario_centro",
        back_populates="usuario_centro",
        lazy="dynamic",
    )
    fichas_cosam = db.relationship(
        "FichaSIC",
        foreign_keys="FichaSIC.id_usuario_cosam",
        back_populates="usuario_cosam",
        lazy="dynamic",
    )
    reportes = db.relationship("ReporteLog", back_populates="usuario", lazy="dynamic")


class Paciente(db.Model):
    __tablename__ = "pacientes"
    id_paciente = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(160), nullable=False)
    rut = db.Column(db.String(20), unique=True, index=True)
    fecha_nacimiento = db.Column(db.Date)
    direccion = db.Column(db.String(255))
    contacto = db.Column(db.String(160))

    fichas = db.relationship("FichaSIC", back_populates="paciente", lazy="dynamic")


class FichaSIC(db.Model):
    __tablename__ = "fichas_sic"
    id_ficha = db.Column(db.Integer, primary_key=True)
    id_paciente = db.Column(db.Integer, db.ForeignKey("pacientes.id_paciente"), nullable=False, index=True)
    id_profesional = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=False, index=True)
    id_usuario_centro = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=False, index=True)
    id_usuario_cosam = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=True, index=True)
    estado = db.Column(db.String(40), default="pendiente", nullable=False, index=True)
    prioridad_sugerida = db.Column(db.String(40))
    prioridad_definitiva = db.Column(db.String(40))
    hipotesis_diagnostica = db.Column(db.Text)
    fundamento = db.Column(db.Text)
    examenes = db.Column(db.Text)
    patologias_ges = db.Column(db.Text)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    paciente = db.relationship("Paciente", back_populates="fichas")
    profesional = db.relationship("Usuario", foreign_keys=[id_profesional], back_populates="fichas_profesional")
    usuario_centro = db.relationship("Usuario", foreign_keys=[id_usuario_centro], back_populates="fichas_centro")
    usuario_cosam = db.relationship("Usuario", foreign_keys=[id_usuario_cosam], back_populates="fichas_cosam")
    agenda = db.relationship("Agenda", back_populates="ficha", lazy="dynamic", cascade="all, delete-orphan")
    reportes = db.relationship("ReporteLog", back_populates="ficha", lazy="dynamic", cascade="all, delete-orphan")


class Agenda(db.Model):
    __tablename__ = "agenda"
    id_agenda = db.Column(db.Integer, primary_key=True)
    id_ficha = db.Column(db.Integer, db.ForeignKey("fichas_sic.id_ficha"), nullable=False, index=True)
    fecha = db.Column(db.Date, nullable=False)
    hora = db.Column(db.Time, nullable=False)
    notas = db.Column(db.Text)

    ficha = db.relationship("FichaSIC", back_populates="agenda")


class ReporteLog(db.Model):
    __tablename__ = "reportes_log"
    id_reporte = db.Column(db.Integer, primary_key=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuarios.id_usuario"), nullable=False, index=True)
    id_ficha = db.Column(db.Integer, db.ForeignKey("fichas_sic.id_ficha"), nullable=True, index=True)
    tipo_grafico = db.Column(db.String(80))
    filtros = db.Column(db.Text)
    fecha_generacion = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    usuario = db.relationship("Usuario", back_populates="reportes")
    ficha = db.relationship("FichaSIC", back_populates="reportes")


@functools.lru_cache(maxsize=1)
def _schedule_time_slots() -> List[str]:
    slots: List[str] = []
    current = datetime.strptime(APPOINTMENT_START_TIME, "%H:%M")
    end = datetime.strptime(APPOINTMENT_END_TIME, "%H:%M")
    while current <= end:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=APPOINTMENT_SLOT_MINUTES)
    return slots


def _default_schedule_form_values(appointment: Optional[Appointment] = None) -> Dict[str, str]:
    if appointment:
        when = appointment.scheduled_at
        return {
            "date": when.strftime("%Y-%m-%d"),
            "time": when.strftime("%H:%M"),
            "place": appointment.place or "",
            "doctor": appointment.doctor or "",
            "notes": appointment.notes or "",
        }
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return {"date": today, "time": "", "place": "", "doctor": "", "notes": ""}


def _render_schedule_form(caso: Case, form: MedicalForm, appointment: Optional[Appointment],
                          form_values: Dict[str, str]) -> Any:
    return render_template(
        "cosam_schedule.html",
        caso=caso,
        form=form,
        appointment=appointment,
        form_values=form_values,
        time_slots=_schedule_time_slots(),
        doctor_choices=APPOINTMENT_DOCTORS,
        place_choices=APPOINTMENT_PLACES,
    )


def _validate_schedule_slot(when: datetime, doctor: str, place: str,
                            ignore_case_id: Optional[int] = None) -> Optional[str]:
    base = Appointment.query.filter(Appointment.scheduled_at == when)
    if ignore_case_id:
        base = base.filter(Appointment.case_id != ignore_case_id)
    doctor_conflict = base.filter(Appointment.doctor == doctor).first()
    if doctor_conflict:
        return f"{doctor} ya tiene una hora asignada en ese bloque."
    place_conflict = base.filter(Appointment.place == place).first()
    if place_conflict:
        return f"El {place} ya está ocupado en ese bloque horario."
    return None


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
                    # En vez de 403 puro, lo mandamos a la home de su rol para evitar bucles y confusiones
                    return redirect(_role_default_target(getattr(user, "role", "")))
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
        if 'doctor_name' not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN doctor_name VARCHAR(160)")
        if 'doctor_rut' not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN doctor_rut VARCHAR(20)")
        conn.commit(); conn.close()
    except Exception:
        pass
    username = input("Usuario: ").strip()
    role = (input("Rol [admin|cosam|centro]: ").strip() or "centro").lower()
    is_master_raw = (input("¿Admin maestro? [s/N]: ").strip() or "n").lower()
    is_master = is_master_raw in {"s", "si", "sí", "y", "yes", "1", "true"}
    pw = getpass.getpass("Contraseña: ")
    doctor_name = None
    doctor_rut = None
    if role in {"centro", "cosam"}:
        wants_doctor = (input("¿Guardar datos de médico por defecto? [s/N]: ").strip() or "n").lower()
        if wants_doctor in {"s", "si", "sí", "y", "yes", "1", "true"}:
            doctor_name = input("Nombre médico: ").strip()
            doctor_rut = input("RUT médico: ").strip()
            if not doctor_name or not doctor_rut:
                print("Debe ingresar nombre y RUT del médico.")
                return
            if not _rut_valido(doctor_rut):
                print("RUT del médico inválido.")
                return
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
    if doctor_name:
        u.doctor_name = doctor_name
    if doctor_rut:
        u.doctor_rut = doctor_rut
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
    domain = _domain(current.username)
    allowed_roles = ["centro", "cosam", "admin"] if is_master else ["centro", "cosam"]
    domain_suffix = f"@{domain}" if (domain and not is_master) else ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        role = (request.form.get("role") or "centro").strip().lower()
        password = request.form.get("password") or ""
        doctor_enabled = request.form.get("doctor_info") == "on"
        doctor_name = (request.form.get("doctor_name") or "").strip()
        doctor_rut = (request.form.get("doctor_rut") or "").strip()
        doctor_enabled = doctor_enabled and role in {"centro", "cosam"}
        if not is_master and domain and "@" not in username:
            username = f"{username}@{domain}"
        if role not in allowed_roles:
            flash("No tiene permiso para asignar ese rol", "error")
        elif not _is_valid_email(username) or len(password) < 8:
            flash("Datos inválidos (correo o contraseña)", "error")
        elif doctor_enabled and (not doctor_name or not doctor_rut):
            flash("Debe ingresar el nombre y RUT del médico.", "error")
        elif doctor_enabled and not _rut_valido(doctor_rut):
            flash("El RUT del médico no es válido.", "error")
        elif not is_master and _domain(username) != domain:
            flash("Solo puede crear usuarios de su propio dominio.", "error")
        elif User.query.filter_by(username=username).first():
            flash("El usuario ya existe", "error")
        else:
            u = User(username=username, role=role)
            u.set_password(password)
            if doctor_enabled:
                u.doctor_name = doctor_name
                u.doctor_rut = doctor_rut
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
        query = User.query
        if domain:
            pattern = f"%@{domain}".lower()
            query = query.filter(func.lower(User.username).like(pattern))
        else:
            query = query.filter(~User.username.contains("@"))
        users = query.order_by(User.created_at.desc()).all()
    return render_template(
        "admin_users.html",
        users=users,
        is_master=is_master,
        allowed_roles=allowed_roles,
        domain_suffix=domain_suffix,
        doctor_roles=["centro", "cosam"],
    )


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
        doctor_enabled = request.form.get("doctor_info") == "on"
        doctor_name = (request.form.get("doctor_name") or "").strip()
        doctor_rut = (request.form.get("doctor_rut") or "").strip()
        if role not in {"centro", "cosam"}:
            doctor_enabled = False
        if not _is_valid_email(email) or role not in {"admin", "cosam", "centro"}:
            flash("Datos inválidos", "error")
        else:
            if not is_master and _domain(email) != _domain(current.username):
                flash("Solo puede actualizar usuarios de su propio dominio.", "error")
                return render_template("admin_user_edit.html", user=u, is_master=is_master, doctor_roles=["centro", "cosam"])
            if doctor_enabled and (not doctor_name or not doctor_rut):
                flash("Debe ingresar el nombre y RUT del médico.", "error")
                return render_template("admin_user_edit.html", user=u, is_master=is_master, doctor_roles=["centro", "cosam"])
            if doctor_enabled and not _rut_valido(doctor_rut):
                flash("El RUT del médico no es válido.", "error")
                return render_template("admin_user_edit.html", user=u, is_master=is_master, doctor_roles=["centro", "cosam"])
            if email != u.username and User.query.filter_by(username=email).first():
                flash("Ya existe un usuario con ese correo", "error")
            else:
                u.username = email
                u.role = role
                u.is_active = active
                if doctor_enabled:
                    u.doctor_name = doctor_name
                    u.doctor_rut = doctor_rut
                else:
                    u.doctor_name = None
                    u.doctor_rut = None
                if is_master:
                    u.is_master_admin = True if (request.form.get("is_master_admin") == "on") else False
                if newpass:
                    if len(newpass) < 8:
                        flash("La contraseña debe tener al menos 8 caracteres", "error")
                        return render_template("admin_user_edit.html", user=u, is_master=is_master, doctor_roles=["centro", "cosam"])
                    u.set_password(newpass)
                db.session.commit()
                flash("Usuario actualizado", "success")
                return redirect(url_for("admin_users"))
    return render_template("admin_user_edit.html", user=u, is_master=is_master, doctor_roles=["centro", "cosam"])


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
@login_required([UserRole.cosam])
def admin_ges():
    # Solo administradores COSAM
    user = getattr(g, "current_user", None)
    if not user or not getattr(user, "is_master_admin", False):
        abort(403)
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


@app.cli.command("reset-password")
@click.argument("username")
@click.option("--password", "-p", help="Contraseña nueva; si se omite se solicitará en la terminal.")
def reset_password_cli(username: str, password: Optional[str]):
    """Actualiza la contraseña de un usuario existente."""
    import getpass

    db.create_all()
    u = User.query.filter_by(username=username).first()
    if not u:
        print("Usuario no encontrado")
        return
    new_password = password or getpass.getpass("Nueva contraseña: ")
    if len(new_password) < 8:
        print("La contraseña debe tener al menos 8 caracteres.")
        return
    u.set_password(new_password)
    db.session.commit()
    print(f"Contraseña actualizada para {u.username}")


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


@app.cli.command("seed-demo-data")
@click.option("--password", default="Cambio123!", show_default=True, help="Contraseña asignada a los usuarios generados.")
def seed_demo_data(password: str):
    """Genera usuarios y fichas de ejemplo para pruebas."""
    db.create_all()
    cosam_accounts = []
    admin_accounts = []
    center_accounts = []

    def _format_rut(num: int) -> str:
        cuerpo = f"{num:08d}"
        dv = _digito_verificador(cuerpo)
        cuerpo_fmt = f"{int(cuerpo):,}".replace(",", ".")
        return f"{cuerpo_fmt}-{dv}"

    def ensure_user(username: str, role: str, *, is_master: bool = False,
                    doctor_name: Optional[str] = None, doctor_rut: Optional[str] = None) -> User:
        user = User.query.filter_by(username=username).first()
        if user:
            return user
        user = User(username=username, role=role, is_master_admin=is_master)
        if doctor_name and doctor_rut:
            user.doctor_name = doctor_name
            user.doctor_rut = doctor_rut
        user.set_password(password)
        db.session.add(user)
        return user

    cosam_names = ["La Reina", "Ñuñoa", "Peñalolén", "Macul", "Providencia"]
    for idx, name in enumerate(cosam_names, start=1):
        username = f"cosam{idx}@cosam.cl"
        user = ensure_user(username, UserRole.cosam.value)
        cosam_accounts.append(user)

    admin_domains = [
        "cordillera.salud.cl",
        "poniente.salud.cl",
        "sur.salud.cl",
        "norte.salud.cl",
        "costero.salud.cl",
    ]
    for idx, domain in enumerate(admin_domains, start=1):
        admin_user = ensure_user(f"admin{idx}@{domain}", UserRole.admin.value, is_master=False)
        admin_accounts.append((admin_user, domain))

    rut_seed = 8000000
    for domain_idx, (admin_user, domain) in enumerate(admin_accounts, start=1):
        for n in range(1, 4):
            rut_seed += 7
            username = f"centro{domain_idx}{n}@{domain}"
            doctor_name = f"Dr. Centro {domain_idx}-{n}"
            doctor_rut = _format_rut(rut_seed)
            user = ensure_user(
                username,
                UserRole.centro.value,
                doctor_name=doctor_name,
                doctor_rut=doctor_rut,
            )
            center_accounts.append(user)

    db.session.commit()

    random.seed(42)
    comunas = ["Las Condes", "Peñalolén", "Ñuñoa", "Providencia", "La Reina", "Macul"]
    patologias = ["Epilepsia", "Esquizofrenia", "Trastorno bipolar", "VIH", "Cáncer"]
    motivos = [
        "Paciente presenta crisis recurrentes.",
        "Seguimiento de controles anuales.",
        "Derivación por evaluación interdisciplinaria.",
        "Necesita evaluación complementaria.",
    ]
    establecimientos = ["CESFAM Cordillera", "CESFAM Oriente", "CESFAM Poniente"]

    created_forms = 0
    for idx, center_user in enumerate(center_accounts, start=1):
        for extra in range(2):
            rut_seed += 5
            paciente_nombre = f"Paciente {idx}-{extra+1}"
            rut_paciente = _format_rut(rut_seed)
            sexo = random.choice(["Masculino", "Femenino"])
            nacimiento = datetime(2005 - (idx % 5), random.randint(1, 12), random.randint(1, 28))
            edad = str(datetime.utcnow().year - nacimiento.year)
            form = MedicalForm(
                servicio_salud="Metropolitano Oriente",
                establecimiento=random.choice(establecimientos),
                especialidad=random.choice(["Psiquiatría", "Neurología", "Pediatría"]),
                unidad="Unidad de Salud Mental",
                nombre=paciente_nombre,
                historia_clinica=f"HC-{idx:03d}{extra}",
                rut=rut_paciente,
                sexo=sexo,
                fecha_nacimiento=nacimiento.strftime("%Y-%m-%d"),
                edad=edad,
                domicilio=f"Calle {idx} #{100 + extra}",
                comuna=random.choice(comunas),
                telefono1=f"+5699{random.randint(1000000,9999999)}",
                telefono2=f"+5698{random.randint(1000000,9999999)}",
                correo1=f"{paciente_nombre.replace(' ', '').lower()}@mail.com",
                correo2="",
                establecimiento_derivacion=random.choice(establecimientos),
                grupo_poblacional=random.choice(["Niño", "Adolescente", "Adulto"]),
                tipo_consulta=random.choice(["Primera vez", "Control", "Urgencia"]),
                tiene_terapias=random.choice(["si", "no"]),
                terapias_otro="",
                hipotesis_diagnostico=random.choice(motivos),
                es_ges=random.choice(["si", "no"]),
                fundamento_diagnostico="Antecedentes clínicos compatibles con el cuadro.",
                examenes_realizados="EEG, laboratorio general.",
                nombre_medico=center_user.doctor_name or f"Dr. {center_user.username.split('@')[0]}",
                rut_medico=center_user.doctor_rut or _format_rut(rut_seed + 1),
                patologias_ges="; ".join(random.sample(patologias, 2)),
            )
            db.session.add(form)
            db.session.flush()
            case = Case(
                form_id=form.id,
                status=random.choice(["enviado", "aceptado", "devuelto"]),
                prioridad=random.choice(["bajo", "medio", "alto"]),
                sender_center_user_id=center_user.id,
            )
            db.session.add(case)
            created_forms += 1

    db.session.commit()

    print(f"Usuarios COSAM generados: {len(cosam_accounts)}")
    print(f"Usuarios admin generados: {len(admin_accounts)}")
    print(f"Usuarios centro generados: {len(center_accounts)}")
    print(f"Formularios creados: {created_forms}")
    print(f"Contraseña utilizada: {password}")


# -------------------- Bandejas COSAM / Centro --------------------

@app.route("/cosam/inbox")
@login_required([UserRole.cosam])
def cosam_inbox():
    porder = case((Case.prioridad == "alto", 0), (Case.prioridad == "medio", 1), else_=2)
    items = (
        Case.query.filter(Case.status == "enviado")
        .order_by(porder, Case.created_at.desc())
        .all()
    )
    form_map = {
        f.id: f
        for f in MedicalForm.query.filter(MedicalForm.id.in_([c.form_id for c in items])).all()
    }
    pares = [(c, form_map.get(c.form_id)) for c in items if form_map.get(c.form_id)]
    high_count = sum(1 for c, _f in pares if (c.prioridad or "").lower() == "alto")
    return render_template("cosam_inbox.html", casos=pares, high_count=high_count)


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
        doctor = (request.form.get("doctor") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        form_values = {
            "date": date_str,
            "time": time_str,
            "place": place,
            "doctor": doctor,
            "notes": notes,
        }
        if doctor not in APPOINTMENT_DOCTORS:
            flash("Seleccione un médico válido.", "error")
            return _render_schedule_form(caso, form, ap, form_values)
        if place not in APPOINTMENT_PLACES:
            flash("Seleccione un box válido.", "error")
            return _render_schedule_form(caso, form, ap, form_values)
        try:
            when = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            flash("Fecha/hora inválida", "error")
            return _render_schedule_form(caso, form, ap, form_values)
        conflict = _validate_schedule_slot(when, doctor, place, ignore_case_id=caso.id)
        if conflict:
            flash(conflict, "error")
            return _render_schedule_form(caso, form, ap, form_values)
        if ap:
            ap.scheduled_at = when
            ap.place = place
            ap.doctor = doctor
            ap.notes = notes
        else:
            ap = Appointment(case_id=caso.id, scheduled_at=when, place=place, doctor=doctor, notes=notes)
            db.session.add(ap)
        db.session.commit()
        destinatario = form.correo1 or form.correo2
        if destinatario:
            _send_email(destinatario, "Reagendamiento de hora",
                        f"Estimado/a {form.nombre}, su hora fue reagendada para {when.strftime('%d/%m/%Y %H:%M')} en {place} con {doctor}.")
        flash("Hora actualizada", "success")
        return redirect(url_for("cosam_pacientes"))
    # GET con ap existente: prellenar
    form_values = _default_schedule_form_values(ap)
    return _render_schedule_form(caso, form, ap, form_values)


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
        doctor = (request.form.get("doctor") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        form_values = {
            "date": date_str,
            "time": time_str,
            "place": place,
            "doctor": doctor,
            "notes": notes,
        }
        if doctor not in APPOINTMENT_DOCTORS:
            flash("Seleccione un médico válido.", "error")
            return _render_schedule_form(caso, form, existing, form_values)
        if place not in APPOINTMENT_PLACES:
            flash("Seleccione un box válido.", "error")
            return _render_schedule_form(caso, form, existing, form_values)
        try:
            when = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            flash("Fecha/hora inválida", "error")
            return _render_schedule_form(caso, form, existing, form_values)
        if existing:
            flash("Ya tenía hora agendada.", "error")
            return redirect(url_for("cosam_pacientes"))
        conflict = _validate_schedule_slot(when, doctor, place)
        if conflict:
            flash(conflict, "error")
            return _render_schedule_form(caso, form, existing, form_values)
        ap = Appointment(case_id=caso.id, scheduled_at=when, place=place, doctor=doctor, notes=notes)
        db.session.add(ap)
        db.session.commit()
        # Email al paciente (si existe correo)
        destinatario = form.correo1 or form.correo2
        if destinatario:
            _send_email(destinatario, "Confirmación de hora",
                        f"Estimado/a {form.nombre}, su hora fue agendada para {when.strftime('%d/%m/%Y %H:%M')} en {place} con {doctor}.\nSaludos.")
        flash("Hora agendada", "success")
        return redirect(url_for("cosam_pacientes"))
    form_values = _default_schedule_form_values(existing)
    return _render_schedule_form(caso, form, existing, form_values)


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


def _build_cosam_report(params: Optional[Mapping[str, Any]] = None):
    """Construye el reporte COSAM (filtros, totales y agregados) a partir de los parámetros dados."""
    from collections import defaultdict, defaultdict as _dd

    params = params or request.args
    fecha_desde_str = (params.get("desde") or "").strip()
    fecha_hasta_str = (params.get("hasta") or "").strip()

    query = db.session.query(MedicalForm, Case).join(Case, Case.form_id == MedicalForm.id)

    try:
        if fecha_desde_str:
            desde = datetime.strptime(fecha_desde_str, "%Y-%m-%d")
            query = query.filter(MedicalForm.created_at >= desde)
    except Exception:
        flash("Fecha 'desde' no v?lida. Usando todos los registros.", "error")
        fecha_desde_str = ""
    try:
        if fecha_hasta_str:
            hasta = datetime.strptime(fecha_hasta_str, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(MedicalForm.created_at < hasta)
    except Exception:
        flash("Fecha 'hasta' no v?lida. Usando todos los registros.", "error")
        fecha_hasta_str = ""

    filas: List[Tuple[MedicalForm, Case]] = query.order_by(MedicalForm.created_at.desc()).all()
    total_casos = len(filas)

    comunas_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "ges": 0, "no_ges": 0})
    patologias_stats: Dict[str, int] = defaultdict(int)

    total_ges = 0
    total_no_ges = 0

    for form, case in filas:
        es_ges_flag = _form_es_ges(form)
        comuna = (form.comuna or "Sin comuna").strip() or "Sin comuna"

        if es_ges_flag:
            total_ges += 1
        else:
            total_no_ges += 1

        comunas_stats[comuna]["total"] += 1
        if es_ges_flag:
            comunas_stats[comuna]["ges"] += 1
        else:
            comunas_stats[comuna]["no_ges"] += 1

        if es_ges_flag:
            for pat in form.patologias_ges_lista():
                patologias_stats[pat] += 1

    comunas_ordenadas = sorted(comunas_stats.items(), key=lambda x: x[0])
    patologias_ordenadas = sorted(patologias_stats.items(), key=lambda x: (-x[1], x[0]))

    comunas_labels = [nombre for (nombre, _stats) in comunas_ordenadas]
    comunas_total = [stats["total"] for (_nombre, stats) in comunas_ordenadas]
    comunas_ges = [stats["ges"] for (_nombre, stats) in comunas_ordenadas]
    comunas_no_ges = [stats["no_ges"] for (_nombre, stats) in comunas_ordenadas]
    patologias_labels = [nombre for (nombre, _cnt) in patologias_ordenadas]
    patologias_counts = [cnt for (_nombre, cnt) in patologias_ordenadas]

    temp_generic: Dict[str, Dict[str, int]] = {
        "comuna": _dd(int),
        "sexo": _dd(int),
        "edad_tramo": _dd(int),
        "es_ges": _dd(int),
        "tipo_consulta": _dd(int),
        "patologia_ges": _dd(int),
    }

    for form, case in filas:
        comuna_val = (form.comuna or "Sin comuna").strip() or "Sin comuna"
        sexo_val = (form.sexo or "Sin dato").strip() or "Sin dato"
        edad_val = _age_bucket(form.edad)
        ges_label = "GES" if _form_es_ges(form) else "No GES"
        tipo_val = (form.tipo_consulta or "Sin dato").strip() or "Sin dato"
        tipo_val = _normalize_tipo_consulta(tipo_val)
        pat_list = form.patologias_ges_lista()
        pat_val = pat_list[0] if pat_list else "Sin patolog?a GES"

        temp_generic["comuna"][comuna_val] += 1
        temp_generic["sexo"][sexo_val] += 1
        temp_generic["edad_tramo"][edad_val] += 1
        temp_generic["es_ges"][ges_label] += 1
        temp_generic["tipo_consulta"][tipo_val] += 1
        temp_generic["patologia_ges"][pat_val] += 1

    generic_chart: Dict[str, Dict[str, List[Any]]] = {}
    for key, mapping in temp_generic.items():
        items = sorted(mapping.items(), key=lambda x: (-x[1], x[0]))
        generic_chart[key] = {
            "labels": [name for name, _cnt in items],
            "values": [cnt for _name, cnt in items],
        }

    return {
        "filtros": {
            "desde": fecha_desde_str,
            "hasta": fecha_hasta_str,
        },
        "filas": filas,
        "comunas": comunas_ordenadas,
        "patologias": patologias_ordenadas,
        "totales": {
            "total": total_casos,
            "ges": total_ges,
            "no_ges": total_no_ges,
        },
        "chart": {
            "comunas_labels": comunas_labels,
            "comunas_total": comunas_total,
            "comunas_ges": comunas_ges,
            "comunas_no_ges": comunas_no_ges,
            "patologias_labels": patologias_labels,
            "patologias_counts": patologias_counts,
            "generic": generic_chart,
        },
    }



def _get_saved_reports() -> List[Dict[str, Any]]:
    try:
        saved = session.get("cosam_report_saved", [])
        if isinstance(saved, list):
            return saved
    except Exception:
        pass
    return []


def _set_saved_reports(items: List[Dict[str, Any]]) -> None:
    session["cosam_report_saved"] = items
    session.modified = True


def _prepare_report_section(params: Mapping[str, Any], chart_type: str, metric_keys: List[str]) -> Dict[str, Any]:
    data = _build_cosam_report(params)
    labels, values, dataset_title, datasets = _build_metric_dataset(data["filas"], metric_keys, chart_type)
    detail_table = _build_detail_table(labels, values, datasets, metric_keys, chart_type)
    report_focus = _report_focus(metric_keys)
    return {
        "filtros": data["filtros"],
        "totales": data["totales"],
        "detail_table": detail_table,
        "labels": labels,
        "values": values,
        "datasets": datasets,
        "dataset_title": dataset_title,
        "chart_type": chart_type,
        "report_focus": report_focus,
    }


def _handle_report_post() -> Any:
    action = (request.form.get("action") or "").strip()
    chart_type = (request.form.get("chart_type") or "bar").strip() or "bar"
    if chart_type not in {"bar", "line", "pie"}:
        chart_type = "bar"
    metric_keys = _parse_metric_keys(request.form, chart_type=chart_type)
    params = {
        "desde": (request.form.get("desde") or "").strip(),
        "hasta": (request.form.get("hasta") or "").strip(),
    }

    if action == "add":
        saved = _get_saved_reports()
        saved.append({
            "params": params,
            "chart_type": chart_type,
            "metric_keys": metric_keys,
        })
        _set_saved_reports(saved)
        flash("Se guardó la selección actual para el informe combinado.", "success")
        return redirect(url_for("cosam_reportes"))

    if action == "clear":
        _set_saved_reports([])
        flash("Se limpiaron las selecciones guardadas para el informe.", "success")
        return redirect(url_for("cosam_reportes"))

    if action == "generate":
        saved = _get_saved_reports()
        entries = saved if saved else [{"params": params, "chart_type": chart_type, "metric_keys": metric_keys}]
        sections = [_prepare_report_section(entry["params"], entry["chart_type"], entry["metric_keys"]) for entry in entries]
        # Limpiar después de generar para evitar duplicados
        _set_saved_reports([])
        return _render_cosam_pdf(sections)

    flash("Acción no válida.", "error")
    return redirect(url_for("cosam_reportes"))


@app.route("/cosam/reportes", methods=["GET", "POST"])
@login_required([UserRole.cosam])
def cosam_reportes():
    """
    Vista de reportes dinámicos para usuarios COSAM.
    """
    if request.method == "POST":
        return _handle_report_post()

    chart_type = (request.args.get("chart_type") or "bar").strip() or "bar"
    if chart_type not in {"bar", "line", "pie"}:
        chart_type = "bar"

    metric_keys = _parse_metric_keys(request.args, chart_type=chart_type)

    data = _build_cosam_report()
    labels, values, title, datasets = _build_metric_dataset(data["filas"], metric_keys, chart_type)
    detail_table = _build_detail_table(labels, values, datasets, metric_keys, chart_type)
    report_focus = _report_focus(metric_keys)
    saved_raw = _get_saved_reports()
    saved_reports = []
    for item in saved_raw:
        try:
            saved_reports.append({
                **item,
                "focus": _report_focus(item.get("metric_keys", [])),
            })
        except Exception:
            saved_reports.append(item)

    return render_template(
        "cosam_reports.html",
        filtros=data["filtros"],
        comunas=data["comunas"],
        patologias=data["patologias"],
        totales=data["totales"],
        chart=data["chart"],
        current={"labels": labels, "values": values, "title": title, "datasets": datasets},
        detail_table=detail_table,
        report_focus=report_focus,
        metric_keys=metric_keys,
        metric_options={k: v[0] for k, v in ATTRIBUTE_CONFIG.items()},
        chart_type=chart_type,
        comunas_catalogo=COMUNAS,
        saved_reports=saved_reports,
    )


def _render_cosam_pdf(sections: List[Dict[str, Any]]):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin_left = 42
    bottom_margin = 60

    def draw_cover():
        y = h - 160
        try:
            logo_path = os.path.join(app.root_path, "static", "img", "logo-minsal.png")
            if os.path.exists(logo_path):
                c.drawImage(ImageReader(logo_path), margin_left, y, width=140, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
        center_x = w / 2
        c.setFont("Helvetica-Bold", 24)
        c.drawCentredString(center_x, y - 10, "Reporte COSAM")
        c.setFont("Helvetica", 12)
        c.drawCentredString(center_x, y - 36, f"Generado el: {datetime.utcnow():%d/%m/%Y %H:%M}")
        c.setFont("Helvetica", 11)
        c.drawCentredString(center_x, y - 54, f"Secciones incluidas: {len(sections)}")
        c.showPage()

    def draw_index():
        y = h - 120
        c.setFont("Helvetica-Bold", 18)
        c.drawString(margin_left, y, "Índice")
        y -= 26
        c.setFont("Helvetica", 11)
        for idx, sec in enumerate(sections, start=1):
            title = sec.get("report_focus", "Casos")
            c.drawString(margin_left, y, f"{idx}. {title}")
            y -= 16
            if y < bottom_margin + 20:
                c.showPage()
                y = h - 120
                c.setFont("Helvetica-Bold", 18)
                c.drawString(margin_left, y, "Índice (cont.)")
                y -= 24
                c.setFont("Helvetica", 11)
        c.showPage()

    def draw_section(section: Dict[str, Any], idx: int) -> None:
        y = h - 100

        def ensure_space(current_y: float, needed: float = 40) -> float:
            if current_y - needed < bottom_margin:
                c.showPage()
                return h - 50
            return current_y

        def draw_paragraph(title: str, lines: List[str], indent: int = 12) -> float:
            nonlocal y
            height = 18 + 14 * len(lines)
            y = ensure_space(y, height)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(margin_left, y, title)
            y -= 16
            c.setFont("Helvetica", 10)
            for line in lines:
                c.drawString(margin_left + indent, y, line)
                y -= 14
            return y

        def draw_table(title: str, headers: List[str], rows: List[List[str]], widths: List[int]) -> None:
            nonlocal y
            row_height = 14
            height = 24 + row_height * (len(rows) + 1)
            y = ensure_space(y, height)
            c.setFont("Helvetica-Bold", 13)
            c.drawString(margin_left, y, title)
            y -= 18
            c.setFont("Helvetica-Bold", 10)
            x = margin_left
            header_y = y
            for head, width in zip(headers, widths):
                c.drawString(x, y, head)
                x += width
            y -= row_height
            c.setFont("Helvetica", 10)
            row_start_y = y
            for row in rows:
                x = margin_left
                for id_col, (cell, width) in enumerate(zip(row, widths)):
                    if id_col == 0:
                        c.drawString(x, y + 2, cell)
                    else:
                        c.drawRightString(x + width - 10, y + 2, cell)
                    x += width
                y -= row_height
            # Dibujar cuadrícula ligera
            grid_top = header_y
            grid_bottom = y + row_height
            c.setStrokeColorRGB(0.8, 0.8, 0.8)
            c.setLineWidth(0.4)
            col_positions = [margin_left]
            acc = margin_left
            for width in widths:
                acc += width
                col_positions.append(acc)
            # Verticales
            for pos in col_positions:
                c.line(pos, grid_top, pos, grid_bottom)
            # Horizontales
            total_rows = len(rows) + 1
            for i_line in range(total_rows + 1):
                y_line = grid_top - (i_line * row_height)
                c.line(margin_left, y_line, margin_left + sum(widths), y_line)
            c.setStrokeColorRGB(0, 0, 0)

        try:
            logo_path = os.path.join(app.root_path, "static", "img", "logo-minsal.png")
            if os.path.exists(logo_path):
                c.drawImage(ImageReader(logo_path), margin_left, y - 10, width=120, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

        center_x = w / 2
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(center_x, y, f"Reporte COSAM - {section.get('report_focus', 'Casos')}")
        y -= 26
        c.setFont("Helvetica", 11)
        c.drawCentredString(center_x, y, f"Generado el: {datetime.utcnow():%d/%m/%Y %H:%M}")
        y -= 28

        filtros = section.get("filtros", {})
        draw_paragraph(
            "Filtros aplicados",
            [
                f"Fecha desde: {filtros.get('desde') or 'Todas'}",
                f"Fecha hasta: {filtros.get('hasta') or 'Todas'}",
            ],
        )

        detail = section.get("detail_table") or {}
        if detail.get("rows"):
            mode = detail.get("mode")
            if mode == "grouped":
                raw_headers = [detail.get("row_header", "Categoría")] + detail.get("column_headers", []) + ["Total"]
                headers = []
                legends = []
                for idx_h, head in enumerate(raw_headers):
                    if idx_h == 0 or idx_h == len(raw_headers) - 1:
                        headers.append(head)
                        continue
                    long_label = len(head) > 18 or len(raw_headers) > 8
                    if long_label and "patolog" in head.lower():
                        alias = f"GES {idx_h}"
                    elif long_label:
                        alias = f"Col {idx_h}"
                    else:
                        alias = head
                    headers.append(alias)
                    if alias != head:
                        legends.append((alias, head))
                rows = []
                for row in detail.get("rows", []):
                    values = [row.get("label", "—")] + [str(v) for v in row.get("values", [])] + [str(row.get("total", 0))]
                    rows.append(values)
                rows.append(["Total"] + [str(v) for v in detail.get("column_totals", [])] + [str(detail.get("grand_total", 0))])
                available_width = w - (margin_left * 2)
                first_w = max(120, min(180, int(available_width * 0.28)))
                other_w = max(50, int((available_width - first_w) / max(len(headers) - 1, 1)))
                widths = [first_w] + [other_w] * (len(headers) - 1)
                if len(headers) > 8:
                    c.setFont("Helvetica-Bold", 9)
                draw_table(f"Detalle por {detail.get('row_header', 'categoría')}", headers, rows, widths)
                if legends:
                    legend_lines = [f"{alias}: {full}" for alias, full in legends]
                    draw_paragraph("Leyenda columnas", legend_lines)
            elif mode == "timeline":
                headers = [detail.get("row_header", "Categoría")] + detail.get("columns", []) + ["Total"]
                rows = []
                for row in detail.get("rows", []):
                    values = [row.get("label", "—")] + [str(v) for v in row.get("values", [])] + [str(row.get("total", 0))]
                    rows.append(values)
                rows.append(["Total"] + [str(v) for v in detail.get("column_totals", [])] + [str(detail.get("grand_total", 0))])
                available_width = w - (margin_left * 2)
                first_w = max(120, min(170, int(available_width * 0.25)))
                remaining = max(1, len(headers) - 1)
                other_w = max(50, int((available_width - first_w) / remaining))
                widths = [first_w] + [other_w] * (len(headers) - 1)
                draw_table(f"Detalle por {detail.get('row_header', 'categoría')} en el tiempo", headers, rows, widths)
            else:
                headers = [detail.get("axis_label", "Categoría"), "Casos", "% del total"]
                rows = []
                for row in detail.get("rows", []):
                    rows.append([row.get("label", "—"), str(row.get("value", 0)), f"{row.get('pct', 0):.1f}%"])
                rows.append(["Total", str(detail.get("grand_total", 0)), "100%"])
                draw_table(f"Detalle por {detail.get('axis_label', 'categoría')}", headers, rows, [200, 80, 80])
        else:
            draw_paragraph("Detalle", ["No hay datos para los filtros seleccionados."])

        labels = section.get("labels") or []
        datasets = section.get("datasets") or []
        values = section.get("values") or []
        chart_type = section.get("chart_type") or "bar"
        dataset_title = section.get("dataset_title") or "Casos"

        all_values = [val for dataset in (datasets or []) for val in dataset.get("data", [])]
        if chart_type == "pie":
            all_values = values
        if labels and all_values and max(all_values) > 0:
            chart_width = min(w * 0.78, 500)
            chart_height = min(h * 0.45, 320)
            # ¿Cabe en la misma página?
            y_chart = y - 30
            needed = chart_height + 60
            if y_chart - needed < bottom_margin:
                c.showPage()
                y_chart = h - 70
            center_x_chart = w / 2
            c.setFont("Helvetica-Bold", 14)
            c.drawCentredString(center_x_chart, y_chart, f"Gráfico : {dataset_title}")
            y_chart -= 28

            left = (w - chart_width) / 2
            bottom = max(bottom_margin + 20, y_chart - chart_height - 40)

            base_colors = [
                (0.145, 0.388, 0.921),
                (0.086, 0.639, 0.290),
                (0.976, 0.451, 0.086),
                (0.863, 0.149, 0.149),
                (0.486, 0.227, 0.933),
                (0.051, 0.580, 0.533),
                (0.918, 0.702, 0.047),
                (0.925, 0.286, 0.600),
                (0.033, 0.569, 0.698),
                (0.294, 0.333, 0.388),
            ]

            def pick_color(idx: int) -> Tuple[float, float, float]:
                return base_colors[idx % len(base_colors)]

            legend_entries: List[Tuple[Tuple[float, float, float], str]] = []

            if chart_type == "pie":
                legend_space = 170
                available_width = w - (margin_left * 2) - legend_space
                chart_width = min(chart_width, available_width)
                left = margin_left + max(0, (available_width - chart_width) / 2)
                total_val = sum(values) or 1
                radius = min(chart_width, chart_height) * 0.45
                cx = left + chart_width / 2
                cy = bottom + chart_height / 2
                start_angle = 0.0
                for idx_p, (label, val) in enumerate(zip(labels, values)):
                    extent = 360.0 * (val / total_val)
                    r, g, b = pick_color(idx_p)
                    c.setFillColorRGB(r, g, b)
                    c.wedge(
                        cx - radius,
                        cy - radius,
                        cx + radius,
                        cy + radius,
                        start_angle,
                        extent,
                        stroke=0,
                        fill=1,
                    )
                    legend_entries.append(((r, g, b), f"{label}: {val}"))
                    start_angle += extent
            elif chart_type == "line":
                datasets_to_draw = datasets or [{"label": dataset_title, "data": values}]
                max_val = max(all_values) or 1
                count = len(labels)
                c.setStrokeColorRGB(0, 0, 0)
                c.line(left, bottom, left, bottom + chart_height)
                c.line(left, bottom, left + chart_width, bottom)
                step = chart_width / max(count - 1, 1)
                c.setFont("Helvetica", 8)
                for idx_label, label in enumerate(labels):
                    c.drawString(left + step * idx_label - 10, bottom - 12, str(label))
                for idx_ds, dataset in enumerate(datasets_to_draw):
                    r, g, b = pick_color(idx_ds)
                    legend_entries.append(((r, g, b), dataset.get("label") or f"Serie {idx_ds+1}"))
                    points = []
                    for idx_label in range(len(labels)):
                        val = dataset.get("data", [])
                        value = val[idx_label] if idx_label < len(val) else 0
                        x_point = left + step * idx_label
                        y_point = bottom + (value / max_val) * chart_height
                        points.append((x_point, y_point, value))
                    c.setStrokeColorRGB(r, g, b)
                    for i_line in range(1, len(points)):
                        c.line(points[i_line - 1][0], points[i_line - 1][1], points[i_line][0], points[i_line][1])
                    for (x_point, y_point, value) in points:
                        c.setFillColorRGB(r, g, b)
                        c.circle(x_point, y_point, 2.2, fill=1, stroke=0)
                        c.setFillColorRGB(0, 0, 0)
                        c.drawString(x_point + 2, y_point + 2, str(int(value)))
            else:
                datasets_to_draw = datasets or [{"label": dataset_title, "data": values}]
                max_val = max(all_values) or 1
                label_count = len(labels)
                series_count = max(1, len(datasets_to_draw))
                group_spacing = 6
                available_width = chart_width - group_spacing * (label_count + 1)
                group_width = available_width / max(label_count, 1)
                inner_spacing = 2
                bar_width = max(4, (group_width - inner_spacing * (series_count - 1)) / max(series_count, 1))
                c.setStrokeColorRGB(0, 0, 0)
                c.line(left, bottom, left, bottom + chart_height)
                c.line(left, bottom, left + chart_width, bottom)
                c.setFont("Helvetica", 8)
                for idx_label, label in enumerate(labels):
                    group_x = left + group_spacing + idx_label * (group_width + group_spacing)
                    c.drawString(group_x, bottom - 12, str(label)[:18])
                    for idx_ds, dataset in enumerate(datasets_to_draw):
                        vals = dataset.get("data", [])
                        value = vals[idx_label] if idx_label < len(vals) else 0
                        height_bar = (value / max_val) * chart_height if max_val else 0
                        x = group_x + idx_ds * (bar_width + inner_spacing)
                        r, g, b = pick_color(idx_ds)
                        c.setFillColorRGB(r, g, b)
                        c.rect(x, bottom, bar_width, height_bar, fill=1, stroke=0)
                        c.setFillColorRGB(0, 0, 0)
                        c.drawString(x, bottom + height_bar + 2, str(int(value)))
                legend_entries = [
                    (pick_color(i), datasets_to_draw[i].get("label") or f"Serie {i+1}")
                    for i in range(series_count)
                ]

            if legend_entries:
                legend_width = 140
                desired_x = left + chart_width + 20
                legend_x = min(w - margin_left - legend_width, desired_x)
                legend_x = max(margin_left, legend_x)
                legend_y = bottom + chart_height - 6
                c.setFont("Helvetica", 9)
                for color, text in legend_entries:
                    if legend_y < bottom + 30:
                        legend_y = bottom + chart_height - 6
                        legend_x = max(margin_left, legend_x + legend_width + 10)
                    r, g, b = color
                    c.setFillColorRGB(r, g, b)
                    c.rect(legend_x, legend_y - 8, 10, 10, fill=1, stroke=0)
                    c.setFillColorRGB(0, 0, 0)
                    short_text = text if len(text) < 32 else text[:31] + "…"
                    c.drawString(legend_x + 14, legend_y - 5, short_text)
                    legend_y -= 14

    # Portada e índice
    draw_cover()
    draw_index()

    for idx_sec, section in enumerate(sections):
        if idx_sec > 0:
            c.showPage()
        draw_section(section, idx_sec)

    c.save()
    buf.seek(0)

    from flask import send_file

    filename = "reporte_cosam.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


@app.route("/cosam/reportes/pdf", methods=["GET"])
@login_required([UserRole.cosam])
def cosam_reportes_pdf():
    params = {
        "desde": (request.args.get("desde") or "").strip(),
        "hasta": (request.args.get("hasta") or "").strip(),
    }
    chart_type = (request.args.get("chart_type") or "bar").strip() or "bar"
    if chart_type not in {"bar", "line", "pie"}:
        chart_type = "bar"
    metric_keys = _parse_metric_keys(request.args, chart_type=chart_type)
    section = _prepare_report_section(params, chart_type, metric_keys)
    return _render_cosam_pdf([section])

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
    patologias = [
        (item or "").strip()
        for item in form_data.getlist("patologias_ges")
        if (item or "").strip()
    ]
    datos["patologias_ges"] = ";".join(patologias[:3])
    datos["edad"] = _calcular_edad(datos.get("fecha_nacimiento", ""))
    tipo_consulta = _normalize_tipo_consulta(form_data.get("tipo_consulta") or "")
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
    # Defaults para COSAM
    try:
        user_role = getattr(getattr(g, "current_user", None), "role", None)
        if user_role == UserRole.cosam.value:
            valores_iniciales["admision_comuna"] = "Providencia"
            valores_iniciales["establecimiento"] = "COSAM"
            valores_iniciales["derivacion_comuna"] = "Providencia"
            valores_iniciales["establecimiento_derivacion"] = "COSAM"
    except Exception:
        pass
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
                    "derivacion_comuna": src.comuna or "",
                })
                valores_iniciales["admision_comuna"] = "Providencia"
                valores_iniciales["establecimiento"] = "COSAM"
    except Exception:
        pass
    try:
        user = getattr(g, "current_user", None)
        if user:
            if not valores_iniciales.get("nombre_medico"):
                valores_iniciales["nombre_medico"] = getattr(user, "doctor_name", "") or ""
            if not valores_iniciales.get("rut_medico"):
                valores_iniciales["rut_medico"] = getattr(user, "doctor_rut", "") or ""
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
        "COSAM",
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
        "COSAM",
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
    # El inicio "/" solo es válido para roles que pueden ver el formulario principal
    if next_path == "/":
        return role in {UserRole.centro.value, UserRole.cosam.value}
    allowed = {
        UserRole.admin.value: ("/admin/", "/formularios", "/api/"),
        UserRole.cosam.value: ("/cosam/", "/formularios", "/api/"),
        UserRole.centro.value: ("/centro/", "/", "/formularios", "/api/"),
    }.get(role, ("/",))
    return any(next_path.startswith(p) for p in allowed)


def _cookie_kwargs():
    secure_flag = bool(app.config.get("SESSION_COOKIE_SECURE")) or request.is_secure
    samesite = app.config.get("SESSION_COOKIE_SAMESITE") or "Strict"
    return {"httponly": True, "secure": secure_flag, "samesite": samesite, "path": "/"}


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


@app.before_request
def _enforce_https():
    """En producción forzamos redirección a HTTPS para activar cabeceras seguras."""
    if app.config.get("FORCE_HTTPS") and not request.is_secure:
        target = request.url.replace("http://", "https://", 1)
        return redirect(target, code=301)


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
            if 'doctor_name' not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN doctor_name VARCHAR(160)")
            if 'doctor_rut' not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN doctor_rut VARCHAR(20)")
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


@app.errorhandler(403)
def _handle_forbidden(err):
    # En vistas HTML redirigimos al inicio correspondiente; APIs mantienen 403
    wants_json = request.path.startswith("/api/") or "application/json" in request.accept_mimetypes
    if wants_json:
        return err
    user = getattr(g, "current_user", None) or _current_user()
    if user:
        flash("No tiene permisos para esa vista.", "error")
        return redirect(_role_default_target(getattr(user, "role", "")))
    return redirect(url_for("login", next=request.path))


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


# -------------------- Reportes dinámicos --------------------

def _form_es_ges(form: MedicalForm) -> bool:
    if getattr(form, "patologias_ges", None):
        try:
            if form.patologias_ges_lista():
                return True
        except Exception:
            if (form.patologias_ges or "").strip():
                return True
    valor = (getattr(form, "es_ges", "") or "").strip().lower()
    return valor in {"si", "sí", "s?"}


def _age_bucket(value: Optional[str]) -> str:
    try:
        edad_int = int(value or "")
    except Exception:
        return "Sin dato"
    if edad_int < 15:
        return "< 15"
    if edad_int < 25:
        return "15-24"
    if edad_int < 45:
        return "25-44"
    if edad_int < 65:
        return "45-64"
    return "65+"


def _normalize_tipo_consulta(valor: str) -> str:
    val = (valor or "").strip().lower()
    if val in {"presencial", "prescencial"}:
        return "Presencial"
    if val == "telemedicina":
        return "Telemedicina"
    if val == "otro":
        return "Otro"
    return "Otro"


ATTRIBUTE_CONFIG: Dict[str, Tuple[str, Callable[[MedicalForm, Case], str]]] = {
    "comuna": (
        "Comuna",
        lambda f, c: (f.comuna or "Sin comuna").strip() or "Sin comuna",
    ),
    "sexo": (
        "Sexo",
        lambda f, c: (f.sexo or "Sin dato").strip() or "Sin dato",
    ),
    "edad_tramo": (
        "Tramo de edad",
        lambda f, c: _age_bucket(f.edad),
    ),
    "es_ges": (
        "GES / No GES",
        lambda f, c: "GES" if _form_es_ges(f) else "No GES",
    ),
    "tipo_consulta": (
        "Tipo de consulta",
        lambda f, c: _normalize_tipo_consulta(f.tipo_consulta or "Sin dato"),
    ),
    "patologia_ges": (
        "Patología GES",
        lambda f, c: (f.patologias_ges_lista() or ["Sin patología GES"])[0],
    ),
}


def _metric_label(key: Optional[str]) -> str:
    if not key:
        return "Categoría"
    return ATTRIBUTE_CONFIG.get(key, (key,))[0]


def _report_focus(metric_keys: List[str]) -> str:
    labels = [_metric_label(k) for k in metric_keys if k]
    if not labels:
        return "Casos"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} vs {labels[1]}"
    return " vs ".join(labels[:3])


def _parse_metric_keys(params: Mapping[str, Any], chart_type: Optional[str] = None) -> List[str]:
    keys: List[str] = []
    for idx, name in enumerate(("metric", "metric2", "metric3")):
        raw = params.get(name)
        val = (raw or "").strip() if isinstance(raw, str) else ""
        if val and val in ATTRIBUTE_CONFIG and val not in keys:
            keys.append(val)
    if not keys:
        keys = ["comuna"]
    if chart_type == "pie":
        return keys[:1]
    if chart_type == "bar":
        return keys[:2]
    if chart_type == "line":
        return keys[:1]
    return keys


def _build_metric_dataset(
    filas: List[Tuple[MedicalForm, Case]],
    metric_keys: List[str],
    chart_type: str,
) -> Tuple[List[str], List[int], str, List[Dict[str, Any]]]:
    from collections import defaultdict

    def _get_conf(key: str) -> Tuple[str, Callable[[MedicalForm, Case], str]]:
        return ATTRIBUTE_CONFIG.get(key, (key, lambda *_: "Sin dato"))

    if chart_type == "line":
        metric_key = metric_keys[0] if metric_keys else "comuna"
        axis_label, extractor = _get_conf(metric_key)
        timeline_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        periods: set[str] = set()
        for form, case in filas:
            try:
                period = (form.created_at or datetime.utcnow()).strftime("%Y-%m")
            except Exception:
                period = "Sin fecha"
            try:
                category = extractor(form, case)
            except Exception:
                category = "Sin dato"
            periods.add(period)
            timeline_counts[category][period] += 1
        labels = sorted(periods)
        datasets = []
        for category, mapping in sorted(timeline_counts.items(), key=lambda item: item[0]):
            datasets.append({
                "label": category,
                "data": [mapping.get(period, 0) for period in labels],
            })
        values = [sum(dataset["data"][idx] for dataset in datasets) for idx in range(len(labels))] if datasets else []
        title = f"Evolución mensual por {axis_label}"
        return labels, values, title, datasets

    if chart_type == "bar" and len(metric_keys) >= 2:
        key_x, key_group = metric_keys[:2]
        axis_x, fn_x = _get_conf(key_x)
        axis_group, fn_group = _get_conf(key_group)
        counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        x_values: set[str] = set()
        groups: set[str] = set()
        for form, case in filas:
            try:
                x_val = fn_x(form, case)
            except Exception:
                x_val = "Sin dato"
            try:
                group_val = fn_group(form, case)
            except Exception:
                group_val = "Sin dato"
            counts[x_val][group_val] += 1
            x_values.add(x_val)
            groups.add(group_val)
        labels = sorted(x_values)
        group_list = sorted(groups)
        datasets = []
        for group in group_list:
            datasets.append({
                "label": group,
                "data": [counts[label].get(group, 0) for label in labels],
            })
        values = [sum(dataset["data"][idx] for dataset in datasets) for idx in range(len(labels))] if datasets else []
        title = f"Casos por {axis_x} y {axis_group}"
        return labels, values, title, datasets

    counts: Dict[str, int] = defaultdict(int)
    axis_names = [ATTRIBUTE_CONFIG[key][0] for key in metric_keys if key in ATTRIBUTE_CONFIG]
    if not axis_names:
        axis_names = [ATTRIBUTE_CONFIG["comuna"][0]]
    for form, case in filas:
        parts: List[str] = []
        for key in metric_keys:
            label, extractor = _get_conf(key)
            try:
                parts.append(extractor(form, case))
            except Exception:
                parts.append("Sin dato")
        if not parts:
            continue
        label = " | ".join(parts)
        counts[label] += 1

    labels = sorted(counts.keys())
    values = [counts[lbl] for lbl in labels]
    datasets = [{"label": "Casos", "data": values}]
    title = "Casos por " + " y ".join(axis_names)
    return labels, values, title, datasets


def _build_detail_table(
    labels: List[str],
    values: List[int],
    datasets: List[Dict[str, Any]],
    metric_keys: List[str],
    chart_type: str,
) -> Dict[str, Any]:
    """
    Construye la estructura de la tabla inferior según el tipo de gráfico
    y las métricas seleccionadas en pantalla.
    """
    def _as_int(val: Any) -> int:
        try:
            return int(val)
        except Exception:
            try:
                return int(float(val or 0))
            except Exception:
                return 0

    if chart_type == "bar" and len(metric_keys) >= 2 and datasets:
        row_header = _metric_label(metric_keys[0])
        column_headers = [ds.get("label") or "Dato" for ds in datasets]
        rows: List[Dict[str, Any]] = []
        for idx, label in enumerate(labels):
            row_values: List[int] = []
            row_total = 0
            for ds in datasets:
                data = ds.get("data") or []
                val = _as_int(data[idx]) if idx < len(data) else 0
                row_values.append(val)
                row_total += val
            rows.append({"label": label, "values": row_values, "total": row_total})
        column_totals = []
        for ds in datasets:
            column_totals.append(sum(_as_int(v) for v in (ds.get("data") or [])))
        grand_total = sum(column_totals)
        return {
            "mode": "grouped",
            "row_header": row_header,
            "column_headers": column_headers,
            "rows": rows,
            "column_totals": column_totals,
            "grand_total": grand_total,
        }

    if chart_type == "line" and datasets and labels:
        row_header = _metric_label(metric_keys[0] if metric_keys else None)
        rows: List[Dict[str, Any]] = []
        for ds in datasets:
            data = [_as_int(v) for v in (ds.get("data") or [])]
            rows.append({
                "label": ds.get("label") or "Dato",
                "values": data,
                "total": sum(data),
            })
        column_totals = [
            sum(row["values"][idx] if idx < len(row["values"]) else 0 for row in rows)
            for idx, _period in enumerate(labels)
        ]
        grand_total = sum(column_totals)
        return {
            "mode": "timeline",
            "row_header": row_header,
            "columns": labels,
            "rows": rows,
            "column_totals": column_totals,
            "grand_total": grand_total,
        }

    axis_label = _metric_label(metric_keys[0] if metric_keys else None)
    total = sum(_as_int(v) for v in values)
    rows = []
    for label, val in zip(labels, values):
        count = _as_int(val)
        pct = round((count / total) * 100, 1) if total else 0.0
        rows.append({"label": label, "value": count, "pct": pct})
    return {
        "mode": "single",
        "axis_label": axis_label,
        "rows": rows,
        "grand_total": total,
    }


if __name__ == "__main__":
    app.run(debug=True)
