import streamlit as st
import pandas as pd
import numpy as np
import gspread
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date, timedelta
import calendar
from fpdf import FPDF
import base64
from io import BytesIO
import json
import time
import requests
from dateutil.relativedelta import relativedelta

# ================= CONFIGURACIÓN =================
st.set_page_config(page_title="Control Total V6", page_icon="💎", layout="wide")

# ================= 🔒 LOGIN =================
def check_password():
    if st.session_state.get('password_correct', False):
        return True
    
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("### 💎 Acceso Master")
        with st.form("login_form"):
            user = st.text_input("Usuario")
            pwd = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Entrar"):
                if user == st.secrets.get("admin_user", "admin") and pwd == st.secrets.get("admin_pass", "1234"):
                    st.session_state['password_correct'] = True
                    st.rerun()
                else:
                    st.error("❌ Datos incorrectos")
    return False

if not check_password():
    st.stop()

# ================= CONEXIÓN GOOGLE =================
def conectar_google():
    try:
        if 'credenciales_seguras' in st.secrets:
            b64 = st.secrets['credenciales_seguras']
            creds = json.loads(base64.b64decode(b64).decode('utf-8'))
            gc = gspread.service_account_from_dict(creds)
        else:
            gc = gspread.service_account(filename='credentials.json')
        return gc.open("BaseDatos_Maestra")
    except Exception as e:
        st.error(f"Error conexión: {e}")
        st.stop()

# ================= LÓGICA DE DATOS Y FECHAS =================
def calcular_fecha_inteligente(dia_objetivo):
    """Calcula la próxima ocurrencia de un día (ej: día 25) ajustando meses cortos"""
    if not dia_objetivo or dia_objetivo == 0: return None
    hoy = datetime.now().date()
    anio, mes = hoy.year, hoy.month
    
    try:
        _, ultimo = calendar.monthrange(anio, mes)
        dia = min(int(dia_objetivo), ultimo)
        fecha = date(anio, mes, dia)
    except: return hoy

    if fecha < hoy: # Si ya pasó, vamos al mes siguiente
        mes += 1
        if mes > 12: mes=1; anio+=1
        _, ultimo = calendar.monthrange(anio, mes)
        dia = min(int(dia_objetivo), ultimo)
        fecha = date(anio, mes, dia)
    return fecha

# ================= NUEVA LÓGICA: MOTOR DE PROYECCIÓN =================
def generar_flujo_real(df_bruto):
    """Convierte compras a MSI en flujo mensual real"""
    pagos_proyectados = []
    
    # Aseguramos que existan las columnas para no romper el código si faltan
    cols_req = ['PLAZO_MESES', 'INTERES', 'DIA_CORTE']
    for c in cols_req:
        if c not in df_bruto.columns: df_bruto[c] = 0

    for index, row in df_bruto.iterrows():
        try:
            # Si no hay fecha válida, saltamos
            if pd.isna(pd.to_datetime(row['FECHA'], errors='coerce')): continue

            fecha_compra = pd.to_datetime(row['FECHA'], dayfirst=True)
            monto_original = abs(float(str(row['IMPORTE']).replace(',','')))
            
            # Datos de configuración (con valores por defecto seguros)
            plazo = int(row['PLAZO_MESES']) if str(row['PLAZO_MESES']).isdigit() and int(row['PLAZO_MESES']) > 0 else 1
            interes_pct = float(row['INTERES']) if str(row['INTERES']).replace('.','',1).isdigit() else 0.0
            dia_corte = int(row['DIA_CORTE']) if str(row['DIA_CORTE']).isdigit() else 0
            
            # Cálculo
            monto_total = monto_original * (1 + (interes_pct / 100))
            pago_mensual = monto_total / plazo
            
            # Ajuste de Fecha de Inicio (Corte de Tarjeta)
            fecha_inicio = fecha_compra
            if dia_corte > 0 and fecha_compra.day > dia_corte:
                fecha_inicio = fecha_compra + relativedelta(months=1)

            # Generar Cuotas
            for i in range(plazo):
                fecha_pago = fecha_inicio + relativedelta(months=i)
                pagos_proyectados.append({
                    'FECHA': fecha_pago,
                    'DESCRIPCION': f"{row['DESCRIPCION']} ({i+1}/{plazo})" if plazo > 1 else row['DESCRIPCION'],
                    'IMPORTE': pago_mensual,
                    'IMPORTE_REAL': -pago_mensual if 'Gasto' in str(row.get('TIPO','Gasto')) else pago_mensual,
                    'CATEGORIA': str(row['DESCRIPCION']).split()[0], # Simple categorización
                    'TIPO_FLUJO': 'Diferido' if plazo > 1 else 'Contado'
                })
        except: continue

    return pd.DataFrame(pagos_proyectados)

@st.cache_data(ttl=5)
def cargar_datos_master():
    sh = conectar_google()
    
    # 1. Movimientos (Para calcular deuda real de tarjetas)
    try:
        df_movs = pd.DataFrame(sh.sheet1.get_all_records()).astype(str)
        if not df_movs.empty:
            df_movs['IMPORTE'] = pd.to_numeric(df_movs['IMPORTE'], errors='coerce').fillna(0).abs()
            df_movs['FECHA'] = pd.to_datetime(df_movs['FECHA'], errors='coerce', dayfirst=True)
            # Signo Real
            df_movs['IMPORTE_REAL'] = df_movs.apply(lambda x: -x['IMPORTE'] if 'GASTO' in str(x['TIPO']).upper() else x['IMPORTE'], axis=1)
    except: df_movs = pd.DataFrame()

    # 2. Deudas y Configuración de Cuentas
    calendario = []
    alertas = []
    
    try:
        df_deudas = pd.DataFrame(sh.worksheet("Deudas").get_all_records())
        if not df_deudas.empty:
            cols_num = ['MONTO_TOTAL', 'ABONADO', 'PLAZO_MESES']
            for c in cols_num:
                df_deudas[c] = pd.to_numeric(df_deudas[c], errors='coerce').fillna(0)

            # PROCESAMIENTO INTELIGENTE
            for idx, row in df_deudas.iterrows():
                if row['ESTADO'] != 'Activo': continue
                
                nombre = row['NOMBRE']
                tipo = row['TIPO']
                dia_pago = int(row['DIA_PAGO']) if str(row['DIA_PAGO']).isdigit() else 0
                
                # A) ES TARJETA DE CRÉDITO (Deuda Fluctuante)
                if "Tarjeta" in tipo or "Crédito" in tipo:
                    # Buscamos el saldo real en los movimientos
                    saldo_real = 0
                    if not df_movs.empty:
                        # Sumamos todo lo gastado (negativo) y pagado (positivo) en esa cuenta
                        movs_tarjeta = df_movs[df_movs['BANCO'] == nombre]
                        saldo_real = movs_tarjeta['IMPORTE_REAL'].sum()
                    
                    deuda_actual = abs(saldo_real) if saldo_real < 0 else 0
                    
                    # Generar evento de calendario
                    prox_pago = calcular_fecha_inteligente(dia_pago)
                    if prox_pago and deuda_actual > 0:
                        dias = (prox_pago - datetime.now().date()).days
                        calendario.append({"Fecha": prox_pago, "Evento": f"Pago {nombre}", "Monto": deuda_actual, "Tipo": "Tarjeta"})
                        if 0 <= dias <= 5:
                            alertas.append(f"💳 **{nombre}**: Pagar ${deuda_actual:,.2f} antes del {prox_pago.strftime('%d/%m')}")

                # B) ES PRÉSTAMO FIJO (Deuda Fija / Mensualidades)
                else:
                    total = row['MONTO_TOTAL']
                    abonado = row['ABONADO']
                    meses = max(row['PLAZO_MESES'], 1)
                    
                    # Cálculo de mensualidad sugerida
                    mensualidad = total / meses
                    restante = total - abonado
                    pago_sugerido = min(mensualidad, restante)
                    
                    prox_pago = calcular_fecha_inteligente(dia_pago)
                    if prox_pago and restante > 0:
                        dias = (prox_pago - datetime.now().date()).days
                        calendario.append({"Fecha": prox_pago, "Evento": f"Mensualidad {nombre}", "Monto": pago_sugerido, "Tipo": "Prestamo"})
                        if 0 <= dias <= 5:
                            alertas.append(f"🏦 **{nombre}**: Mensualidad de ${pago_sugerido:,.2f} vence el {prox_pago.strftime('%d/%m')}")
                            
    except Exception as e:
        df_deudas = pd.DataFrame()
        st.error(f"Error Deudas: {e}")

    # 3. Inversiones (Simple)
    try:
        df_inv = pd.DataFrame(sh.worksheet("Inversiones").get_all_records())
        if not df_inv.empty:
            df_inv['FECHA_INICIO'] = pd.to_datetime(df_inv['FECHA_INICIO'], errors='coerce', dayfirst=True)
            df_inv['MONTO_INICIAL'] = pd.to_numeric(df_inv['MONTO_INICIAL']).fillna(0)
    except: df_inv = pd.DataFrame()

    return df_movs, df_deudas, df_inv, calendario, alertas, sh

# ================= HERRAMIENTAS V3 (PDF/Excel) =================
def generar_pdf(fecha, cuenta, monto, concepto):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "COMPROBANTE", ln=1, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Fecha: {fecha}", ln=1)
    pdf.cell(0, 10, f"Monto: ${monto:,.2f}", ln=1)
    pdf.cell(0, 10, f"Concepto: {concepto}", ln=1)
    return pdf.output(dest='S').encode('latin-1')

def descargar_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()

def guardar_registro(sh, hoja, datos):
    try:
        sh.worksheet(hoja).append_row(datos)
        st.cache_data.clear()
        return True
    except: return False

# ================= TELEGRAM (El Mayordomo) =================
def procesar_telegram(sh):
    TOKEN = st.secrets.get("telegram_token")
    MY_ID = str(st.secrets.get("telegram_user_id")).strip()
    if not TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        r = requests.get(url).json()
        if not r.get('ok'): return
        
        for m in r['result']:
            uid = m['update_id']
            requests.get(f"{url}?offset={uid+1}") # Borrar cola
            if str(m['message']['chat']['id']) != MY_ID: continue
            
            txt = m['message'].get('text','').lower().split()
            if len(txt) >= 3:
                # Lógica simple: gasto 50 concepto
                try:
                    monto = float(txt[1])
                    desc = " ".join(txt[2:])
                    hoy = datetime.now().strftime("%Y-%m-%d")
                    tipo = "Gasto" if "gasto" in txt[0] else "Pago"
                    guardar_registro(sh, "Hoja 1", ["Telegram", hoy, desc, monto, "-", "-", tipo, "Efectivo"])
                    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": MY_ID, "text": f"✅ {tipo} de ${monto} anotado."})
                except: pass
    except: pass

# ================= INTERFAZ PRINCIPAL =================
df_movs, df_deudas, df_inv, calendario, alertas, sh_obj = cargar_datos_master()
# --- 🚀 AQUÍ LA MAGIA: Generamos el Flujo Real ---
if not df_movs.empty:
    df_flujo_real = generar_flujo_real(df_movs)
else:
    df_flujo_real = pd.DataFrame()
# --------------------------------------------------
# --- SIDEBAR: CENTRO DE MANDO ---
with st.sidebar:
    st.title("🎛️ Centro de Mando")
    
    if st.button("🤖 Sincronizar Telegram"):
        procesar_telegram(sh_obj)
        st.rerun()
    
    st.divider()
    
    # 1. ACTUALIZAR CUENTAS EXISTENTES
    with st.expander("⚙️ Configurar Cuenta/Tarjeta"):
        st.info("Agrega fechas a tus cuentas existentes para activar el calendario.")
        with st.form("config_cuenta"):
            cuentas_existentes = sorted(list(df_movs['BANCO'].unique())) if not df_movs.empty else []
            cta_sel = st.selectbox("Selecciona Cuenta", cuentas_existentes + ["Nueva..."])
            c_tipo = st.selectbox("Tipo", ["Tarjeta Crédito (Fluctuante)", "Préstamo Fijo", "Débito/Efectivo"])
            
            # Inputs condicionales visuales
            st.caption("Configuración de Fechas:")
            col_a, col_b = st.columns(2)
            dia_corte = col_a.number_input("Día Corte", 0, 31, 0)
            dia_pago = col_b.number_input("Día Pago", 0, 31, 0)
            
            if st.form_submit_button("Guardar Configuración"):
                # Si es nueva o existente, la agregamos a DEUDAS como configuración
                # NOMBRE, TIPO, TOTAL(0), PLAZO(1), CORTE, PAGO, ABONADO(0), ESTADO
                guardar_registro(sh_obj, "Deudas", [cta_sel, c_tipo, 0, 1, dia_corte, dia_pago, 0, "Activo"])
                st.toast("Cuenta configurada!")
                time.sleep(1)
                st.rerun()

    # 2. NUEVO PRÉSTAMO (DIVIDIR EN MESES)
    with st.expander("🤝 Nuevo Préstamo / MSI"):
        with st.form("nuevo_prestamo"):
            p_nom = st.text_input("Nombre (ej: Préstamo Coche)")
            p_total = st.number_input("Monto Total Deuda", min_value=0.0)
            p_meses = st.number_input("Plazo (Meses)", min_value=1, value=12)
            p_dia = st.number_input("Día de Pago Mensual", 1, 31, 5)
            
            mensualidad = p_total / p_meses if p_meses > 0 else 0
            st.write(f"**Mensualidad estimada:** ${mensualidad:,.2f}")
            
            if st.form_submit_button("Registrar Deuda"):
                guardar_registro(sh_obj, "Deudas", [p_nom, "Préstamo Fijo", p_total, p_meses, 0, p_dia, 0, "Activo"])
                st.rerun()

    # 3. GASTO RÁPIDO
    with st.expander("💸 Gasto Rápido"):
        with st.form("fast_gasto"):
            g_monto = st.number_input("Monto", min_value=0.0, step=0.01, format="%.2f")
            g_desc = st.text_input("Concepto")
            g_cta = st.selectbox("Cuenta", cuentas_existentes if cuentas_existentes else ["Efectivo"])
            if st.form_submit_button("Guardar"):
                guardar_registro(sh_obj, "Hoja 1", ["Manual", str(datetime.now().date()), g_desc, g_monto, "-", "-", "Gasto", g_cta])
                st.rerun()

# --- ALERTAS VISIBLES ---
st.subheader(f"Bienvenido, {st.secrets.get('admin_user','Jefe')}")
if alertas:
    for a in alertas: st.error(a)

# --- PESTAÑAS (RECUPERANDO TODO) ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard Visual", "📅 Calendario", "📝 Gestión Detallada", "💳 Deudas & Tarjetas"])

# TAB 1: DASHBOARD VISUAL (Estilo V3)
with tab1:
    # Usamos df_movs para SALDO TOTAL (porque el dinero ya salió del banco)
    saldo = df_movs['IMPORTE_REAL'].sum() if not df_movs.empty else 0
    
    # ... (código de inversiones igual) ...
    inv = df_inv['MONTO_INICIAL'].sum() if not df_inv.empty else 0
   
    col1, col2, col3 = st.columns(3)
    col1.metric("💰 Liquidez Total", f"${saldo:,.2f}")
    col2.metric("📈 En Inversiones", f"${inv:,.2f}")
   
    # --- CAMBIO: USAMOS df_flujo_real PARA GASTOS DEL MES ---
    hoy = datetime.now()
    gastos_mes = 0
    if not df_flujo_real.empty:
        # Filtramos por el mes de pago REAL, no la fecha de compra
        mask_mes = (df_flujo_real['FECHA'].dt.month == hoy.month) & (df_flujo_real['FECHA'].dt.year == hoy.year)
        # Sumamos solo lo negativo (gastos)
        gastos_mes = abs(df_flujo_real[mask_mes & (df_flujo_real['IMPORTE_REAL'] < 0)]['IMPORTE_REAL'].sum())
    
    col3.metric("💸 Gastos Reales (MSI)", f"${gastos_mes:,.2f}")
   
    # GRÁFICOS
    if not df_flujo_real.empty:
        c_g1, c_g2 = st.columns(2)
        with c_g1:
            # --- CAMBIO: Gráfico basado en flujo real ---
            gastos_plot = df_flujo_real[df_flujo_real['IMPORTE_REAL'] < 0].copy()
            # Filtramos solo lo de ESTE mes para el gráfico de pastel
            gastos_plot_mes = gastos_plot[(gastos_plot['FECHA'].dt.month == hoy.month) & (gastos_plot['FECHA'].dt.year == hoy.year)]
            
            if not gastos_plot_mes.empty:
                fig = px.pie(gastos_plot_mes, values='IMPORTE', names='CATEGORIA', title="Gastos Reales de Este Mes")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Sin gastos registrados este mes.")
                
        with c_g2:
            # Evolución saldo (Mantenemos df_movs original para ver historia real bancaria)
            df_evo = df_movs.sort_values('FECHA').copy()
            df_evo['Saldo Acum'] = df_evo['IMPORTE_REAL'].cumsum()
            fig2 = px.line(df_evo, x='FECHA', y='Saldo Acum', title="Evolución Histórica")
            st.plotly_chart(fig2, use_container_width=True)
            
# TAB 2: CALENDARIO (Estilo V5)
with tab2:
    if calendario:
        df_cal = pd.DataFrame(calendario).sort_values("Fecha")
        for i, row in df_cal.iterrows():
            dias = (row['Fecha'] - hoy.date()).days
            color = "#ff4b4b" if dias <= 3 else "#ffa726" if dias <= 7 else "#2ecc71"
            with st.container():
                c1, c2, c3 = st.columns([1,3,2])
                c1.write(f"**{row['Fecha'].strftime('%d %b')}**")
                c2.markdown(f"<span style='color:{color}'>●</span> {row['Evento']}", unsafe_allow_html=True)
                c3.write(f"**${row['Monto']:,.2f}**")
                st.divider()
    else:
        st.info("Configura tus cuentas en el menú lateral para ver el calendario.")

# TAB 3: GESTIÓN DETALLADA (Estilo V4 - PDF/Excel)
with tab3:
    st.markdown("### 🛠️ Herramientas Administrativas")
    
    f_ini = st.date_input("Desde", date(hoy.year, 1, 1))
    f_fin = st.date_input("Hasta", hoy)
    
    if not df_movs.empty:
        mask = (df_movs['FECHA'].dt.date >= f_ini) & (df_movs['FECHA'].dt.date <= f_fin)
        df_view = df_movs.loc[mask].sort_values('FECHA', ascending=False)
        
        c_tool1, c_tool2 = st.columns([3, 1])
        with c_tool1:
            st.dataframe(df_view[['FECHA', 'DESCRIPCION', 'IMPORTE_REAL', 'BANCO']], use_container_width=True)
        
        with c_tool2:
            st.download_button("📥 Bajar Excel", descargar_excel(df_view), "Finanzas.xlsx")
            st.write("---")
            sel_pdf = st.selectbox("Imprimir Movimiento:", df_view.index, format_func=lambda x: f"{df_view.loc[x,'DESCRIPCION']}")
            if st.button("🖨️ Generar PDF"):
                r = df_view.loc[sel_pdf]
                pdf_bytes = generar_pdf(str(r['FECHA'].date()), r['BANCO'], r['IMPORTE'], r['DESCRIPCION'])
                b64 = base64.b64encode(pdf_bytes).decode()
                st.markdown(f'<a href="data:application/pdf;base64,{b64}" download="Recibo.pdf">Descargar PDF</a>', unsafe_allow_html=True)

# TAB 4: DEUDAS Y TARJETAS (Híbrido)
with tab4:
    st.subheader("💳 Estado de Deudas")
    
    if not df_deudas.empty:
        for idx, row in df_deudas.iterrows():
            if row['ESTADO'] == 'Activo':
                with st.container():
                    nombre = row['NOMBRE']
                    tipo = row['TIPO']
                    
                    if "Tarjeta" in tipo:
                        # Lógica Fluctuante: Calculamos deuda basada en gastos reales
                        saldo_card = 0
                        if not df_movs.empty:
                            saldo_card = df_movs[df_movs['BANCO'] == nombre]['IMPORTE_REAL'].sum()
                        
                        deuda_real = abs(saldo_card) if saldo_card < 0 else 0
                        st.markdown(f"### 💳 {nombre} (Crédito)")
                        c1, c2 = st.columns(2)
                        c1.metric("Deuda Actual (Fluctuante)", f"${deuda_real:,.2f}")
                        if row.get('DIA_CORTE') and int(row['DIA_CORTE']) > 0:
                             c2.caption(f"📅 Corte: Día {int(row['DIA_CORTE'])} | Pago: Día {int(row['DIA_PAGO'])}")
                        
                        # Barra inversa (Cuanto más debes, más se llena)
                        st.progress(min(deuda_real / 10000, 1.0)) # Asumiendo linea de 10k visual
                        st.divider()

                    else:
                        # Lógica Fija (Préstamos)
                        total = row['MONTO_TOTAL']
                        abonado = row['ABONADO']
                        pendiente = total - abonado
                        progreso = abonado / total if total > 0 else 0
                        
                        st.markdown(f"### 🏦 {nombre} (Préstamo)")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Deuda Original", f"${total:,.2f}")
                        c2.metric("Pagado", f"${abonado:,.2f}")
                        c3.metric("Pendiente", f"${pendiente:,.2f}")
                        
                        st.progress(progreso)
                        st.caption(f"Plazo: {int(row['PLAZO_MESES'])} meses")
                        
                        # Botón rápido de abono
                        if st.button(f"Abonar Mensualidad (${total/max(row['PLAZO_MESES'],1):,.2f})", key=f"btn_{idx}"):
                             # Actualizar hoja Deudas
                             nuevo_abono = abonado + (total/max(row['PLAZO_MESES'],1))
                             sh_obj.worksheet("Deudas").update_cell(idx+2, 7, nuevo_abono) # Col 7 es Abonado
                             st.toast("Abono registrado!")
                             time.sleep(1)
                             st.rerun()
                        st.divider()
    else:
        st.info("Configura tus cuentas en el menú lateral.")
