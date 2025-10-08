from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "ssmo.db"

app = Flask(__name__)
app.config.update(
    SECRET_KEY="cambio-esto-en-produccion",
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DATABASE_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

db = SQLAlchemy(app)


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

        patologias = ", ".join(self.patologias_ges_lista()) or "Sin patologías GES registradas"
        return (
            "FORMULARIO GUARDADO\n"
            "===================\n\n"
            f"Fecha de registro: {self.created_at.strftime('%d/%m/%Y %H:%M')}\n\n"
            "DATOS PERSONALES\n"
            f"• Nombre: {self.nombre or 'No especificado'}\n"
            f"• RUT: {self.rut or 'No especificado'}\n"
            f"• Fecha de nacimiento: {self.fecha_nacimiento or 'No especificada'}\n"
            f"• Edad: {self.edad or 'No especificada'}\n"
            f"• Comuna: {self.comuna or 'No especificada'}\n\n"
            "DATOS MÉDICOS\n"
            f"• Especialidad: {self.especialidad or 'No especificada'}\n"
            f"• Tipo de consulta: {self.tipo_consulta or 'No especificado'}\n"
            f"• Hipótesis diagnóstica: {self.hipotesis_diagnostico or 'No especificada'}\n"
            f"• Exámenes realizados: {self.examenes_realizados or 'No especificados'}\n"
            f"• Médico responsable: {self.nombre_medico or 'No especificado'}\n\n"
            "GES\n"
            f"• Caso GES: {self.es_ges or 'No especificado'}\n"
            f"• Patologías declaradas: {patologias}\n"
        )


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
    "Trastorno depresivo mayor",
    "Esquizofrenia",
    "Consumo perjudicial de alcohol y drogas",
    "Trastorno de ansiedad",
    "Trastorno del espectro autista",
]


def _extraer_datos_formulario(form_data) -> Dict[str, Optional[str]]:
    datos = {campo: form_data.get(campo) or "" for campo in FORM_FIELDS}
    patologias = form_data.getlist("patologias_ges")
    datos["patologias_ges"] = ";".join(patologias)
    return datos


def _validar_datos(datos: Dict[str, str]) -> List[str]:
    errores: List[str] = []
    if not datos["nombre"].strip():
        errores.append("El nombre del paciente es obligatorio.")
    if not datos["servicio_salud"].strip():
        errores.append("Debe indicar el servicio de salud.")

    for rut_field in ("rut", "rut_padre", "rut_medico"):
        rut = datos.get(rut_field, "").strip()
        if rut and not _rut_valido(rut):
            errores.append(f"El RUT ingresado en '{rut_field}' no es válido.")
    return errores


def _rut_valido(rut: str) -> bool:
    """Valida RUT chileno considerando dígito verificador."""

    rut = rut.replace(".", "").replace("-", "").upper()
    if not rut[:-1].isdigit() or len(rut) < 8:
        return False
    cuerpo = rut[:-1]
    dv = rut[-1]

    suma = 0
    factor = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = suma % 11
    digito_esperado = "0" if resto == 0 else "K" if resto == 1 else str(11 - resto)
    return dv == digito_esperado


@app.route("/", methods=["GET", "POST"])
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
                patologias=PATOLOGIAS_GES,
                errores=errores,
            )

        registro = MedicalForm(**datos)
        db.session.add(registro)
        db.session.commit()
        flash("Formulario guardado correctamente.", "success")
        return redirect(url_for("ver_formulario", form_id=registro.id))

    valores_iniciales = {campo: "" for campo in FORM_FIELDS}
    valores_iniciales["servicio_salud"] = "Metropolitano Oriente"
    return render_template(
        "form.html",
        campos=valores_iniciales,
        patologias=PATOLOGIAS_GES,
        errores=[],
    )


@app.route("/formularios")
def listar_formularios():
    registros = MedicalForm.query.order_by(MedicalForm.created_at.desc()).all()
    return render_template("entries.html", registros=registros)


@app.route("/formularios/<int:form_id>")
def ver_formulario(form_id: int):
    registro: Optional[MedicalForm] = MedicalForm.query.get_or_404(form_id)
    return render_template("summary.html", registro=registro)


@app.context_processor
def inject_globals():
    return {"patologias_catalogo": PATOLOGIAS_GES}


@app.before_first_request
def inicializar_db():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
