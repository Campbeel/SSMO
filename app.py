from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional
import re

from flask import Flask, flash, redirect, render_template, request, url_for, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "ssmo_sic.db"

app = Flask(__name__)
app.config.update(
    SECRET_KEY="cambio-esto-en-produccion",
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DATABASE_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

db = SQLAlchemy(app)


@dataclass
class Establecimiento(db.Model):
    __tablename__ = 'establecimiento'
    id_est = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String, nullable=False, unique=True)
    servicio_salud = db.Column(db.String)

    # Relaciones inversas para las SICs, diferenciando origen y destino.
    sics_origen = db.relationship('Sic', foreign_keys='Sic.id_est_orig', back_populates='establecimiento_origen')
    sics_destino = db.relationship('Sic', foreign_keys='Sic.id_est_dest', back_populates='establecimiento_destino')

class Paciente(db.Model):
    __tablename__ = 'paciente'
    rut_pac = db.Column(db.String, primary_key=True)
    id_est = db.Column(db.Integer, db.ForeignKey('establecimiento.id_est'), nullable=False)
    nombre = db.Column(db.String, nullable=False)
    historia_clinica = db.Column(db.String)
    sexo = db.Column(db.String)
    fecha_nacimiento = db.Column(db.Date)
    edad = db.Column(db.Integer)
    domicilio = db.Column(db.String)
    comuna = db.Column(db.String)
    telefono1 = db.Column(db.String)
    telefono2 = db.Column(db.String)
    correo1 = db.Column(db.String)
    correo2 = db.Column(db.String)

    # Relación inversa a Sic
    sics = db.relationship('Sic', back_populates='paciente')

class Profesional(db.Model):
    __tablename__ = 'profesional'
    rut_pro = db.Column(db.String, primary_key=True)
    nombre = db.Column(db.String, nullable=False)
    especialidad = db.Column(db.String)

    # Relación inversa a Sic
    sics = db.relationship('Sic', back_populates='profesional')

pro_est_association = db.Table('pro_est',
    db.Column('rut_pro', db.String, db.ForeignKey('profesional.rut_pro'), primary_key=True),
    db.Column('id_est', db.Integer, db.ForeignKey('establecimiento.id_est'), primary_key=True),
    db.Column('estado', db.String)
)

class Sic(db.Model):
    __tablename__ = 'sic'
    id_sic = db.Column(db.Integer, primary_key=True)
    rut_pro = db.Column(db.String, db.ForeignKey('profesional.rut_pro'), nullable=False)
    rut_pac = db.Column(db.String, db.ForeignKey('paciente.rut_pac'), nullable=False)
    id_est_orig = db.Column(db.Integer, db.ForeignKey('establecimiento.id_est'), nullable=False)
    id_est_dest = db.Column(db.Integer, db.ForeignKey('establecimiento.id_est'), nullable=False)
    tipo_consulta = db.Column(db.String)
    especialidad_orig = db.Column(db.String)
    especialidad_dest = db.Column(db.String)
    diagnostico = db.Column(db.Text)
    examenes = db.Column(db.Text)
    ges = db.Column(db.String)
    ges_des = db.Column(db.Text)
    prioridad = db.Column(db.String)
    fecha_creacion = db.Column(db.TIMESTAMP, default=datetime.utcnow, nullable=False)

    # Relaciones
    paciente = db.relationship('Paciente', back_populates='sics')
    profesional = db.relationship('Profesional', back_populates='sics')
    establecimiento_origen = db.relationship('Establecimiento', foreign_keys=[id_est_orig], back_populates='sics_origen')
    establecimiento_destino = db.relationship('Establecimiento', foreign_keys=[id_est_dest], back_populates='sics_destino')

    def resumen_texto(self):
        """Genera un resumen en texto plano de la SIC para copiar."""
        
        paciente = self.paciente
        profesional = self.profesional
        origen = self.establecimiento_origen
        destino = self.establecimiento_destino

        fecha_nac_str = paciente.fecha_nacimiento.strftime('%d/%m/%Y') if paciente.fecha_nacimiento else 'No especificada'
        patologias_str = self.ges_des.replace(';', ', ') if self.ges_des else 'Ninguna'

        return f"""
DATOS DEL PACIENTE
------------------
Nombre: {paciente.nombre}
RUT: {paciente.rut_pac}
Fecha de Nacimiento: {fecha_nac_str}
Domicilio: {paciente.domicilio or 'No especificado'}

DATOS DE LA INTERCONSULTA (SIC)
-------------------------------
Origen: {origen.nombre}
Destino: {destino.nombre}
Especialidad Origen: {self.especialidad_orig or 'No especificada'}
Especialidad Destino: {self.especialidad_dest or 'No especificada'}
Hipótesis Diagnóstica: {self.diagnostico}
Caso GES: {self.ges or 'No'}
Patologías GES: {patologias_str}

PROFESIONAL RESPONSABLE
-----------------------
Nombre: {profesional.nombre}
RUT: {profesional.rut_pro}
        """.strip()


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
    "prioridad",
    "examenes_realizados",
    "nombre_medico",
    "rut_medico",
]

PATOLOGIAS_GES: List[str] = [
    "Trastorno depresivo mayor",
    "Esquizofrenia",
    "Consumo perjudicial de alcohol y drogas",
    "Trastorno de ansiedad",
    "Trastorno del espectro autista",
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
    "Confirmación diagnóstica",
    "Realizar tratamiento",
    "Seguimiento",
    "Otro",
]


def _limpiar_rut(rut: str) -> str:
    """Elimina puntos y guiones del RUT."""
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

def _rut_valido(rut: str) -> bool:
    """Valida RUT chileno considerando dígito verificador."""

    limpio = _limpiar_rut(rut).upper()
    if len(limpio) < 2 or not limpio[:-1].isdigit():
        return False
    cuerpo = limpio[:-1]
    dv = limpio[-1]
    esperado = _digito_verificador(cuerpo)
    if dv == "0" and esperado == "K":
        dv = "K"
    return esperado == dv

def _extraer_datos_formulario(form_data) -> Dict[str, Optional[str]]:
    """Extrae y normaliza los datos directamente del formulario de request."""
    datos = {}
    
    # Campos del Paciente
    datos['rut_pac'] = _normalizar_rut(form_data.get('rut_pac', ''))
    datos['nombre_paciente'] = form_data.get('nombre_paciente', '').strip()
    datos['historia_clinica'] = form_data.get('historia_clinica', '')
    datos['sexo'] = form_data.get('sexo', '')
    datos['fecha_nacimiento'] = form_data.get('fecha_nacimiento', '')
    datos['domicilio'] = form_data.get('domicilio', '')
    datos['comuna'] = form_data.get('comuna', '')
    datos['telefono1'] = form_data.get('telefono1', '')
    datos['telefono2'] = form_data.get('telefono2', '')
    datos['correo1'] = form_data.get('correo1', '')
    datos['correo2'] = form_data.get('correo2', '')

    # Campos del Profesional
    datos['rut_pro'] = _normalizar_rut(form_data.get('rut_pro', ''))
    datos['nombre_medico'] = form_data.get('nombre_medico', '').strip()
    datos['especialidad_orig'] = form_data.get('especialidad_orig', '')

    # Campos de la Interconsulta (SIC)
    datos['id_est_orig'] = form_data.get('id_est_orig', '')
    datos['id_est_dest'] = form_data.get('id_est_dest', '')
    datos['tipo_consulta'] = form_data.get('tipo_consulta', '')
    datos['especialidad_dest'] = form_data.get('especialidad_dest', '')
    datos['diagnostico'] = form_data.get('diagnostico', '')
    datos['examenes'] = form_data.get('examenes', '')
    datos['ges'] = form_data.get('ges', '')
    datos['prioridad'] = form_data.get('prioridad', '')

    # Calcula la edad
    datos['edad'] = _calcular_edad(datos.get('fecha_nacimiento', ''))

    return datos


def _validar_datos(datos: Dict[str, str]) -> List[str]:
    """Valida los datos extraídos del formulario basado en la lógica relacional."""
    errores: List[str] = []

    # Validaciones del Paciente
    if not datos["nombre_paciente"]:
        errores.append("El nombre del paciente es obligatorio.")
    if not datos["rut_pac"]:
        errores.append("El RUT del paciente es obligatorio.")
    if not _rut_valido(datos["rut_pac"]):
        errores.append("El RUT del paciente no es válido.")
    if not datos["fecha_nacimiento"]:
        errores.append("La fecha de nacimiento es obligatoria.")
    if not datos["telefono1"]:
        errores.append("El teléfono 1 del paciente es obligatorio.")
    if not datos["correo1"]:
        errores.append("El correo 1 del paciente es obligatorio.")
    if datos["correo1"] and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", datos["correo1"]):
        errores.append("El correo 1 del paciente no es válido.")

    # Validaciones del Profesional
    if not datos["nombre_medico"]:
        errores.append("El nombre del médico es obligatorio.")
    if not datos["rut_pro"]:
        errores.append("El RUT del médico es obligatorio.")
    if not _rut_valido(datos["rut_pro"]):
        errores.append("El RUT del médico no es válido.")

    # Validaciones de la SIC
    if not datos["id_est_orig"]:
        errores.append("Debe seleccionar el establecimiento de origen.")
    if not datos["id_est_dest"]:
        errores.append("Debe seleccionar el establecimiento de destino.")
    if not datos["diagnostico"]:
        errores.append("La hipótesis diagnóstica es obligatoria.")

    return errores

@app.route("/", methods=["GET", "POST"])
def formulario():
    if request.method == "POST":
        datos = _extraer_datos_formulario(request.form)
        
        if not datos.get('id_est_dest') and datos.get('id_est_orig') != '22':
            datos['id_est_dest'] = '22'

        errores = _validar_datos(datos)

        if errores:
            for error in errores:
                flash(error, "error")
            establecimientos = Establecimiento.query.order_by(Establecimiento.nombre).all()
            return render_template(
                "form.html", 
                campos=datos, 
                errores=errores, 
                establecimientos=establecimientos
            )

        # 1. Buscar o crear Paciente
        paciente = Paciente.query.filter_by(rut_pac=datos['rut_pac']).first()
        if not paciente:
            paciente = Paciente(
                rut_pac=datos['rut_pac'],
                nombre=datos['nombre_paciente'],
                historia_clinica=datos['historia_clinica'],
                sexo=datos.get('sexo'),
                fecha_nacimiento=datetime.strptime(datos['fecha_nacimiento'], '%Y-%m-%d').date() if datos.get('fecha_nacimiento') else None,
                edad=datos.get('edad'),
                domicilio=datos.get('domicilio'),
                comuna=datos.get('comuna'),
                telefono1=datos.get('telefono1'),
                telefono2=datos.get('telefono2'),
                correo1=datos.get('correo1'),
                correo2=datos.get('correo2'),
                # El establecimiento del paciente se asocia al de origen de la SIC
                id_est=datos.get('id_est_orig') 
            )
            db.session.add(paciente)

        # 2. Buscar o crear Profesional
        profesional = Profesional.query.filter_by(rut_pro=datos['rut_pro']).first()
        if not profesional:
            profesional = Profesional(
                rut_pro=datos['rut_pro'],
                nombre=datos['nombre_medico'],
                especialidad=datos['especialidad_orig']
            )
            db.session.add(profesional)

        # 3. Crear el registro SIC asignando los objetos de relación
        nueva_sic = Sic(
            profesional=profesional,
            paciente=paciente,
            id_est_orig=datos.get('id_est_orig'),
            id_est_dest=datos.get('id_est_dest'),
            tipo_consulta=datos.get('tipo_consulta'),
            especialidad_orig=datos.get('especialidad_orig'),
            especialidad_dest=datos.get('especialidad_dest'),
            diagnostico=datos.get('diagnostico'),
            examenes=datos.get('examenes'),
            ges=datos.get('ges'),
            prioridad=datos.get('prioridad')
        )
        db.session.add(nueva_sic)
        db.session.commit()
        db.session.refresh(nueva_sic)

        flash("Formulario SIC guardado correctamente.", "success")
        return redirect(url_for("ver_formulario", form_id=nueva_sic.id_sic))

    campos_vacios = _extraer_datos_formulario({})
    campos_vacios["servicio_salud"] = "Metropolitano Oriente"
    
    establecimientos = Establecimiento.query.order_by(Establecimiento.nombre).all()
    
    return render_template(
        "form.html",
        campos=campos_vacios,
        errores=[],
        establecimientos=establecimientos,
    )


@app.route("/formularios")
def listar_formularios():
    registros = Sic.query.options(
        joinedload(Sic.paciente)
    ).order_by(Sic.fecha_creacion.desc()).all()
    return render_template("entries.html", registros=registros)


@app.route("/formularios/<int:form_id>")
def ver_formulario(form_id: int):
    # Cargar el registro SIC y forzar la carga de las relaciones para evitar errores.
    registro: Optional[Sic] = Sic.query.options(
        joinedload(Sic.paciente),
        joinedload(Sic.profesional),
        joinedload(Sic.establecimiento_origen),
        joinedload(Sic.establecimiento_destino)
    ).get_or_404(form_id)
    return render_template("summary.html", registro=registro)


@app.context_processor
def inject_globals():
    return {
        "patologias_catalogo": PATOLOGIAS_GES,
        "comunas_catalogo": COMUNAS,
        "tipos_consulta_catalogo": TIPOS_CONSULTA,
    }


def _establecimiento_to_dict(e: Establecimiento) -> Dict[str, Optional[str]]:
    return {
        "id_est": e.id_est,
        "nombre": e.nombre,
        "servicio_salud": e.servicio_salud,
    }


def _paciente_to_dict(p: Paciente) -> Dict[str, Optional[str]]:
    return {
        "rut_pac": p.rut_pac,
        "id_est": p.id_est,
        "nombre": p.nombre,
        "historia_clinica": p.historia_clinica,
        "sexo": p.sexo,
        "fecha_nacimiento": p.fecha_nacimiento.isoformat() if p.fecha_nacimiento else None,
        "edad": p.edad,
        "domicilio": p.domicilio,
        "comuna": p.comuna,
        "telefono1": p.telefono1,
        "telefono2": p.telefono2,
        "correo1": p.correo1,
        "correo2": p.correo2,
    }


def _profesional_to_dict(pr: Profesional) -> Dict[str, Optional[str]]:
    return {
        "rut_pro": pr.rut_pro,
        "nombre": pr.nombre,
        "especialidad": pr.especialidad,
    }


def _sic_to_dict(s: Sic) -> Dict[str, Optional[str]]:
    return {
        "id_sic": s.id_sic,
        "rut_pro": s.rut_pro,
        "rut_pac": s.rut_pac,
        "id_est_orig": s.id_est_orig,
        "id_est_dest": s.id_est_dest,
        "tipo_consulta": s.tipo_consulta,
        "especialidad_orig": s.especialidad_orig,
        "especialidad_dest": s.especialidad_dest,
        "diagnostico": s.diagnostico,
        "examenes": s.examenes,
        "ges": s.ges,
        "ges_des": s.ges_des,
        "prioridad": s.prioridad,
        "fecha_creacion": s.fecha_creacion.isoformat() if s.fecha_creacion else None,
        "paciente": _paciente_to_dict(s.paciente) if s.paciente else None,
        "profesional": _profesional_to_dict(s.profesional) if s.profesional else None,
        "establecimiento_origen": _establecimiento_to_dict(s.establecimiento_origen) if s.establecimiento_origen else None,
        "establecimiento_destino": _establecimiento_to_dict(s.establecimiento_destino) if s.establecimiento_destino else None,
    }


# --- API mínima para pruebas con Postman ---
@app.route("/api/establecimientos", methods=["GET"])
def api_establecimientos():
    establecimientos = Establecimiento.query.order_by(Establecimiento.id_est).all()
    return jsonify([_establecimiento_to_dict(e) for e in establecimientos])


@app.route("/api/sics", methods=["GET"])
def api_sics():
    q = (
        Sic.query.options(
            joinedload(Sic.paciente),
            joinedload(Sic.profesional),
            joinedload(Sic.establecimiento_origen),
            joinedload(Sic.establecimiento_destino),
        )
        .order_by(Sic.id_sic.desc())
    )
    sics = q.all()
    return jsonify([_sic_to_dict(s) for s in sics])


@app.route("/api/sics/<int:sic_id>", methods=["GET"])
def api_sic_detalle(sic_id: int):
    s: Optional[Sic] = (
        Sic.query.options(
            joinedload(Sic.paciente),
            joinedload(Sic.profesional),
            joinedload(Sic.establecimiento_origen),
            joinedload(Sic.establecimiento_destino),
        ).get(sic_id)
    )
    if not s:
        abort(404)
    return jsonify(_sic_to_dict(s))
@app.cli.command("seed-db")
def seed_db():
    """Borra y repuebla la base de datos con datos de prueba."""
    db.drop_all()
    db.create_all()

    # 1. Poblar establecimientos para que los IDs coincidan con el formulario (tira errors si no existen)
    # Crear lista unificada? me ha dado paja
    establecimientos_data = [
        (1, "Cesfam Carol Urzúa"), (2, "Cesfam La Faena"), (3, "Cesfam Lo Hermida"),
        (4, "Cesfam San Luis"), (5, "Cesfam Cardenal Silva Henríquez"), (6, "Cesfam Padre Whelan"),
        (7, "Cesfam Las Torres"), (8, "Cesfam Félix de Amesti"), (9, "Cesfam Santa Julia"),
        (10, "Cesfam Alberto Hurtado"), (11, "Cesfam Rosita Renard"), (12, "Cesfam Salvador Bustos"),
        (13, "Cesfam Ossandón"), (14, "Cesfam Juan Pablo II"), (15, "Cesfam Hernán Alessandri"),
        (16, "Cesfam El Aguilucho"), (17, "Cesfam Dr. Alfonso Leng"), (18, "Cesfam Apoquindo"),
        (19, "Cesfam Aníbal Ariztía"), (20, "Cesfam Vitacura"), (21, "Cesfam Lo Barnechea"),
        (22, "COSAM SSMO")
    ]
    for id_est, nombre in establecimientos_data:
        est = Establecimiento(id_est=id_est, nombre=nombre, servicio_salud="Metropolitano Oriente")
        db.session.add(est)
    
    db.session.commit()

    # 2. Crear datos de prueba válidos (poblar db)
    # Caso 1: Derivación desde un CESFAM a COSAM
    paciente1 = Paciente(
        rut_pac="10.171.923-5",
        nombre="Juan Pérez González",
        historia_clinica="123456",
        sexo="Masculino",
        fecha_nacimiento=date(1990, 5, 15),
        domicilio="Av. Siempre Viva 742",
        comuna="Peñalolén",
        telefono1="987654321",
        correo1="juan.perez@example.com",
        id_est=1 # Cesfam Carol Urzúa
    )
    profesional1 = Profesional(
        rut_pro="20.484.529-8",
        nombre="Dra. Ana Rodríguez",
        especialidad="Psicólogo(a)"
    )
    sic1 = Sic(
        rut_pro=profesional1.rut_pro,
        rut_pac=paciente1.rut_pac,
        id_est_orig=1, # Origen: Cesfam Carol Urzúa
        id_est_dest=22, # Destino: COSAM SSMO
        tipo_consulta="Confirmación diagnóstica",
        especialidad_orig="Psicólogo(a)",
        especialidad_dest="Psiquiatría Adulto",
        diagnostico="Sospecha de Trastorno de Ansiedad Generalizada.",
        ges="No",
        prioridad="Media"
    )

    db.session.add_all([paciente1, profesional1, sic1])
    db.session.commit()

    print("Base de datos poblada con datos de prueba.")


if __name__ == "__main__":
    app.run(debug=True)
