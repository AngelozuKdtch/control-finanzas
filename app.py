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
st.set_page_config(page_title="Control Total V7", page_icon="💎", layout="wide")

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

# ================= NUEVO MOTOR: PROYECCIÓN FINANCIERA =================
def generar_flujo_real(df_bruto):
    """
    Convierte compras a MSI y con intereses en un flujo mensual real.
    Ajusta fechas automáticamente según el día de corte.
    """
    pagos_proyectados = []
    
    # Aseguramos que existan las columnas para no romper el código si faltan
    cols_req = ['PLAZO_MESES', 'INTERES', 'DIA_CORTE']
    for c in cols_req:
        if c not in df_bruto.columns: df_bruto[c] = 0

    for index, row in df_bruto.iterrows():
        try:
            if pd.isna(pd.to_datetime(row['FECHA'], errors='coerce')): continue

            fecha_compra = pd.to_datetime(row['FECHA'], dayfirst=True)
            monto_original = abs(float(str(row['IMPORTE']).replace(',','')))
            
            try: plazo = int(float(str(row['PLAZO_MESES']))) 
            except: plazo = 1
            if plazo < 1: plazo = 1

            try: interes_pct = float(str(row['INTERES']).replace('%','')) 
            except: interes_pct = 0.0

            try: dia_corte = int(float(str(row['DIA_CORTE']))) 
            except: dia_corte = 0
            
            # --- CÁLCULO MATEMÁTICO ---
            monto_total = monto_original * (1 + (interes_pct / 100))
            pago_mensual = monto_total / plazo
            
            # --- LÓGICA DE TIEMPO (CORTE DE TARJETA) ---
            fecha_inicio = fecha_compra
            if dia_corte > 0 and fecha_compra.day > dia_corte:
                fecha_inicio = fecha_compra + relativedelta(months=1)

            for i in range(plazo):
                fecha_pago = fecha_inicio + relativedelta(months=i)
                desc_extra = f" ({i+1}/{plazo})" if plazo > 1 else ""
                
                pagos_proyectados.append({
                    'FECHA': fecha_pago,
                    'DESCRIPCION': f"{row['DESCRIPCION']}{desc_extra}",
                    'IMPORTE': pago_mensual,
                    'IMPORTE_REAL': -pago_mensual if 'Gasto' in str(row.get('TIPO','Gasto')) else pago_mensual,
                    'CATEGORIA': str(row['DESCRIPCION']).split()[0], 
                    'TIPO_FLUJO': 'Diferido' if plazo > 1 else 'Contado'
                })
        except Exception as e: 
            continue

    return pd.DataFrame(pagos_proyectados)
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
        st.error(f"Error conexión Google: {e}")
        st.stop()

# ================= TELEGRAM: MAYORDOMO Y ALERTAS =================
def enviar_mensaje_telegram(mensaje):
    TOKEN = st.secrets.get("telegram_token")
    MY_ID = str(st.secrets.get("telegram_user_id")).strip()
    if TOKEN and MY_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": MY_ID, "text": mensaje})
        except: pass

def procesar_telegram(sh, df_deudas):
    """Lee mensajes y ENVÍA ALERTAS DE PAGO"""
    TOKEN = st.secrets.get("telegram_token")
    if not TOKEN: return

    # 1. ENVIAR ALERTAS DE PAGO (3 días antes)
    if not df_deudas.empty:
        hoy = datetime.now().date()
        for idx, row in df_deudas.iterrows():
            if row['ESTADO'] != 'Activo': continue
            nombre = row['NOMBRE']
            
            # Checar Día de Pago
            dia_pago = int(row.get('DIA_PAGO', 0))
            if dia_pago > 0:
                fecha_pago = calcular_fecha_inteligente(dia_pago)
                dias_restantes = (fecha_pago - hoy).days
                if 0 <= dias_restantes <= 3:
                    msg = f"🔔 AVISO: El pago de '{nombre}' vence en {dias_restantes} días ({fecha_pago.strftime('%d/%m')})."
                    enviar_mensaje_telegram(msg)
            
            # Checar Día de Corte (Solo Tarjetas)
            dia_corte = int(row.get('DIA_CORTE', 0))
            if dia_corte > 0 and "Tarjeta" in row['TIPO']:
                fecha_corte = calcular_fecha_inteligente(dia_corte)
                dias_restantes = (fecha_corte - hoy).days
                if 0 <= dias_restantes <= 3:
                    msg = f"✂️ AVISO: Corte de tarjeta '{nombre}' en {dias_restantes} días ({fecha_corte.strftime('%d/%m')})."
                    enviar_mensaje_telegram(msg)

    # 2. LEER MENSAJES RECIBIDOS (Para anotar gastos)
    MY_ID = str(st.secrets.get("telegram_user_id")).strip()
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        r = requests.get(url).json()
        if r.get('ok'):
            for m in r['result']:
                uid = m['update_id']
                requests.get(f"{url}?offset={uid+1}") # Borrar cola
                if str(m['message']['chat']['id']) != MY_ID: continue
                
                txt = m['message'].get('text','').lower().split()
                if len(txt) >= 2:
                    try:
                        if txt[0].replace('.','',1).isdigit():
                            monto = float(txt[0])
                            desc = " ".join(txt[1:])
                            tipo = "Gasto"
                        else:
                            monto = float(txt[1])
                            desc = " ".join(txt[2:])
                            tipo = "Pago" if "pago" in txt[0] else "Gasto"
                        
                        hoy_str = datetime.now().strftime("%Y-%m-%d")
                        guardar_registro(sh, "Hoja 1", ["Telegram", hoy_str, desc, monto, "-", "-", tipo, "Efectivo", 1, 0, 0])
                        enviar_mensaje_telegram(f"✅ {tipo} de ${monto} anotado.")
                    except: pass
    except: pass

# ================= CARGA DE DATOS =================
@st.cache_data(ttl=5)
def cargar_datos_master():
    sh = conectar_google()
    try:
        df_movs = pd.DataFrame(sh.sheet1.get_all_records()).astype(str)
        if not df_movs.empty:
            df_movs['IMPORTE'] = pd.to_numeric(df_movs['IMPORTE'], errors='coerce').fillna(0).abs()
            df_movs['FECHA'] = pd.to_datetime(df_movs['FECHA'], errors='coerce', dayfirst=True)
            if 'PLAZO_MESES' not in df_movs.columns: df_movs['PLAZO_MESES'] = 1
            if 'INTERES' not in df_movs.columns: df_movs['INTERES'] = 0
            if 'DIA_CORTE' not in df_movs.columns: df_movs['DIA_CORTE'] = 0
            df_movs['IMPORTE_REAL'] = df_movs.apply(
                lambda x: -x['IMPORTE'] if 'GASTO' in str(x['TIPO']).upper() else x['IMPORTE'], axis=1
            )
    except: df_movs = pd.DataFrame()

    calendario = []
    alertas = []
    try:
        df_deudas = pd.DataFrame(sh.worksheet("Deudas").get_all_records())
        if not df_deudas.empty:
            cols_num = ['MONTO_TOTAL', 'ABONADO', 'PLAZO_MESES', 'DIA_CORTE', 'DIA_PAGO', 'INTERES_ORIGINAL']
            for c in cols_num:
                if c in df_deudas.columns: df_deudas[c] = pd.to_numeric(df_deudas[c], errors='coerce').fillna(0)
            
            # Generar Alertas Visuales locales
            for idx, row in df_deudas.iterrows():
                if row['ESTADO'] != 'Activo': continue
                nombre = row['NOMBRE']
                dia_pago = int(row.get('DIA_PAGO', 1))
                prox_pago = calcular_fecha_inteligente(dia_pago)
                
                # Diferenciar Tarjetas y Prestamos para el calendario
                monto_mostrar = 0
                if "Tarjeta" in row['TIPO']:
                     if not df_movs.empty:
                        saldo = df_movs[df_movs['BANCO'] == nombre]['IMPORTE_REAL'].sum()
                        monto_mostrar = abs(saldo) if saldo < 0 else 0
                else:
                    total = row.get('MONTO_TOTAL', 0)
                    abonado = row.get('ABONADO', 0)
                    restante = total - abonado
                    meses = max(int(row.get('PLAZO_MESES', 1)), 1)
                    monto_mostrar = min(total/meses, restante)

                if prox_pago and monto_mostrar > 1:
                    dias = (prox_pago - datetime.now().date()).days
                    calendario.append({"Fecha": prox_pago, "Evento": f"Pago {nombre}", "Monto": monto_mostrar})
                    if 0 <= dias <= 5:
                        alertas.append(f"🔔 Pagar **{nombre}** (${monto_mostrar:,.2f}) antes del {prox_pago.strftime('%d/%m')}")

    except: df_deudas = pd.DataFrame()

    try:
        df_inv = pd.DataFrame(sh.worksheet("Inversiones").get_all_records())
        if not df_inv.empty: df_inv['MONTO_INICIAL'] = pd.to_numeric(df_inv['MONTO_INICIAL']).fillna(0)
    except: df_inv = pd.DataFrame()

    return df_movs, df_deudas, df_inv, calendario, alertas, sh

# ================= HERRAMIENTAS GUARDADO =================
def generar_pdf(fecha, cuenta, monto, concepto):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "COMPROBANTE", ln=1, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Fecha: {fecha}", ln=1); pdf.cell(0, 10, f"Cuenta: {cuenta}", ln=1)
    pdf.cell(0, 10, f"Monto: ${monto:,.2f}", ln=1); pdf.cell(0, 10, f"Concepto: {concepto}", ln=1)
    return pdf.output(dest='S').encode('latin-1')

def descargar_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer: df.to_excel(writer, index=False)
    return output.getvalue()

def guardar_registro(sh, hoja, datos):
    try:
        sh.worksheet(hoja).append_row(datos)
        st.cache_data.clear()
        return True
    except: return False
# ================= INTERFAZ PRINCIPAL =================
df_movs, df_deudas, df_inv, calendario, alertas, sh_obj = cargar_datos_master()

# 🚀 ACTIVAR MOTOR INTELIGENTE
if not df_movs.empty:
    df_flujo_real = generar_flujo_real(df_movs)
else:
    df_flujo_real = pd.DataFrame()

# --- SIDEBAR ---
with st.sidebar:
    st.title("🎛️ Centro de Mando")
    
    # BOTÓN PODEROSO: Sincroniza y ENVÍA ALERTAS
    if st.button("🤖 Sincronizar y Alertar"):
        procesar_telegram(sh_obj, df_deudas) # <-- Aquí dispara las alertas
        st.toast("Sincronizando y verificando fechas...")
        time.sleep(2)
        st.rerun()
    
    st.divider()
    
    # 1. ACTUALIZAR CUENTAS
    with st.expander("⚙️ Configurar Cuenta/Tarjeta"):
        with st.form("config_cuenta"):
            cuentas_existentes = sorted(list(df_movs['BANCO'].unique())) if not df_movs.empty else []
            cta_sel = st.selectbox("Selecciona Cuenta", cuentas_existentes + ["Nueva..."])
            c_tipo = st.selectbox("Tipo", ["Tarjeta Crédito", "Préstamo Fijo", "Débito/Efectivo"])
            
            col_a, col_b = st.columns(2)
            dia_corte = col_a.number_input("Día Corte", 0, 31, 0)
            dia_pago = col_b.number_input("Día Pago", 0, 31, 0)
            
            if st.form_submit_button("Guardar Configuración"):
                guardar_registro(sh_obj, "Deudas", [cta_sel, c_tipo, 0, 1, dia_corte, dia_pago, 0, "Activo"])
                st.toast("Guardado.")
                time.sleep(1)
                st.rerun()

    # 2. NUEVO REGISTRO (CORREGIDO: YO DEBO / ME DEBEN)
    with st.expander("🤝 Deudas y Préstamos"):
        with st.form("nuevo_prestamo"):
            # A. Selector: ¿Quién debe a quién?
            tipo_deuda = st.radio("¿Quién debe?", ["🔴 Yo debo (Deuda)", "🟢 Me deben (Cobrar)"])
            
            p_nom = st.text_input("Nombre / Concepto")
            
            c1, c2 = st.columns(2)
            p_monto_base = c1.number_input("Monto Inicial", min_value=0.0)
            p_interes = c2.number_input("Interés Total (%)", min_value=0.0, value=0.0)
            
            c3, c4 = st.columns(2)
            p_meses = c3.number_input("Plazo (Meses)", min_value=1, value=12)
            p_dia = c4.number_input("Día de Pago", 1, 31, 15)
            
            deuda_total = p_monto_base * (1 + (p_interes / 100))
            mensualidad = deuda_total / p_meses if p_meses > 0 else 0
            
            st.caption(f"Total: ${deuda_total:,.2f} | Mensual: ${mensualidad:,.2f}")
            
            if st.form_submit_button("Registrar Operación"):
                # Si es "Me deben", el tipo interno es "Por Cobrar"
                tipo_interno = "Por Cobrar" if "Me deben" in tipo_deuda else "Préstamo Fijo"
                guardar_registro(sh_obj, "Deudas", [p_nom, tipo_interno, deuda_total, p_meses, 0, p_dia, 0, "Activo", p_interes])
                st.rerun()

    # 3. GASTO RÁPIDO
    with st.expander("💸 Gasto Rápido"):
        g_monto = st.number_input("Monto", min_value=0.0, step=0.01)
        g_desc = st.text_input("Concepto")
        g_cta = st.selectbox("Cuenta", cuentas_existentes if cuentas_existentes else ["Efectivo"])
        
        es_a_meses = st.checkbox("¿A Meses?")
        g_plazo = 1
        g_interes = 0.0
        
        if es_a_meses:
            c_m1, c_m2 = st.columns(2)
            g_plazo = c_m1.number_input("Meses", 2, 48, 3)
            g_interes = c_m2.number_input("Interés (%)", 0.0, 100.0, 0.0)
            
        if st.button("Guardar Gasto"):
            dia_corte_auto = 0
            try:
                if not df_deudas.empty:
                    row_cta = df_deudas[df_deudas['NOMBRE'] == g_cta]
                    if not row_cta.empty:
                         val = row_cta.iloc[0]['DIA_CORTE'] if 'DIA_CORTE' in row_cta.columns else row_cta.iloc[0, 4]
                         dia_corte_auto = int(val)
            except: pass
            
            fecha_hoy = str(datetime.now().date())
            datos = ["Manual", fecha_hoy, g_desc, g_monto, "-", "-", "Gasto", g_cta, g_plazo, g_interes, dia_corte_auto]
            guardar_registro(sh_obj, "Hoja 1", datos)
            st.success("Registrado.")
            time.sleep(1)
            st.rerun()

st.subheader(f"Bienvenido, {st.secrets.get('admin_user','Jefe')}")
if alertas:
    for a in alertas: st.error(a)
# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "📅 Calendario", "📝 Bitácora", "💳 Deudas/Cobros"])

# TAB 1: DASHBOARD
with tab1:
    saldo = df_movs['IMPORTE_REAL'].sum() if not df_movs.empty else 0
    inv = df_inv['MONTO_INICIAL'].sum() if not df_inv.empty else 0
    
    hoy = datetime.now()
    gastos_mes_real = 0
    if not df_flujo_real.empty:
        mask_mes = (df_flujo_real['FECHA'].dt.month == hoy.month) & (df_flujo_real['FECHA'].dt.year == hoy.year)
        gastos_mes_real = abs(df_flujo_real[mask_mes & (df_flujo_real['IMPORTE_REAL'] < 0)]['IMPORTE_REAL'].sum())

    col1, col2, col3 = st.columns(3)
    col1.metric("💰 Liquidez Total", f"${saldo:,.2f}")
    col2.metric("📈 En Inversiones", f"${inv:,.2f}")
    col3.metric("💸 Gastos Reales Mes", f"${gastos_mes_real:,.2f}", delta_color="inverse")

    if not df_flujo_real.empty:
        c_g1, c_g2 = st.columns(2)
        with c_g1:
            datos_mes = df_flujo_real[(df_flujo_real['FECHA'].dt.month == hoy.month) & (df_flujo_real['IMPORTE_REAL'] < 0)].copy()
            if not datos_mes.empty:
                datos_mes['IMPORTE'] = abs(datos_mes['IMPORTE_REAL'])
                st.plotly_chart(px.pie(datos_mes, values='IMPORTE', names='CATEGORIA', hole=0.4), use_container_width=True)
        with c_g2:
            if not df_movs.empty:
                df_evo = df_movs.sort_values('FECHA').copy()
                df_evo['Saldo Acumulado'] = df_evo['IMPORTE_REAL'].cumsum()
                st.plotly_chart(px.line(df_evo, x='FECHA', y='Saldo Acumulado'), use_container_width=True)

# TAB 2: CALENDARIO
with tab2:
    if calendario:
        df_cal = pd.DataFrame(calendario).sort_values("Fecha")
        for i, row in df_cal.iterrows():
            dias = (row['Fecha'] - hoy.date()).days
            color = "#ff4b4b" if dias <= 3 else "#2ecc71"
            with st.container():
                c1, c2, c3 = st.columns([1,3,2])
                c1.write(f"**{row['Fecha'].strftime('%d %b')}**")
                c2.markdown(f"<span style='color:{color}'>●</span> {row['Evento']}", unsafe_allow_html=True)
                c3.write(f"**${row['Monto']:,.2f}**")
                st.divider()

# TAB 3: BITÁCORA
with tab3:
    if not df_movs.empty:
        df_view = df_movs.sort_values('FECHA', ascending=False).head(50) # Ultimos 50
        st.dataframe(df_view[['FECHA','DESCRIPCION','IMPORTE','BANCO']], use_container_width=True)
        st.download_button("📥 Descargar Excel", descargar_excel(df_view), "Data.xlsx")

# TAB 4: DEUDAS Y COBRANZA (NUEVO DISEÑO DIVIDIDO)
with tab4:
    # --- A. ME DEBEN (ACTIVOS) ---
    st.subheader("🟢 Cuentas por Cobrar (Me deben)")
    if not df_deudas.empty:
        cobros = df_deudas[(df_deudas['TIPO'] == 'Por Cobrar') & (df_deudas['ESTADO'] == 'Activo')]
        if not cobros.empty:
            for idx, row in cobros.iterrows():
                total = row.get('MONTO_TOTAL', 0)
                abonado = row.get('ABONADO', 0)
                pendiente = total - abonado
                plazo = max(int(row.get('PLAZO_MESES', 1)), 1)
                
                with st.container():
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"**👤 {row['NOMBRE']}**")
                    c2.caption(f"Paga día {int(row.get('DIA_PAGO', 1))}")
                    
                    k1, k2, k3 = st.columns(3)
                    k1.metric("Prestaste", f"${total:,.2f}")
                    k2.metric("Recibido", f"${abonado:,.2f}")
                    k3.metric("Te deben", f"${pendiente:,.2f}")
                    
                    cuota = total / plazo
                    if st.button(f"✅ Registrar Cobro (${cuota:,.2f})", key=f"cobro_{idx}"):
                        try:
                            cell = sh_obj.worksheet("Deudas").find(row['NOMBRE'])
                            sh_obj.worksheet("Deudas").update_cell(cell.row, 7, abonado + cuota) 
                            st.toast("Cobro registrado")
                            time.sleep(1); st.rerun()
                        except: st.error("Error")
                    st.divider()
        else: st.info("Nadie te debe dinero (o no lo has registrado).")

    # --- B. YO DEBO (PASIVOS) ---
    st.subheader("🔴 Mis Deudas (Yo debo)")
    if not df_deudas.empty:
        mis_deudas = df_deudas[(df_deudas['TIPO'] != 'Por Cobrar') & (df_deudas['ESTADO'] == 'Activo')]
        for idx, row in mis_deudas.iterrows():
            nombre = row['NOMBRE']
            with st.container():
                if "Tarjeta" in row['TIPO']:
                    st.markdown(f"#### 💳 {nombre}")
                    saldo = 0
                    if not df_movs.empty: saldo = df_movs[df_movs['BANCO'] == nombre]['IMPORTE_REAL'].sum()
                    deuda = abs(saldo) if saldo < 0 else 0
                    
                    c1, c2 = st.columns(2)
                    c1.metric("Deuda Total", f"${deuda:,.2f}")
                    c2.caption(f"Corte: {int(row.get('DIA_CORTE',0))} | Pago: {int(row.get('DIA_PAGO',0))}")
                    st.progress(min(deuda/20000, 1.0))
                else:
                    st.markdown(f"#### 🏦 {nombre}")
                    total = row.get('MONTO_TOTAL', 0)
                    abonado = row.get('ABONADO', 0)
                    pendiente = total - abonado
                    
                    k1, k2, k3 = st.columns(3)
                    k1.metric("Deuda", f"${total:,.2f}")
                    k2.metric("Pagado", f"${abonado:,.2f}")
                    k3.metric("Restante", f"${pendiente:,.2f}")
                    
                    cuota = total / max(int(row.get('PLAZO_MESES', 1)), 1)
                    if st.button(f"💸 Pagar (${cuota:,.2f})", key=f"pago_{idx}"):
                        try:
                            cell = sh_obj.worksheet("Deudas").find(nombre)
                            sh_obj.worksheet("Deudas").update_cell(cell.row, 7, abonado + cuota)
                            st.toast("Pago registrado"); time.sleep(1); st.rerun()
                        except: st.error("Error")
                st.divider()
