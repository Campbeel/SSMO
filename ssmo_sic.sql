CREATE TABLE IF NOT EXISTS paciente (
    rut_pac TEXT PRIMARY KEY,
    id_est INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    historia_clinica TEXT,
    sexo TEXT,
    fecha_nacimiento DATE,
    edad INTEGER,
    domicilio TEXT,
    comuna TEXT,
    telefono1 TEXT,
    telefono2 TEXT,
    correo1 TEXT,
    correo2 TEXT,

    FOREIGN KEY (id_est) REFERENCES establecimiento(id_est)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS establecimiento (
  id_est INTEGER PRIMARY KEY,
  nombre TEXT NOT NULL UNIQUE,
  servicio_salud TEXT
);

CREATE TABLE IF NOT EXISTS profesional (
    rut_pro TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    especialidad TEXT
);

CREATE TABLE IF NOT EXISTS sic (
    id_sic INTEGER PRIMARY KEY,
    rut_pro TEXT NOT NULL,
    rut_pac TEXT NOT NULL,
    id_est_orig INTEGER NOT NULL,
    id_est_dest INTEGER NOT NULL,
    tipo_consulta TEXT,
    especialidad_orig TEXT,
    especialidad_dest TEXT,
    ges TEXT,
    ges_des TEXT,
    diagnostico TEXT,
    examenes TEXT,
    prioridad TEXT,
    fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (rut_pac) REFERENCES paciente(rut_pac)
        ON UPDATE CASCADE ON DELETE RESTRICT,
        
    FOREIGN KEY (rut_pro) REFERENCES profesional(rut_pro)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    
    FOREIGN KEY (id_est_dest) REFERENCES establecimiento(id_est)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    
    FOREIGN KEY (id_est_orig) REFERENCES establecimiento(id_est)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS pro_est (
    rut_pro TEXT NOT NULL,
    id_est INTEGER NOT NULL,
    estado TEXT,
    PRIMARY KEY (rut_pro, id_est),
    FOREIGN KEY (rut_pro) REFERENCES profesional(rut_pro),
    FOREIGN KEY (id_est) REFERENCES establecimiento(id_est)
);