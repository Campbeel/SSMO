from app import app, db, Establecimiento, Paciente, Profesional, Sic
with app.app_context():
    print('Establecimientos:', db.session.query(Establecimiento).count())
    print('Pacientes:', db.session.query(Paciente).count())
    print('Profesionales:', db.session.query(Profesional).count())
    print('SICs:', db.session.query(Sic).count())
