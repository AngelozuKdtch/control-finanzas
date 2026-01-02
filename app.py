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
st.set_page_config(page_title="Control Total V8 - Master", page_icon="💎", layout="wide")

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

# ================= LÓGICA DE FECHAS =================
def calcular_fecha_inteligente(dia_objetivo):
    """Calcula la próxima fecha de pago ajustando meses y años"""
    if not dia_objetivo or dia_objetivo == 0: return None
    hoy = datetime.now().date()
    anio, mes = hoy.year, hoy.month
    
    try:
        _, ultimo = calendar.monthrange(anio, mes)
        dia = min(int(dia_objetivo), ultimo)
        fecha = date(anio, mes, dia)
    except: return hoy

    if fecha < hoy: # Si la fecha ya pasó, calcular para el mes siguiente
        mes += 1
        if mes > 12: mes=1; anio+=1
        _, ultimo = calendar.monthrange(anio, mes)
        dia = min(int(dia_objetivo), ultimo)
        fecha = date(anio, mes, dia)
    return fecha

# ================= MOTOR: PROYECCIÓN FINANCIERA (MSI & INTERESES) =================
def generar_flujo_real(df_bruto):
    """Desglosa compras a meses e intereses en pagos mensuales"""
    pagos_proyectados = []
    
    # Garantizar columnas mínimas
    for c in ['PLAZO_MESES', 'INTERES', 'DIA_CORTE']:
        if c not in df_bruto.columns: df_bruto[c] = 0

    for index, row in df_bruto.iterrows():
        try:
            if pd.isna(pd.to_datetime(row['FECHA'], errors='coerce')): continue
            
            # Extracción segura de datos
            fecha_compra = pd.to_datetime(row['FECHA'], dayfirst=True)
            monto_original = abs(float(str(row['IMPORTE']).replace(',','')))
            
            try: plazo = int(float(str(row['PLAZO_MESES']))) 
            except: plazo = 1
            if plazo < 1: plazo = 1

            try: interes_pct = float(str(row['INTERES']).replace('%','')) 
            except: interes_pct = 0.0

            try: dia_corte = int(float(str(row['DIA_CORTE']))) 
            except: dia_corte = 0
            
            # Lógica Financiera
            monto_total = monto_original * (1 + (interes_pct / 100))
            pago_mensual = monto_total / plazo
            
            # Lógica de Corte de Tarjeta
            fecha_inicio = fecha_compra
            if dia_corte > 0 and fecha_compra.day > dia_corte:
                fecha_inicio = fecha_compra + relativedelta(months=1)

            # Generar flujo
            for i in range(plazo):
                fecha_pago = fecha_inicio + relativedelta(months=i)
                desc_extra = f" ({i+1}/{plazo})" if plazo > 1 else ""
                
                # Signo: Si es Gasto es negativo, si es Ingreso es positivo
                es_gasto = 'GASTO' in str(row.get('TIPO','Gasto')).upper()
                importe_real = -pago_mensual if es_gasto else pago_mensual

                pagos_proyectados.append({
                    'FECHA': fecha_pago,
                    'DESCRIPCION': f"{row['DESCRIPCION']}{desc_extra}",
                    'IMPORTE': pago_mensual,
                    'IMPORTE_REAL': importe_real,
                    'CATEGORIA': str(row['DESCRIPCION']).split()[0], 
                    'TIPO_FLUJO': 'Diferido' if plazo > 1 else 'Contado'
                })
        except: continue

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

# ================= TELEGRAM & ALERTAS =================
def enviar_mensaje_telegram(mensaje):
    TOKEN = st.secrets.get("telegram_token")
    MY_ID = str(st.secrets.get("telegram_user_id")).strip()
    if TOKEN and MY_ID:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": MY_ID, "text": mensaje})
        except: pass

def procesar_telegram(sh, df_deudas):
    """Sincroniza mensajes y envía alertas de pago"""
    TOKEN = st.secrets.get("telegram_token")
    if not TOKEN: return

    # 1. ALERTAS DE PAGO (3 días antes)
    if not df_deudas.empty:
        hoy = datetime.now().date()
        for idx, row in df_deudas.iterrows():
            if row['ESTADO'] != 'Activo': continue
            nombre = row['NOMBRE']
            
            # A) Alerta de Fecha de Pago
            dia_pago = int(row.get('DIA_PAGO', 0))
            if dia_pago > 0:
                fecha_pago = calcular_fecha_inteligente(dia_pago)
                dias_rest = (fecha_pago - hoy).days
                if 0 <= dias_rest <= 3:
                    msg = f"🔔 AVISO DE PAGO: '{nombre}' vence en {dias_rest} días ({fecha_pago.strftime('%d/%m')})."
                    enviar_mensaje_telegram(msg)
            
            # B) Alerta de Corte (Solo Tarjetas)
            dia_corte = int(row.get('DIA_CORTE', 0))
            if dia_corte > 0 and "Tarjeta" in row['TIPO']:
                fecha_corte = calcular_fecha_inteligente(dia_corte)
                dias_rest = (fecha_corte - hoy).days
                if 0 <= dias_rest <= 3:
                    msg = f"✂️ AVISO DE CORTE: Tarjeta '{nombre}' corta en {dias_rest} días."
                    enviar_mensaje_telegram(msg)

    # 2. LEER GASTOS DE TELEGRAM
    MY_ID = str(st.secrets.get("telegram_user_id")).strip()
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        r = requests.get(url).json()
        if r.get('ok'):
            for m in r['result']:
                uid = m['update_id']
                requests.get(f"{url}?offset={uid+1}") 
                if str(m['message']['chat']['id']) != MY_ID: continue
                
                txt = m['message'].get('text','').lower().split()
                if len(txt) >= 2:
                    try:
                        # Formato: "50 tacos" o "gasto 50 tacos"
                        if txt[0].replace('.','',1).isdigit():
                            monto = float(txt[0])
                            desc = " ".join(txt[1:])
                            tipo = "Gasto"
                        else:
                            monto = float(txt[1])
                            desc = " ".join(txt[2:])
                            tipo = "Pago" if "pago" in txt[0] else "Gasto"
                        
                        hoy_str = datetime.now().strftime("%Y-%m-%d")
                        # Se guarda como Gasto en efectivo por defecto
                        guardar_registro(sh, "Hoja 1", ["Telegram", hoy_str, desc, monto, "-", "-", tipo, "Efectivo", 1, 0, 0])
                        enviar_mensaje_telegram(f"✅ Anotado: {tipo} ${monto}")
                    except: pass
    except: pass

# ================= CARGA DE DATOS =================
@st.cache_data(ttl=5)
def cargar_datos_master():
    sh = conectar_google()
    
    # 1. Movimientos
    try:
        df_movs = pd.DataFrame(sh.sheet1.get_all_records()).astype(str)
        if not df_movs.empty:
            df_movs['IMPORTE'] = pd.to_numeric(df_movs['IMPORTE'], errors='coerce').fillna(0).abs()
            df_movs['FECHA'] = pd.to_datetime(df_movs['FECHA'], errors='coerce', dayfirst=True)
            # Columnas nuevas defaults
            for col in ['PLAZO_MESES', 'INTERES', 'DIA_CORTE']:
                if col not in df_movs.columns: df_movs[col] = 0
            
            # GASTO es negativo, INGRESO es positivo
            df_movs['IMPORTE_REAL'] = df_movs.apply(
                lambda x: -x['IMPORTE'] if 'GASTO' in str(x['TIPO']).upper() else x['IMPORTE'], axis=1
            )
    except: df_movs = pd.DataFrame()

    # 2. Deudas y Calendario
    calendario = []
    alertas = []
    try:
        df_deudas = pd.DataFrame(sh.worksheet("Deudas").get_all_records())
        if not df_deudas.empty:
            for c in ['MONTO_TOTAL', 'ABONADO', 'PLAZO_MESES', 'DIA_CORTE', 'DIA_PAGO', 'INTERES_ORIGINAL']:
                if c in df_deudas.columns: df_deudas[c] = pd.to_numeric(df_deudas[c], errors='coerce').fillna(0)
            
            # Generar alertas visuales
            hoy = datetime.now().date()
            for idx, row in df_deudas.iterrows():
                if row['ESTADO'] != 'Activo': continue
                nombre = row['NOMBRE']
                dia_pago = int(row.get('DIA_PAGO', 1))
                prox_pago = calcular_fecha_inteligente(dia_pago)
                
                # Calcular monto a mostrar
                monto_cal = 0
                if "Tarjeta" in row['TIPO']:
                     if not df_movs.empty:
                        s = df_movs[df_movs['BANCO'] == nombre]['IMPORTE_REAL'].sum()
                        monto_cal = abs(s) if s < 0 else 0
                else:
                    total = row.get('MONTO_TOTAL', 0)
                    abonado = row.get('ABONADO', 0)
                    restante = total - abonado
                    meses = max(int(row.get('PLAZO_MESES', 1)), 1)
                    monto_cal = min(total/meses, restante)

                if prox_pago and monto_cal > 1:
                    dias = (prox_pago - hoy).days
                    # Solo mostrar "Me deben" o "Yo debo" si es deuda
                    tipo_cal = "Cobrar" if "Por Cobrar" in row['TIPO'] else "Pagar"
                    calendario.append({"Fecha": prox_pago, "Evento": f"{tipo_cal} {nombre}", "Monto": monto_cal})
                    
                    if 0 <= dias <= 5:
                        alertas.append(f"⚠️ {tipo_cal} **{nombre}** (${monto_cal:,.2f}) vence el {prox_pago.strftime('%d/%m')}")

    except: df_deudas = pd.DataFrame()

    # 3. Inversiones
    try:
        df_inv = pd.DataFrame(sh.worksheet("Inversiones").get_all_records())
        if not df_inv.empty: df_inv['MONTO_INICIAL'] = pd.to_numeric(df_inv['MONTO_INICIAL']).fillna(0)
    except: df_inv = pd.DataFrame()

    return df_movs, df_deudas, df_inv, calendario, alertas, sh

# ================= HERRAMIENTAS DE ARCHIVO =================
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

# 🚀 ACTIVAR MOTOR FINANCIERO
if not df_movs.empty:
    df_flujo_real = generar_flujo_real(df_movs)
else:
    df_flujo_real = pd.DataFrame()

# --- SIDEBAR: CENTRO DE MANDO ---
with st.sidebar:
    st.title("🎛️ Centro de Mando")
    
    # BOTÓN DE SINCRONIZACIÓN Y ALERTAS
    if st.button("🤖 Sincronizar y Alertas"):
        procesar_telegram(sh_obj, df_deudas)
        st.toast("Datos actualizados y alertas enviadas.")
        time.sleep(1)
        st.rerun()
    
    st.divider()
    
    # 1. CONFIGURAR CUENTAS
    with st.expander("⚙️ Configurar Cuenta"):
        with st.form("conf_cuenta"):
            cuentas = sorted(list(df_movs['BANCO'].unique())) if not df_movs.empty else []
            cta = st.selectbox("Cuenta", cuentas + ["Nueva..."])
            tipo = st.selectbox("Tipo", ["Tarjeta Crédito", "Préstamo", "Débito/Efectivo"])
            c1, c2 = st.columns(2)
            d_corte = c1.number_input("Día Corte", 0, 31, 0)
            d_pago = c2.number_input("Día Pago", 0, 31, 0)
            if st.form_submit_button("Guardar"):
                guardar_registro(sh_obj, "Deudas", [cta, tipo, 0, 1, d_corte, d_pago, 0, "Activo"])
                st.rerun()

    # 2. DEUDAS (YO DEBO / ME DEBEN)
    with st.expander("🤝 Deudas y Préstamos"):
        with st.form("new_debt"):
            quien = st.radio("Dirección", ["🔴 Yo Debo", "🟢 Me Deben"])
            nom = st.text_input("Nombre / Concepto")
            c1, c2 = st.columns(2)
            monto = c1.number_input("Monto Inicial", min_value=0.0)
            interes = c2.number_input("Interés (%)", 0.0)
            c3, c4 = st.columns(2)
            meses = c3.number_input("Plazo", 1, 60, 12)
            dia = c4.number_input("Día Pago", 1, 31, 15)
            
            total = monto * (1 + interes/100)
            st.caption(f"Total: ${total:,.2f}")
            
            if st.form_submit_button("Registrar"):
                tipo_int = "Por Cobrar" if "Me Deben" in quien else "Préstamo Fijo"
                guardar_registro(sh_obj, "Deudas", [nom, tipo_int, total, meses, 0, dia, 0, "Activo", interes])
                st.rerun()

    # 3. REGISTRAR MOVIMIENTOS (GASTOS, INGRESOS, PAGOS)
    with st.expander("📝 Registrar Movimiento"):
        # Esto soluciona tu duda de dónde poner el dinero que tienes
        tipo_mov = st.selectbox("Tipo de Movimiento", ["Gasto / Pago (-)", "Ingreso / Saldo Inicial (+)"])
        
        monto = st.number_input("Monto", 0.0, step=10.0)
        desc = st.text_input("Concepto (ej. Nomina, Super)")
        cuenta = st.selectbox("Cuenta Afectada", cuentas if cuentas else ["Efectivo"])
        
        # Opciones Avanzadas (Meses)
        es_msi = False
        plazo, int_extra = 1, 0.0
        
        if "Gasto" in tipo_mov:
            es_msi = st.checkbox("¿A Meses / Diferido?")
            if es_msi:
                c1, c2 = st.columns(2)
                plazo = c1.number_input("Meses", 2, 48, 3)
                int_extra = c2.number_input("Interés Extra %", 0.0)
                st.caption(f"Final: ${monto*(1+int_extra/100):,.2f}")

        if st.button("Guardar Movimiento"):
            # Detectar corte auto
            corte_auto = 0
            if not df_deudas.empty:
                try: 
                    row = df_deudas[df_deudas['NOMBRE'] == cuenta].iloc[0]
                    corte_auto = int(row.get('DIA_CORTE', 0))
                except: pass
            
            tipo_final = "Gasto" if "Gasto" in tipo_mov else "Ingreso"
            fecha = str(datetime.now().date())
            # Guardamos
            guardar_registro(sh_obj, "Hoja 1", ["Manual", fecha, desc, monto, "-", "-", tipo_final, cuenta, plazo, int_extra, corte_auto])
            st.success("Registrado.")
            time.sleep(1)
            st.rerun()

# --- ALERTAS VISIBLES ---
st.subheader(f"Hola, {st.secrets.get('admin_user','Admin')}")
if alertas:
    for a in alertas: st.error(a)

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "📅 Calendario", "📝 Bitácora", "💳 Carteras y Deudas"])

# TAB 1: DASHBOARD
with tab1:
    saldo = df_movs['IMPORTE_REAL'].sum() if not df_movs.empty else 0
    inv = df_inv['MONTO_INICIAL'].sum() if not df_inv.empty else 0
    
    hoy = datetime.now()
    gasto_mes = 0
    if not df_flujo_real.empty:
        mask = (df_flujo_real['FECHA'].dt.month == hoy.month) & (df_flujo_real['FECHA'].dt.year == hoy.year)
        gasto_mes = abs(df_flujo_real[mask & (df_flujo_real['IMPORTE_REAL'] < 0)]['IMPORTE_REAL'].sum())

    c1, c2, c3 = st.columns(3)
    c1.metric("💰 Liquidez Total", f"${saldo:,.2f}")
    c2.metric("📈 Inversiones", f"${inv:,.2f}")
    c3.metric("💸 Gastos Reales Mes", f"${gasto_mes:,.2f}", delta_color="inverse")

    if not df_flujo_real.empty:
        col1, col2 = st.columns(2)
        with col1:
            dm = df_flujo_real[(df_flujo_real['FECHA'].dt.month == hoy.month) & (df_flujo_real['IMPORTE_REAL'] < 0)].copy()
            if not dm.empty:
                dm['ABS'] = abs(dm['IMPORTE_REAL'])
                st.plotly_chart(px.pie(dm, values='ABS', names='CATEGORIA', hole=0.4, title="Gastos del Mes"), use_container_width=True)
        with col2:
            if not df_movs.empty:
                evo = df_movs.sort_values('FECHA').copy()
                evo['Acum'] = evo['IMPORTE_REAL'].cumsum()
                st.plotly_chart(px.line(evo, x='FECHA', y='Acum', title="Historia de Saldo"), use_container_width=True)

# TAB 2: CALENDARIO
with tab2:
    if calendario:
        cal = pd.DataFrame(calendario).sort_values("Fecha")
        for i, row in cal.iterrows():
            dias = (row['Fecha'] - hoy.date()).days
            col = "#ff4b4b" if dias <= 3 else "#2ecc71"
            with st.container():
                c1, c2, c3 = st.columns([1,3,2])
                c1.write(f"**{row['Fecha'].strftime('%d %b')}**")
                c2.markdown(f"<span style='color:{col}'>●</span> {row['Evento']}", unsafe_allow_html=True)
                c3.write(f"**${row['Monto']:,.2f}**")
                st.divider()

# TAB 3: BITÁCORA DETALLADA
with tab3:
    if not df_movs.empty:
        v = df_movs.sort_values('FECHA', ascending=False).head(100)
        st.dataframe(v[['FECHA','DESCRIPCION','IMPORTE_REAL','BANCO','TIPO']], use_container_width=True)
        st.download_button("📥 Excel", descargar_excel(v), "data.xlsx")
        
        # PDF Generator
        sel = st.selectbox("Generar Recibo de:", v.index, format_func=lambda x: f"{v.loc[x,'DESCRIPCION']} (${v.loc[x,'IMPORTE']})")
        if st.button("🖨️ PDF"):
            r = v.loc[sel]
            b64 = base64.b64encode(generar_pdf(str(r['FECHA']), r['BANCO'], r['IMPORTE'], r['DESCRIPCION'])).decode()
            st.markdown(f'<a href="data:application/pdf;base64,{b64}" download="recibo.pdf">Descargar PDF</a>', unsafe_allow_html=True)

# TAB 4: DEUDAS Y COBROS (FLEXIBLE)
with tab4:
    # --- A. CUENTAS POR COBRAR (ME DEBEN) ---
    st.subheader("🟢 Cuentas por Cobrar (Activos)")
    if not df_deudas.empty:
        # Filtro: Lo que es 'Por Cobrar' y está Activo
        cobros = df_deudas[(df_deudas['TIPO'] == 'Por Cobrar') & (df_deudas['ESTADO'] == 'Activo')]
        
        if not cobros.empty:
            for i, row in cobros.iterrows():
                nombre = row['NOMBRE']
                total = row.get('MONTO_TOTAL', 0)
                abonado = row.get('ABONADO', 0)
                pendiente = total - abonado
                
                # Calculamos la cuota sugerida (mensualidad)
                plazo = max(int(row.get('PLAZO_MESES', 1)), 1)
                sugerido = pendiente / max((plazo - (abonado / (total/plazo) if total > 0 else 0)), 1)
                if sugerido > pendiente: sugerido = pendiente
                
                with st.container():
                    col_info, col_action = st.columns([2, 1])
                    
                    with col_info:
                        st.markdown(f"**👤 {nombre}**")
                        st.progress(min(abonado/total, 1.0) if total > 0 else 0)
                        k1, k2 = st.columns(2)
                        k1.caption(f"Debe Total: ${total:,.2f}")
                        k2.metric("Pendiente", f"${pendiente:,.2f}")

                    with col_action:
                        # CAJA FLEXIBLE: El usuario decide cuánto le pagaron
                        monto_recibido = st.number_input("Monto Recibido", min_value=0.0, max_value=float(pendiente), value=float(sugerido), key=f"rec_{i}")
                        
                        if st.button("✅ Registrar Cobro", key=f"btn_c_{i}"):
                            if monto_recibido > 0:
                                try:
                                    # 1. Actualizar Deuda en Sheet
                                    cell = sh_obj.worksheet("Deudas").find(nombre)
                                    sh_obj.worksheet("Deudas").update_cell(cell.row, 7, abonado + monto_recibido)
                                    
                                    # 2. Registrar el Ingreso en Flujo (Hoja 1) para que suba tu saldo
                                    hoy_str = str(datetime.now().date())
                                    sh_obj.worksheet("Hoja 1").append_row(
                                        ["Auto", hoy_str, f"Cobro a {nombre}", monto_recibido, "-", "-", "Ingreso", "Efectivo", 1, 0, 0]
                                    )
                                    
                                    st.toast(f"¡Genial! Cobraste ${monto_recibido:,.2f}")
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error: {e}")
                    st.divider()
        else:
            st.info("Nadie te debe dinero actualmente.")

    # --- B. MIS DEUDAS (YO DEBO) ---
    st.subheader("🔴 Mis Deudas (Pasivos)")
    if not df_deudas.empty:
        deudas = df_deudas[(df_deudas['TIPO'] != 'Por Cobrar') & (df_deudas['ESTADO'] == 'Activo')]
        
        for i, row in deudas.iterrows():
            nombre = row['NOMBRE']
            
            with st.container():
                # Encabezado
                tipo_icono = "💳" if "Tarjeta" in row['TIPO'] else "🏦"
                st.markdown(f"#### {tipo_icono} {nombre}")

                # Lógica Tarjeta vs Préstamo
                es_tarjeta = "Tarjeta" in row['TIPO']
                saldo_pendiente = 0
                
                if es_tarjeta:
                    # Para tarjeta, la deuda es la suma de gastos en el historial
                    saldo_real = 0
                    if not df_movs.empty:
                        saldo_real = df_movs[df_movs['BANCO'] == nombre]['IMPORTE_REAL'].sum()
                    saldo_pendiente = abs(saldo_real) if saldo_real < 0 else 0
                    st.caption(f"Corte: Día {int(row.get('DIA_CORTE',0))} | Pagar antes del: Día {int(row.get('DIA_PAGO',0))}")
                else:
                    # Para préstamo, es Total - Abonado
                    total = row.get('MONTO_TOTAL', 0)
                    abonado = row.get('ABONADO', 0)
                    saldo_pendiente = total - abonado
                
                # Columnas de Acción
                c_izq, c_der = st.columns([1, 1])
                
                with c_izq:
                    st.metric("Deuda Actual", f"${saldo_pendiente:,.2f}")
                    if saldo_pendiente > 0:
                        # Barra de progreso inversa (mientras más pagas, menos roja)
                        if not es_tarjeta and total > 0:
                            st.progress(min(abonado/total, 1.0))
                
                with c_der:
                    # --- AQUÍ ESTÁ LA SOLUCIÓN A TU DUDA ---
                    # Calculamos un sugerido, pero TÚ lo puedes cambiar
                    if es_tarjeta:
                        sugerido = saldo_pendiente # En tarjeta sueles querer pagar todo para no generar intereses
                    else:
                        # En préstamos, sugerimos la mensualidad
                        plazo = max(int(row.get('PLAZO_MESES', 1)), 1)
                        sugerido = row.get('MONTO_TOTAL', 0) / plazo
                    
                    # Input Manual: Aquí pones 20, 50, o todo lo que quieras
                    pago_manual = st.number_input("Monto a Pagar", min_value=0.0, value=float(min(sugerido, saldo_pendiente)), key=f"pay_in_{i}")
                    
                    if st.button("💸 Realizar Pago", key=f"pay_btn_{i}"):
                        if pago_manual > 0:
                            try:
                                # 1. Registrar el Gasto en Historial (Resta dinero de tu liquidez)
                                hoy_str = str(datetime.now().date())
                                # Si es tarjeta, es un 'Pago' a la cuenta. Si es préstamo, es gasto.
                                concepto_pago = f"Pago Tarjeta {nombre}" if es_tarjeta else f"Abono {nombre}"
                                sh_obj.worksheet("Hoja 1").append_row(
                                    ["Auto", hoy_str, concepto_pago, pago_manual, "-", "-", "Pago", nombre if es_tarjeta else "Efectivo", 1, 0, 0]
                                )
                                
                                # 2. Si es PRÉSTAMO, actualizamos el 'Abonado' en la hoja Deudas
                                # (Las tarjetas se actualizan solas al registrar el gasto en Hoja 1)
                                if not es_tarjeta:
                                    cell = sh_obj.worksheet("Deudas").find(nombre)
                                    nuevo_abonado = row.get('ABONADO', 0) + pago_manual
                                    sh_obj.worksheet("Deudas").update_cell(cell.row, 7, nuevo_abonado)

                                st.success(f"Pago de ${pago_manual:,.2f} registrado.")
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")
                
                st.divider()
    else:
        st.info("No tienes deudas activas registradas.")
