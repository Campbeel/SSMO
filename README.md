# SSMO

Aplicación web para gestionar formularios médicos del Servicio de Salud Metropolitano Oriente (SSMO).

## Características

- Formulario web para ingresar los datos administrativos y clínicos del paciente.
- Validaciones básicas (campos obligatorios y formato de RUT chileno).
- Generación automática de una plantilla resumen para copiar y compartir.
- Persistencia de los registros en una base de datos SQLite.
- Listado de formularios guardados para consultar registros anteriores.

## Requisitos

- Python 3.11+
- Dependencias listadas en `requirements.txt`

## Instalación y ejecución

1. Crear y activar un entorno virtual (opcional pero recomendado):

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # En Windows usar .venv\Scripts\activate
   ```

2. Instalar las dependencias:

   ```bash
   pip install -r requirements.txt
   ```

3. Inicializar la Base de Datos

Antes de ejecutar la aplicación por primera vez, necesitas crear las tablas en la base de datos. Utiliza el comando CLI personalizado que se incluye en el proyecto:
Bash

	```bash
	flask create-db
	```

Este comando eliminará la base de datos existente (si la hay) y creará una nueva con la estructura correcta y datos de prueba. Solo necesitas ejecutarlo una vez o cuando quieras restaurar la base de datos a su estado inicial.

4. Ejecutar la aplicación:

   ```bash
   flask --app app run --debug
   ```

5. Abrir el navegador en `http://127.0.0.1:5000/` para acceder al formulario.

La base de datos `ssmo.db` se crea automáticamente la primera vez que se ejecuta la aplicación.

## Desarrollo

- Los estilos principales se encuentran en `static/styles.css`.
- Las plantillas HTML viven en la carpeta `templates/`.
- El modelo de datos y las rutas están definidas en `app.py`.

Para ejecutar las pruebas manuales, puede completar el formulario y verificar que la plantilla generada contenga los datos ingresados y que el registro se encuentre disponible en el listado de formularios guardados.
