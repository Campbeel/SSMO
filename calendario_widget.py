import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, date
import calendar

class CalendarioWidget(tk.Toplevel):
    """Widget de calendario para selección de fechas"""
    def __init__(self, parent, fecha_actual=None):
        super().__init__(parent)
        self.parent = parent
        self.fecha_seleccionada = fecha_actual or date.today()
        self.resultado = None
        
        self.title("🗓️ Seleccionar fecha")
        self.geometry("300x280")
        self.resizable(False, False)
        
        # Centrar la ventana
        self.transient(parent)
        self.grab_set()
        
        self._crear_widgets()
        
    def _crear_widgets(self):
        # Frame principal
        self.main_frame = ttk.Frame(self)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Navegación principal con selectores
        nav_frame = ttk.Frame(self.main_frame)
        nav_frame.pack(fill="x", pady=(0, 5))
        
        # Botones mes anterior/siguiente
        self.btn_anterior = ttk.Button(nav_frame, text="◀", width=3, 
                                      command=self._mes_anterior)
        self.btn_anterior.pack(side="left")
        
        # Selector de mes con búsqueda incremental
        meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
        self.combo_mes = ttk.Combobox(nav_frame, values=meses, state="readonly", width=12)
        self.combo_mes.set(meses[self.fecha_seleccionada.month - 1])
        self.combo_mes.bind('<<ComboboxSelected>>', self._cambiar_mes)
        self.combo_mes.bind('<KeyPress>', self._navegar_mes_incremental)
        self.combo_mes.bind('<FocusOut>', self._reset_busqueda_mes)
        self.combo_mes.pack(side="left", padx=(5, 0))
        
        # Selector de año con búsqueda incremental
        anos = list(range(1900, date.today().year + 20))  # De 1900 a 20 años en el futuro
        self.combo_ano = ttk.Combobox(nav_frame, values=[str(ano) for ano in anos], 
                                    state="readonly", width=6)
        self.combo_ano.set(str(self.fecha_seleccionada.year))
        self.combo_ano.bind('<<ComboboxSelected>>', self._cambiar_ano)
        self.combo_ano.bind('<KeyPress>', self._navegar_ano_incremental)
        self.combo_ano.bind('<FocusOut>', self._reset_busqueda_ano)
        self.combo_ano.pack(side="left", padx=(5, 0))
        
        # Variables para acumulación de búsqueda
        self.busqueda_mes = ""
        self.busqueda_ano = ""
        self.timer_mes = None
        self.timer_ano = None
        
        self.btn_siguiente = ttk.Button(nav_frame, text="▶", width=3,
                                      command=self._mes_siguiente)
        self.btn_siguiente.pack(side="left", padx=(5, 0))
        
        # Crear calendario
        self._actualizar_calendario()
        
        # Frame de botones principales
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(fill="x", pady=(10, 0))
        
        ttk.Button(btn_frame, text="✅ Aceptar", command=self._aceptar).pack(side="right", padx=(5, 0))
        ttk.Button(btn_frame, text="❌ Cancelar", command=self._cancelar).pack(side="right")
        
        
    def _actualizar_calendario(self):
        """Actualizar el calendario visual"""
        # Limpiar calendario anterior si existe
        if hasattr(self, 'calendario_frame'):
            self.calendario_frame.destroy()
        
        # Frame del calendario
        self.calendario_frame = ttk.Frame(self.main_frame)
        self.calendario_frame.pack(fill="both", expand=True)
        
        # Actualizar selectores de mes/año
        meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
        self.combo_mes.set(meses[self.fecha_seleccionada.month - 1])
        self.combo_ano.set(self.fecha_seleccionada.year)
        
        # Obtener calendario del mes
        cal = calendar.monthcalendar(self.fecha_seleccionada.year, self.fecha_seleccionada.month)
        
        # Días de la semana
        dias_semana = ['Lu', 'Ma', 'Mi', 'Ju', 'Vi', 'Sá', 'Do']
        for i, dia in enumerate(dias_semana):
            label = ttk.Label(self.calendario_frame, text=dia, width=3, font=('Arial', 8, 'bold'))
            label.grid(row=0, column=i, padx=1, pady=1)
        
        # Días del mes - usar tk.Button para soportar relief
        for semana_idx, semana in enumerate(cal, 1):
            for dia_idx, dia in enumerate(semana):
                btn_dia = tk.Button(self.calendario_frame)
                
                if dia != 0:  # Si hay día en esa celda
                    btn_dia.configure(text=str(dia), width=4, height=2,
                                    command=lambda d=dia: self._seleccionar_dia(d),
                                    font=('Arial', 9))
                    
                    # Marcar el día actual si coincide
                    if dia == self.fecha_seleccionada.day:
                        btn_dia.configure(relief='solid', borderwidth=2, 
                                        bg='lightblue')
                else:
                    btn_dia.configure(text="", width=4, height=2, 
                                    state="disabled")
                
                btn_dia.grid(row=semana_idx, column=dia_idx, padx=1, pady=1)
    
    def _cambiar_mes(self, event):
        """Cambiar mes desde el selector"""
        meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
        mes_seleccionado = self.combo_mes.get()
        nuevo_mes = meses.index(mes_seleccionado) + 1
        
        # Asegurar que el día existe en el nuevo mes
        _, ultimo_dia = calendar.monthrange(self.fecha_seleccionada.year, nuevo_mes)
        dia = min(self.fecha_seleccionada.day, ultimo_dia)
        
        self.fecha_seleccionada = self.fecha_seleccionada.replace(month=nuevo_mes, day=dia)
        self._actualizar_calendario()
    
    def _cambiar_ano(self, event):
        """Cambiar año desde el selector"""
        nuevo_ano = int(self.combo_ano.get())
        
        # Asegurar que el día existe en el nuevo año (para febrero en año bisiesto)
        _, ultimo_dia = calendar.monthrange(nuevo_ano, self.fecha_seleccionada.month)
        dia = min(self.fecha_seleccionada.day, ultimo_dia)
        
        self.fecha_seleccionada = self.fecha_seleccionada.replace(year=nuevo_ano, day=dia)
        self._actualizar_calendario()
    
    def _navegar_mes_incremental(self, event):
        """Búsqueda incremental en selector de mes"""
        char = event.char.upper()
        
        # Solo procesar letras
        if char.isalpha():
            self.busqueda_mes += char
            
            # Cancelar timer anterior si existe
            if self.timer_mes:
                self.root.after_cancel(self.timer_mes)
            
            meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
                    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
            
            # Buscar mes que empiece con la cadena acumulada
            meses_coincidentes = [mes for mes in meses if mes.upper().startswith(self.busqueda_mes)]
            
            if meses_coincidentes:
                self.combo_mes.set(meses_coincidentes[0])
                self._cambiar_mes(None)
            
            # Programar reset de búsqueda después de 1 segundo de inactividad
            self.timer_mes = self.root.after(1000, self._reset_busqueda_mes)
    
    def _navegar_ano_incremental(self, event):
        """Búsqueda incremental en selector de año"""
        char = event.char
        
        # Solo procesar números
        if char.isdigit():
            self.busqueda_ano += char
            
            # Cancelar timer anterior si existe
            if self.timer_ano:
                self.root.after_cancel(self.timer_ano)
            
            valores = self.combo_ano['values']
            
            # Buscar año que empiece con la cadena acumulada
            anos_coincidentes = [ano for ano in valores if ano.startswith(self.busqueda_ano)]
            
            if anos_coincidentes:
                self.combo_ano.set(anos_coincidentes[0])
                self._cambiar_ano(None)
            
            # Programar reset de búsqueda después de 1 segundo de inactividad
            self.timer_ano = self.root.after(1000, self._reset_busqueda_ano)
    
    def _reset_busqueda_mes(self):
        """Resetar búsqueda incremental de mes"""
        self.busqueda_mes = ""
        if self.timer_mes:
            self.root.after_cancel(self.timer_mes)
            self.timer_mes = None
    
    def _reset_busqueda_ano(self):
        """Resetar búsqueda incremental de año"""
        self.busqueda_ano = ""
        if self.timer_ano:
            self.root.after_cancel(self.timer_ano)
            self.timer_ano = None
    
    def _mes_anterior(self):
        """Ir al mes anterior"""
        if self.fecha_seleccionada.month == 1:
            self.fecha_seleccionada = self.fecha_seleccionada.replace(
                year=self.fecha_seleccionada.year - 1, month=12)
        else:
            # Asegurar que el día existe en el mes anterior
            nuevo_mes = self.fecha_seleccionada.month - 1
            _, ultimo_dia = calendar.monthrange(self.fecha_seleccionada.year, nuevo_mes)
            dia = min(self.fecha_seleccionada.day, ultimo_dia)
            self.fecha_seleccionada = self.fecha_seleccionada.replace(month=nuevo_mes, day=dia)
        self._actualizar_calendario()
        
    def _mes_siguiente(self):
        """Ir al mes siguiente"""
        if self.fecha_seleccionada.month == 12:
            self.fecha_seleccionada = self.fecha_seleccionada.replace(
                year=self.fecha_seleccionada.year + 1, month=1)
        else:
            # Asegurar que el día existe en el mes siguiente
            nuevo_mes = self.fecha_seleccionada.month + 1
            _, ultimo_dia = calendar.monthrange(self.fecha_seleccionada.year, nuevo_mes)
            dia = min(self.fecha_seleccionada.day, ultimo_dia)
            self.fecha_seleccionada = self.fecha_seleccionada.replace(month=nuevo_mes, day=dia)
        self._actualizar_calendario()
    
    def _seleccionar_dia(self, dia):
        """Seleccionar un día específico"""
        # Asegurar que el día existe en el mes actual
        _, ultimo_dia = calendar.monthrange(self.fecha_seleccionada.year, self.fecha_seleccionada.month)
        if dia <= ultimo_dia:
            self.fecha_seleccionada = self.fecha_seleccionada.replace(day=dia)
        
        self._actualizar_calendario()
    
    def _aceptar(self):
        """Aceptar la fecha seleccionada"""
        self.resultado = self.fecha_seleccionada.strftime("%d/%m/%Y")
        self.destroy()
    
    def _cancelar(self):
        """Cancelar selección"""
        self.resultado = None
        self.destroy()
