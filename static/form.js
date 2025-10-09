document.addEventListener('DOMContentLoaded', () => {
  const fechaNacimientoInput = document.querySelector('input[name="fecha_nacimiento"]');
  const edadInput = document.querySelector('input[name="edad"]');
  const rutInputs = document.querySelectorAll('input[data-rut]');
  const menorToggle = document.querySelector('#menor-edad');
  const menorField = document.querySelector('[data-menor-field]');
  const consultaSelector = document.querySelector('[data-consulta-selector]');
  const consultaOtro = document.querySelector('[data-consulta-otro]');

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
      input.setCustomValidity('El RUT ingresado no es válido.');
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

  if (menorToggle && menorField) {
    const toggleMenor = () => {
      const habilitado = menorToggle.checked;
      const campo = menorField.querySelector('input');
      if (campo) {
        campo.disabled = !habilitado;
        if (!habilitado) {
          campo.value = '';
          campo.setCustomValidity('');
        }
      }
      menorField.classList.toggle('field-disabled', !habilitado);
    };
    menorToggle.addEventListener('change', toggleMenor);
    toggleMenor();
  }

  if (consultaSelector && consultaOtro) {
    const actualizarConsulta = () => {
      const esOtro = consultaSelector.value === 'Otro';
      const campo = consultaOtro.querySelector('input');
      if (campo) {
        campo.disabled = !esOtro;
        if (!esOtro) {
          campo.value = '';
          campo.setCustomValidity('');
        }
      }
      consultaOtro.classList.toggle('field-disabled', !esOtro);
    };
    consultaSelector.addEventListener('change', actualizarConsulta);
    actualizarConsulta();
  }
});
