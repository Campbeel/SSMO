document.addEventListener('DOMContentLoaded', () => {
  const fechaNacimientoInput = document.querySelector('input[name="fecha_nacimiento"]');
  const edadInput = document.querySelector('input[name="edad"]');
  const rutInputs = document.querySelectorAll('input[data-rut]');
  const onlyDigitsInputs = document.querySelectorAll('[data-only-digits]');
  const correo1 = document.querySelector('input[name="correo1"]');
  const correo2 = document.querySelector('input[name="correo2"]');
  const phoneInputs = document.querySelectorAll('[data-phone]');
  // indicadores visuales eliminados para simplificar

  const calcularEdad = (valor) => {
    if (!valor || !edadInput) {
      if (edadInput) {
        edadInput.value = '';
      }
      return;
    }
    const partes = valor.split('-').map(Number);
    if (partes.length !== 3 || partes.some(Number.isNaN)) {
      edadInput.value = '';
      return;
    }
    const [anio, mes, dia] = partes;
    const nacimiento = new Date(Date.UTC(anio, mes - 1, dia));
    if (Number.isNaN(nacimiento.getTime())) {
      edadInput.value = '';
      return;
    }
    const hoy = new Date();
    let edad = hoy.getUTCFullYear() - nacimiento.getUTCFullYear();
    const mesDiferencia = hoy.getUTCMonth() - nacimiento.getUTCMonth();
    const diaDiferencia = hoy.getUTCDate() - nacimiento.getUTCDate();
    if (mesDiferencia < 0 || (mesDiferencia === 0 && diaDiferencia < 0)) {
      edad -= 1;
    }
    edadInput.value = edad >= 0 ? edad : '';
  };

  const limpiarRut = (valor) => valor.replace(/[^0-9Kk]/g, '');

  const digitoVerificador = (cuerpo) => {
    let suma = 0;
    let factor = 2;
    for (let i = cuerpo.length - 1; i >= 0; i -= 1) {
      suma += parseInt(cuerpo[i], 10) * factor;
      factor = factor === 7 ? 2 : factor + 1;
    }
    const resto = suma % 11;
    if (resto === 0) return '0';
    if (resto === 1) return 'K';
    return String(11 - resto);
  };

  const normalizarRut = (valor) => {
    const limpio = limpiarRut(valor).toUpperCase();
    if (limpio.length < 2 || !/^[0-9]+$/.test(limpio.slice(0, -1))) {
      return limpio;
    }
    const cuerpo = limpio.slice(0, -1);
    let dv = limpio.slice(-1);
    const esperado = digitoVerificador(cuerpo);
    if (dv === '0' && esperado === 'K') {
      dv = 'K';
    }
    return cuerpo + dv;
  };

  const formatearRut = (valor) => {
    const normalizado = normalizarRut(valor);
    if (normalizado.length < 2 || !/^[0-9]+$/.test(normalizado.slice(0, -1))) {
      return normalizado;
    }
    const cuerpo = normalizado.slice(0, -1);
    const dv = normalizado.slice(-1);
    const reversed = cuerpo.split('').reverse();
    const partes = [];
    for (let i = 0; i < reversed.length; i += 1) {
      partes.push(reversed[i]);
      if ((i + 1) % 3 === 0 && i + 1 !== reversed.length) {
        partes.push('.');
      }
    }
    return `${partes.reverse().join('')}-${dv}`;
  };

  const rutValido = (valor) => {
    const normalizado = normalizarRut(valor);
    if (normalizado.length < 2 || !/^[0-9]+$/.test(normalizado.slice(0, -1))) {
      return false;
    }
    const cuerpo = normalizado.slice(0, -1);
    let dv = normalizado.slice(-1);
    const esperado = digitoVerificador(cuerpo);
    if (dv === '0' && esperado === 'K') {
      dv = 'K';
    }
    return dv === esperado;
  };

  const actualizarRut = (input) => {
    const valor = input.value.trim();
    if (!valor) {
      input.setCustomValidity('');
      return;
    }
    const formateado = formatearRut(valor);
    input.value = formateado;
    if (!rutValido(formateado)) {
      input.setCustomValidity('El RUT ingresado no es vÃƒÂ¡lido.');
      input.reportValidity();
    } else {
      input.setCustomValidity('');
    }
  };

  rutInputs.forEach((input) => {
    input.addEventListener('input', () => {
      const limpio = limpiarRut(input.value).toUpperCase();
      input.value = limpio;
    });
    input.addEventListener('blur', () => actualizarRut(input));
    input.addEventListener('change', () => actualizarRut(input));
    if (input.value) {
      actualizarRut(input);
    }
  });

  if (fechaNacimientoInput) {
    const actualizarEdad = () => calcularEdad(fechaNacimientoInput.value);
    fechaNacimientoInput.addEventListener('change', actualizarEdad);
    fechaNacimientoInput.addEventListener('blur', actualizarEdad);
    actualizarEdad();
  }

  // Solo dÃƒÂ­gitos (historia clÃƒÂ­nica, etc.)
  const soloDigitos = (input) => {
    const limpio = input.value.replace(/\D+/g, '');
    if (input.value !== limpio) input.value = limpio;
  };
  onlyDigitsInputs.forEach((el) => {
    el.addEventListener('input', () => soloDigitos(el));
    // normaliza valor inicial si viene con otros caracteres
    soloDigitos(el);
  });

  // ValidaciÃƒÂ³n de email bÃƒÂ¡sica con mensaje amigable
  const emailValido = (valor) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/i.test(valor);
  const validarEmailInput = (input, obligatorio = false) => {
    const v = (input?.value || '').trim();
    if (!v) {
      input?.setCustomValidity(obligatorio ? 'Este correo es obligatorio.' : '');
      return;
    }
    if (!emailValido(v)) {
      input?.setCustomValidity('Ingrese un correo electrÃƒÂ³nico vÃƒÂ¡lido.');
    } else {
      input?.setCustomValidity('');
    }
  };
  if (correo1) {
    const handler = () => validarEmailInput(correo1, true);
    correo1.addEventListener('input', handler);
    correo1.addEventListener('blur', handler);
    handler();
  }
  if (correo2) {
    const handler2 = () => validarEmailInput(correo2, false);
    correo2.addEventListener('input', handler2);
    correo2.addEventListener('blur', handler2);
    handler2();
  }

  // TelÃƒÂ©fonos: permitir solo dÃƒÂ­gitos y un '+' inicial opcional
  const normalizarTelefono = (valor) => {
    if (!valor) return '';
    // eliminar todo excepto dÃƒÂ­gitos y "+"
    let limpio = valor.replace(/[^\d+]/g, '');
    // si hay mÃƒÂºltiples '+', dejar sÃƒÂ³lo uno al inicio
    const tieneMas = (limpio.match(/\+/g) || []).length > 1;
    limpio = limpio.replace(/\+/g, '');
    // reconstruir con '+' inicial si la cadena original empezaba con '+' o si habÃƒÂ­a al menos uno
    const empiezaMas = valor.trim().startsWith('+') || tieneMas;
    return (empiezaMas ? '+' : '') + limpio;
  };
  const validarTelefono = (input, obligatorio = false) => {
    const v = (input?.value || '').trim();
    if (!v) {
      input?.setCustomValidity(obligatorio ? 'Este telÃƒÂ©fono es obligatorio.' : '');
      return;
    }
    const normal = normalizarTelefono(v);
    if (normal !== v) input.value = normal;
    const ok = /^\+?\d+$/.test(normal);
    input?.setCustomValidity(ok ? '' : 'Ingrese solo nÃƒÂºmeros y un "+" inicial.');
  };
  phoneInputs.forEach((el) => {
    const obligatorio = el.getAttribute('name') === 'telefono1';
    const handler = () => validarTelefono(el, obligatorio);
    el.addEventListener('input', handler);
    el.addEventListener('blur', handler);
    handler();
  });

  // Eliminado el checkbox de "menor de edad"; el campo apoderado queda visible y opcional

  // --- Tipo de consulta -> muestra detalle sÃƒÂ³lo si es "Otro" ---
  const consultaSelector = document.querySelector('[data-consulta-selector]');
  const consultaOtro     = document.querySelector('[data-consulta-otro]');

  const actualizarConsulta = () => {
    const esOtro = consultaSelector?.value === 'Otro';
    if (consultaOtro) {
      consultaOtro.hidden = !esOtro;
      const campo = consultaOtro.querySelector('input');
      if (campo) {
        campo.disabled = !esOtro;
        if (!esOtro) campo.value = '';
      }
    }
  };
  if (consultaSelector && consultaOtro) {
    consultaSelector.addEventListener('change', actualizarConsulta);
    actualizarConsulta(); // estado inicial
  }

  // --- Establecimientos dependientes de comuna (general) ---
  const EST_CATALOGO = (window.EST_CATALOGO || {});
  const EST_PRE = (window.EST_PRESELECCIONADO || '');
  const DER_PRE = (window.DER_PRESELECCIONADO || '');

  const buscarComunaPorEstablecimiento = (nombre) => {
    if (!nombre) return '';
    for (const [com, lista] of Object.entries(EST_CATALOGO)) {
      if (lista.includes(nombre)) return com;
    }
    return '';
  };

const poblarEstablecimientosEn = (selectEl, comuna, preselect = '') => {
  if (!selectEl) return;
  const opciones = EST_CATALOGO[comuna] || [];
  const current = selectEl.value;
  selectEl.innerHTML = '';
  const def = document.createElement('option');
  def.value = '';
  def.disabled = true;
  def.selected = true;
  def.textContent = 'Seleccione un establecimiento';
  selectEl.appendChild(def);
  const setOption = (nombre, selected = false) => {
    const op = document.createElement('option');
    op.value = nombre;
    op.textContent = nombre;
    if (selected) op.selected = true;
    selectEl.appendChild(op);
  };
  opciones.forEach((nombre) => setOption(nombre, preselect && preselect === nombre));
  if (preselect && !opciones.includes(preselect)) {
    // Si el establecimiento no está en catálogo, lo agregamos para mantener el valor
    setOption(preselect, true);
  }
  if (!preselect && opciones.includes(current)) {
    selectEl.value = current;
  }
};

const initParDependiente = (comunaEl, estEl, preselected) => {
  if (!(comunaEl && estEl)) return;
  let comunaIni = preselected ? buscarComunaPorEstablecimiento(preselected) : '';
  if (comunaIni) {
    comunaEl.value = comunaIni;
    poblarEstablecimientosEn(estEl, comunaIni, preselected);
  } else if (preselected) {
    // Si no se encontró comuna, igual mostrar el establecimiento preseleccionado
    poblarEstablecimientosEn(estEl, '', preselected);
  }
  comunaEl.addEventListener('change', () => {
    poblarEstablecimientosEn(estEl, comunaEl.value, '');
  });
};

  // Par 1: Establecimiento APS
  initParDependiente(
    document.querySelector('[data-est-comuna]'),
    document.querySelector('[data-est-select]'),
    EST_PRE,
  );
  // Par 2: Derivación
  initParDependiente(
    document.querySelector('[data-der-comuna]'),
    document.querySelector('[data-der-select]'),
    DER_PRE,
  );

  // --- Selecciones dinámicas de Patologías GES (máx 3) ---
  const gesContainer = document.querySelector('[data-ges-container]');
  if (gesContainer) {
    const limit = Number.parseInt(gesContainer.dataset.gesMax || '3', 10);
    const templateItem = gesContainer.querySelector('[data-ges-item]');
    const getSelects = () => Array.from(gesContainer.querySelectorAll('select[name="patologias_ges"]'));

    const parseSeleccionadas = () => {
      const raw = gesContainer.dataset.gesSelected || '[]';
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          return parsed
            .filter((item) => typeof item === 'string' && item.trim())
            .slice(0, Number.isNaN(limit) ? 3 : limit);
        }
      } catch (_err) {
        // Ignorar errores de parseo y continuar con lista vacía
      }
      return [];
    };

    const pruneEmptySelects = () => {
      const selects = getSelects();
      let blanksAllowed = 1;
      selects.forEach((select) => {
        if (!select.value.trim()) {
          if (blanksAllowed > 0) {
            blanksAllowed -= 1;
          } else {
            const wrapper = select.closest('[data-ges-item]');
            if (wrapper && wrapper !== templateItem) {
              wrapper.remove();
            }
          }
        }
      });
    };

    const handleSelectChange = () => {
      pruneEmptySelects();
      ensureEmptySlot();
    };

    const attachListener = (select) => {
      if (select) {
        select.addEventListener('change', handleSelectChange);
      }
    };

    const createSelect = (valor = '') => {
      if (!templateItem) return null;
      const nuevo = templateItem.cloneNode(true);
      const select = nuevo.querySelector('select[name="patologias_ges"]');
      if (select) {
        select.value = valor;
        attachListener(select);
      }
      gesContainer.appendChild(nuevo);
      return select;
    };

    const ensureEmptySlot = () => {
      const selects = getSelects();
      const limite = Number.isNaN(limit) ? 3 : limit;
      const hasEmpty = selects.some((select) => !select.value.trim());
      if (!hasEmpty && selects.length < limite) {
        createSelect('');
      }
    };

    const initGesSelects = () => {
      if (!templateItem) return;
      const firstSelect = templateItem.querySelector('select[name="patologias_ges"]');
      attachListener(firstSelect);
      const valores = parseSeleccionadas();
      if (firstSelect && valores.length > 0) {
        firstSelect.value = valores[0];
      }
      for (let i = 1; i < valores.length; i += 1) {
        createSelect(valores[i]);
      }
      ensureEmptySlot();
    };

    initGesSelects();
  }


  // INDICADOR
});
