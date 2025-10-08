from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from flask_sqlalchemy import SQLAlchemy
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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


def _parrafo(texto: str, *, estilo: ParagraphStyle) -> Paragraph:
    """Normaliza texto para ser usado en el PDF."""

    contenido = (texto or "—").replace("\n", "<br/>")
    return Paragraph(contenido, estilo)


def generar_pdf_formulario(registro: MedicalForm) -> BytesIO:
    """Genera un PDF con los datos del formulario almacenado."""

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    body_style: ParagraphStyle = styles["BodyText"].clone("BodyTextSmall")
    body_style.fontSize = 9
    body_style.leading = 12
    heading_style = styles["Heading2"].clone("Heading2Small")
    heading_style.fontSize = 12
    heading_style.leading = 16

    story = [
        Paragraph("Solicitud de interconsulta o derivación", styles["Title"]),
        Spacer(1, 6),
        Paragraph(
            f"Registro generado el {registro.created_at.strftime('%d/%m/%Y %H:%M')}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]

    def tabla_campos(titulo: str, campos: List[tuple[str, str]]):
        story.append(Paragraph(titulo, heading_style))
        story.append(Spacer(1, 4))
        filas: List[List[Paragraph | str]] = [["Campo", "Valor"]]
        for etiqueta, valor in campos:
            filas.append([etiqueta, _parrafo(valor, estilo=body_style)])
        tabla = Table(filas, colWidths=[60 * mm, 110 * mm])
        tabla.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E4EEF9")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#12385B")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFD")]),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9CB3C9")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#51749C")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(tabla)
        story.append(Spacer(1, 12))

    tabla_campos(
        "Antecedentes del paciente",
        [
            ("Servicio de salud", registro.servicio_salud),
            ("Establecimiento", registro.establecimiento),
            ("Especialidad", registro.especialidad),
            ("Unidad", registro.unidad),
            ("Nombre", registro.nombre),
            ("Historia clínica", registro.historia_clinica),
            ("RUT paciente", registro.rut),
            ("RUT apoderado", registro.rut_padre),
            ("Sexo", registro.sexo),
            ("Fecha de nacimiento", registro.fecha_nacimiento),
            ("Edad", registro.edad),
            ("Domicilio", registro.domicilio),
            ("Comuna", registro.comuna),
            (
                "Teléfonos",
                " / ".join(filter(None, [registro.telefono1, registro.telefono2])) or "—",
            ),
            (
                "Correos electrónicos",
                " / ".join(filter(None, [registro.correo1, registro.correo2])) or "—",
            ),
        ],
    )

    patologias = registro.patologias_ges_lista()
    patologias_texto = ", ".join(patologias) if patologias else "Sin patologías registradas"

    tabla_campos(
        "Información médica",
        [
            ("Establecimiento de derivación", registro.establecimiento_derivacion),
            ("Grupo poblacional", registro.grupo_poblacional),
            ("Tipo de consulta", registro.tipo_consulta),
            ("Terapias específicas", registro.tiene_terapias),
            ("Detalle terapias", registro.terapias_otro),
            ("Hipótesis diagnóstica", registro.hipotesis_diagnostico),
            ("¿Caso GES?", registro.es_ges),
            ("Fundamento diagnóstico", registro.fundamento_diagnostico),
            ("Exámenes realizados", registro.examenes_realizados),
            ("Patologías GES", patologias_texto),
        ],
    )

    tabla_campos(
        "Profesional responsable",
        [
            ("Nombre del médico", registro.nombre_medico),
            ("RUT del médico", registro.rut_medico),
        ],
    )

    story.append(Paragraph("Resumen textual", heading_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(registro.resumen_texto().replace("\n", "<br/>"), body_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


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


@app.route("/formularios/<int:form_id>/pdf")
def descargar_pdf(form_id: int):
    registro: Optional[MedicalForm] = MedicalForm.query.get_or_404(form_id)
    pdf_buffer = generar_pdf_formulario(registro)
    filename = f"formulario-{registro.id}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.context_processor
def inject_globals():
    return {"patologias_catalogo": PATOLOGIAS_GES}


_db_initialized = False


@app.before_request
def inicializar_db():
    global _db_initialized
    if not _db_initialized:
        db.create_all()
        _db_initialized = True


if __name__ == "__main__":
    app.run(debug=True)
