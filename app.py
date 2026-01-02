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
            # Si no hay fecha válida, saltamos
            if pd.isna(pd.to_datetime(row['FECHA'], errors='coerce')): continue

            fecha_compra = pd.to_datetime(row['FECHA'], dayfirst=True)
            monto_original = abs(float(str(row['IMPORTE']).replace(',','')))
            
            # Datos de configuración (con valores por defecto seguros)
            # Intentamos leer el plazo, si falla ponemos 1
            try: plazo = int(float(str(row['PLAZO_MESES']))) 
            except: plazo = 1
            if plazo < 1: plazo = 1

            # Intentamos leer el interés, si falla ponemos 0
            try: interes_pct = float(str(row['INTERES']).replace('%','')) 
            except: interes_pct = 0.0

            # Intentamos leer el día de corte
            try: dia_corte = int(float(str(row['DIA_CORTE']))) 
            except: dia_corte = 0
            
            # --- CÁLCULO MATEMÁTICO ---
            # Monto total aumenta si hay interés (ej. Préstamo o Compra con Interés)
            monto_total = monto_original * (1 + (interes_pct / 100))
            pago_mensual = monto_total / plazo
            
            # --- LÓGICA DE TIEMPO (CORTE DE TARJETA) ---
            fecha_inicio = fecha_compra
            # Si hay día de corte y la compra fue DESPUÉS del corte, el pago empieza al siguiente mes
            if dia_corte > 0 and fecha_compra.day > dia_corte:
                fecha_inicio = fecha_compra + relativedelta(months=1)

            # Generar los 'clones' para cada mes
            for i in range(plazo):
                fecha_pago = fecha_inicio + relativedelta(months=i)
                
                # Etiqueta para saber en qué pago vas (ej: "TV Samsung (2/12)")
                desc_extra = f" ({i+1}/{plazo})" if plazo > 1 else ""
                
                pagos_proyectados.append({
                    'FECHA': fecha_pago,
                    'DESCRIPCION': f"{row['DESCRIPCION']}{desc_extra}",
                    'IMPORTE': pago_mensual,
                    # Para gastos usamos negativo, para ingresos positivo
                    'IMPORTE_REAL': -pago_mensual if 'Gasto' in str(row.get('TIPO','Gasto')) else pago_mensual,
                    'CATEGORIA': str(row['DESCRIPCION']).split()[0], 
                    'TIPO_FLUJO': 'Diferido' if plazo > 1 else 'Contado'
                })
        except Exception as e: 
            continue # Si una fila falla, la ignoramos y seguimos

    return pd.DataFrame(pagos_proyectados)
# ================= CONEXIÓN GOOGLE =================
def conectar_google():
    try:
        # Intenta usar credenciales seguras de Streamlit Cloud
        if 'credenciales_seguras' in st.secrets:
            b64 = st.secrets['credenciales_seguras']
            creds = json.loads(base64.b64decode(b64).decode('utf-8'))
            gc = gspread.service_account_from_dict(creds)
        else:
            # Si no, busca el archivo local
            gc = gspread.service_account(filename='credentials.json')
        return gc.open("BaseDatos_Maestra")
    except Exception as e:
        st.error(f"Error conexión Google: {e}")
        st.stop()

# ================= CARGA DE DATOS MAESTRA =================
@st.cache_data(ttl=5)
def cargar_datos_master():
    sh = conectar_google()
    
    # 1. Movimientos (Historial Completo)
    try:
        # Leemos todo como texto primero para evitar errores de formato
        df_movs = pd.DataFrame(sh.sheet1.get_all_records()).astype(str)
        if not df_movs.empty:
            # Limpieza y conversión de columnas clave
            df_movs['IMPORTE'] = pd.to_numeric(df_movs['IMPORTE'], errors='coerce').fillna(0).abs()
            df_movs['FECHA'] = pd.to_datetime(df_movs['FECHA'], errors='coerce', dayfirst=True)
            
            # Columnas nuevas (si no existen, se crean con 0)
            if 'PLAZO_MESES' not in df_movs.columns: df_movs['PLAZO_MESES'] = 1
            if 'INTERES' not in df_movs.columns: df_movs['INTERES'] = 0
            if 'DIA_CORTE' not in df_movs.columns: df_movs['DIA_CORTE'] = 0
            
            # Cálculo de Importe Real (Negativo para gastos)
            df_movs['IMPORTE_REAL'] = df_movs.apply(
                lambda x: -x['IMPORTE'] if 'GASTO' in str(x['TIPO']).upper() else x['IMPORTE'], axis=1
            )
    except: df_movs = pd.DataFrame()

    # 2. Deudas y Configuración
    calendario = []
    alertas = []
    
    try:
        df_deudas = pd.DataFrame(sh.worksheet("Deudas").get_all_records())
        if not df_deudas.empty:
            cols_num = ['MONTO_TOTAL', 'ABONADO', 'PLAZO_MESES', 'DIA_CORTE', 'DIA_PAGO', 'INTERES_ORIGINAL']
            for c in cols_num:
                if c in df_deudas.columns:
                    df_deudas[c] = pd.to_numeric(df_deudas[c], errors='coerce').fillna(0)

            # PROCESAMIENTO INTELIGENTE DE DEUDAS
            for idx, row in df_deudas.iterrows():
                if row['ESTADO'] != 'Activo': continue
                
                nombre = row['NOMBRE']
                tipo = row['TIPO']
                dia_pago = int(row.get('DIA_PAGO', 1))
                
                # A) TARJETAS (Deuda Fluctuante)
                if "Tarjeta" in tipo or "Crédito" in tipo:
                    saldo_real = 0
                    if not df_movs.empty:
                        # Sumamos lo gastado (negativo) y pagado (positivo)
                        saldo_real = df_movs[df_movs['BANCO'] == nombre]['IMPORTE_REAL'].sum()
                    
                    deuda_actual = abs(saldo_real) if saldo_real < 0 else 0
                    
                    prox_pago = calcular_fecha_inteligente(dia_pago)
                    if prox_pago and deuda_actual > 1: # Solo avisa si debes más de 1 peso
                        dias = (prox_pago - datetime.now().date()).days
                        calendario.append({"Fecha": prox_pago, "Evento": f"Pago {nombre}", "Monto": deuda_actual, "Tipo": "Tarjeta"})
                        if 0 <= dias <= 5:
                            alertas.append(f"💳 **{nombre}**: Pagar ${deuda_actual:,.2f} antes del {prox_pago.strftime('%d/%m')}")

                # B) PRÉSTAMOS (Fijos)
                else:
                    total = row.get('MONTO_TOTAL', 0)
                    abonado = row.get('ABONADO', 0)
                    meses = max(int(row.get('PLAZO_MESES', 1)), 1)
                    
                    restante = total - abonado
                    mensualidad = total / meses
                    pago_sugerido = min(mensualidad, restante)
                    
                    prox_pago = calcular_fecha_inteligente(dia_pago)
                    if prox_pago and restante > 1:
                        dias = (prox_pago - datetime.now().date()).days
                        calendario.append({"Fecha": prox_pago, "Evento": f"Mensualidad {nombre}", "Monto": pago_sugerido, "Tipo": "Prestamo"})
                        if 0 <= dias <= 5:
                            alertas.append(f"🏦 **{nombre}**: Mensualidad de ${pago_sugerido:,.2f} vence el {prox_pago.strftime('%d/%m')}")
                            
    except Exception as e:
        df_deudas = pd.DataFrame()
        # st.error(f"Nota: Revisa hoja Deudas ({e})") # Opcional silenciar error

    # 3. Inversiones
    try:
        df_inv = pd.DataFrame(sh.worksheet("Inversiones").get_all_records())
        if not df_inv.empty:
            df_inv['MONTO_INICIAL'] = pd.to_numeric(df_inv['MONTO_INICIAL']).fillna(0)
    except: df_inv = pd.DataFrame()

    return df_movs, df_deudas, df_inv, calendario, alertas, sh

# ================= HERRAMIENTAS V3 (PDF/Excel/Guardar) =================
def generar_pdf(fecha, cuenta, monto, concepto):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "COMPROBANTE DE GASTO", ln=1, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Fecha: {fecha}", ln=1)
    pdf.cell(0, 10, f"Cuenta: {cuenta}", ln=1)
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
    except Exception as e:
        st.error(f"Error guardando: {e}")
        return False

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
            if len(txt) >= 2:
                # Formato simple: "50 tacos" o "gasto 50 tacos"
                try:
                    # Detectar si el primer elemento es numero
                    if txt[0].replace('.','',1).isdigit():
                        monto = float(txt[0])
                        desc = " ".join(txt[1:])
                        tipo = "Gasto"
                    else:
                        monto = float(txt[1])
                        desc = " ".join(txt[2:])
                        tipo = "Pago" if "pago" in txt[0] else "Gasto"
                    
                    hoy = datetime.now().strftime("%Y-%m-%d")
                    # Guardamos por defecto en Efectivo si viene de Telegram rápido
                    guardar_registro(sh, "Hoja 1", ["Telegram", hoy, desc, monto, "-", "-", tipo, "Efectivo", 1, 0, 0])
                    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": MY_ID, "text": f"✅ {tipo} de ${monto} anotado."})
                except: pass
    except: pass
# ================= INTERFAZ PRINCIPAL =================
# 1. Cargar Datos Maestros
df_movs, df_deudas, df_inv, calendario, alertas, sh_obj = cargar_datos_master()

# 2. 🚀 ACTIVAR MOTOR INTELIGENTE
# Generamos el flujo real (desglosando MSI y aplicando intereses)
if not df_movs.empty:
    df_flujo_real = generar_flujo_real(df_movs)
else:
    df_flujo_real = pd.DataFrame()

# --- SIDEBAR: CENTRO DE MANDO ---
with st.sidebar:
    st.title("🎛️ Centro de Mando")
    
    if st.button("🤖 Sincronizar Telegram"):
        procesar_telegram(sh_obj)
        st.rerun()
    
    st.divider()
    
    # 1. ACTUALIZAR CUENTAS (Configuración)
    with st.expander("⚙️ Configurar Cuenta/Tarjeta"):
        st.info("Define fechas para activar las alertas.")
        with st.form("config_cuenta"):
            cuentas_existentes = sorted(list(df_movs['BANCO'].unique())) if not df_movs.empty else []
            cta_sel = st.selectbox("Selecciona Cuenta", cuentas_existentes + ["Nueva..."])
            c_tipo = st.selectbox("Tipo", ["Tarjeta Crédito (Fluctuante)", "Préstamo Fijo", "Débito/Efectivo"])
            
            st.caption("Fechas Clave:")
            col_a, col_b = st.columns(2)
            dia_corte = col_a.number_input("Día Corte", 0, 31, 0)
            dia_pago = col_b.number_input("Día Pago", 0, 31, 0)
            
            if st.form_submit_button("Guardar Configuración"):
                # Guarda: Nombre, Tipo, Total(0), Plazo(1), Corte, Pago, Abonado(0), Estado
                guardar_registro(sh_obj, "Deudas", [cta_sel, c_tipo, 0, 1, dia_corte, dia_pago, 0, "Activo"])
                st.toast("Configuración guardada.")
                time.sleep(1)
                st.rerun()

    # 2. NUEVO PRÉSTAMO (Con Interés Global)
    with st.expander("🤝 Nuevo Préstamo / Deuda"):
        with st.form("nuevo_prestamo"):
            p_nom = st.text_input("Nombre (ej: Préstamo Coche)")
            p_monto_solicitado = st.number_input("Dinero Recibido", min_value=0.0)
            
            # Inputs de Interés y Plazo
            c1, c2 = st.columns(2)
            p_interes = c1.number_input("Interés Total (%)", min_value=0.0, value=0.0, help="Ej: 15 para 15%")
            p_meses = c2.number_input("Plazo (Meses)", min_value=1, value=12)
            p_dia = st.number_input("Día de Pago", 1, 31, 5)
            
            # Cálculo de la deuda real
            deuda_total = p_monto_solicitado * (1 + (p_interes / 100))
            mensualidad = deuda_total / p_meses if p_meses > 0 else 0
            
            st.markdown(f"---")
            st.markdown(f"**Deuda Final:** ${deuda_total:,.2f}")
            st.markdown(f"**Mensualidad:** ${mensualidad:,.2f}")
            
            if st.form_submit_button("Registrar Deuda"):
                # Guarda: ..., TotalReal, Plazo, Corte, Pago, Abonado, Estado, InteresOriginal
                guardar_registro(sh_obj, "Deudas", [p_nom, "Préstamo Fijo", deuda_total, p_meses, 0, p_dia, 0, "Activo", p_interes])
                st.rerun()

    # 3. GASTO RÁPIDO (Con MSI y Detección de Corte)
    with st.expander("💸 Gasto Rápido / Tarjetazo"):
        with st.form("fast_gasto"):
            g_monto = st.number_input("Monto", min_value=0.0, step=0.01)
            g_desc = st.text_input("Concepto")
            g_cta = st.selectbox("Cuenta", cuentas_existentes if cuentas_existentes else ["Efectivo"])
            
            # Opción para diferir pagos
            es_a_meses = st.checkbox("¿A Meses / Diferido?")
            if es_a_meses:
                c_m1, c_m2 = st.columns(2)
                g_plazo = c_m1.number_input("Meses", 2, 48, 3)
                g_interes = c_m2.number_input("Interés Extra (%)", 0.0, 100.0, 0.0)
            else:
                g_plazo = 1
                g_interes = 0.0
                
            if st.form_submit_button("Guardar"):
                # A. Detectar Día de Corte de la tarjeta seleccionada
                dia_corte_auto = 0
                try:
                    if not df_deudas.empty:
                        # Filtramos la tarjeta
                        row_cta = df_deudas[df_deudas['NOMBRE'] == g_cta]
                        if not row_cta.empty:
                             # Intentamos leer la columna DIA_CORTE (o por indice 4 si los nombres fallan)
                             val = row_cta.iloc[0]['DIA_CORTE'] if 'DIA_CORTE' in row_cta.columns else row_cta.iloc[0, 4]
                             dia_corte_auto = int(val)
                except: pass
                
                # B. Guardar (Agregando Plazo, Interés y Corte al final)
                # Hoja 1: Manual, Fecha, Desc, Importe, -, -, Gasto, Banco, PLAZO, INTERES, DIA_CORTE
                fecha_hoy = str(datetime.now().date())
                datos = ["Manual", fecha_hoy, g_desc, g_monto, "-", "-", "Gasto", g_cta, g_plazo, g_interes, dia_corte_auto]
                
                guardar_registro(sh_obj, "Hoja 1", datos)
                st.success("✅ Gasto registrado.")
                time.sleep(1)
                st.rerun()

# --- MOSTRAR ALERTAS ---
st.subheader(f"Bienvenido, {st.secrets.get('admin_user','Admin')}")
if alertas:
    for a in alertas: st.error(a)
# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard Visual", "📅 Calendario", "📝 Gestión Detallada", "💳 Deudas & Tarjetas"])

# TAB 1: DASHBOARD VISUAL (INTELIGENTE)
with tab1:
    # A. MÉTRICAS PRINCIPALES
    # Saldo Real: Usamos df_movs porque es lo que realmente hay en el banco hoy
    saldo = df_movs['IMPORTE_REAL'].sum() if not df_movs.empty else 0
    inv = df_inv['MONTO_INICIAL'].sum() if not df_inv.empty else 0
    
    # Gastos Reales del Mes (Aquí usamos el flujo proyectado con MSI)
    hoy = datetime.now()
    gastos_mes_real = 0
    if not df_flujo_real.empty:
        # Filtramos cuotas que caen en ESTE mes/año
        mask_mes = (df_flujo_real['FECHA'].dt.month == hoy.month) & (df_flujo_real['FECHA'].dt.year == hoy.year)
        # Sumamos solo gastos (negativos)
        gastos_mes_real = abs(df_flujo_real[mask_mes & (df_flujo_real['IMPORTE_REAL'] < 0)]['IMPORTE_REAL'].sum())

    col1, col2, col3 = st.columns(3)
    col1.metric("💰 Liquidez Total", f"${saldo:,.2f}", help="Dinero real en cuentas")
    col2.metric("📈 En Inversiones", f"${inv:,.2f}")
    col3.metric("💸 Gastos de Este Mes", f"${gastos_mes_real:,.2f}", delta_color="inverse", help="Incluye cuotas de MSI que tocan este mes")

    # B. GRÁFICOS
    if not df_flujo_real.empty:
        c_g1, c_g2 = st.columns(2)
        
        with c_g1:
            st.markdown("##### 🍰 Distribución de Gastos (Cuotas incluidas)")
            # Filtramos datos de este mes para el pastel
            datos_mes = df_flujo_real[
                (df_flujo_real['FECHA'].dt.month == hoy.month) & 
                (df_flujo_real['FECHA'].dt.year == hoy.year) &
                (df_flujo_real['IMPORTE_REAL'] < 0)
            ].copy()
            
            if not datos_mes.empty:
                datos_mes['IMPORTE'] = abs(datos_mes['IMPORTE_REAL']) # Positivo para el gráfico
                fig = px.pie(datos_mes, values='IMPORTE', names='CATEGORIA', hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No hay gastos registrados para este mes.")

        with c_g2:
            st.markdown("##### 📉 Evolución de Saldo (Histórica)")
            # Para la linea de tiempo usamos df_movs (la realidad bancaria)
            if not df_movs.empty:
                df_evo = df_movs.sort_values('FECHA').copy()
                df_evo['Saldo Acumulado'] = df_evo['IMPORTE_REAL'].cumsum()
                fig2 = px.line(df_evo, x='FECHA', y='Saldo Acumulado')
                st.plotly_chart(fig2, use_container_width=True)

# TAB 2: CALENDARIO DE PAGOS
with tab2:
    if calendario:
        st.caption("Próximos pagos detectados automáticamente:")
        df_cal = pd.DataFrame(calendario).sort_values("Fecha")
        
        for i, row in df_cal.iterrows():
            dias_restantes = (row['Fecha'] - hoy.date()).days
            # Colores semáforo
            if dias_restantes < 0: color = "#9e9e9e" # Vencido/Pasado
            elif dias_restantes <= 3: color = "#ff4b4b" # Rojo (Urgente)
            elif dias_restantes <= 7: color = "#ffa726" # Naranja (Pronto)
            else: color = "#2ecc71" # Verde (Lejos)
            
            with st.container():
                c1, c2, c3 = st.columns([1,3,2])
                c1.write(f"**{row['Fecha'].strftime('%d %b')}**")
                c2.markdown(f"<span style='color:{color}'>●</span> {row['Evento']}", unsafe_allow_html=True)
                c3.write(f"**${row['Monto']:,.2f}**")
                st.divider()
    else:
        st.info("No hay pagos próximos detectados. Configura tus Fechas de Corte/Pago en el menú lateral.")

# TAB 3: GESTIÓN DETALLADA (Tabla + PDF/Excel)
with tab3:
    st.markdown("### 📝 Bitácora de Movimientos")
    
    col_d1, col_d2 = st.columns([3,1])
    with col_d1:
        f_ini = st.date_input("Desde", date(hoy.year, 1, 1))
    with col_d2:
        f_fin = st.date_input("Hasta", hoy)

    if not df_movs.empty:
        # Filtro de fechas
        mask = (df_movs['FECHA'].dt.date >= f_ini) & (df_movs['FECHA'].dt.date <= f_fin)
        df_view = df_movs.loc[mask].sort_values('FECHA', ascending=False)
        
        # Mostrar tabla limpia
        cols_ver = ['FECHA', 'DESCRIPCION', 'IMPORTE', 'TIPO', 'BANCO', 'PLAZO_MESES']
        # Solo mostramos columnas que existan
        cols_final = [c for c in cols_ver if c in df_view.columns]
        st.dataframe(df_view[cols_final], use_container_width=True)
        
        # Botones de Acción
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1:
            st.download_button("📥 Descargar Excel Completo", descargar_excel(df_view), "Finanzas_Master.xlsx")
        with c_btn2:
            # Selector para PDF
            if not df_view.empty:
                opcion = st.selectbox("Selecciona movimiento para Recibo PDF:", df_view.index, format_func=lambda x: f"{df_view.loc[x,'DESCRIPCION']} (${df_view.loc[x,'IMPORTE']})")
                if st.button("🖨️ Generar PDF"):
                    r = df_view.loc[opcion]
                    pdf_data = generar_pdf(str(r['FECHA'].date()), r['BANCO'], r['IMPORTE'], r['DESCRIPCION'])
                    b64 = base64.b64encode(pdf_data).decode()
                    st.markdown(f'<a href="data:application/pdf;base64,{b64}" download="Recibo_{r["FECHA"].date()}.pdf">⬇️ Descargar PDF Listo</a>', unsafe_allow_html=True)

# TAB 4: DEUDAS Y TARJETAS
with tab4:
    st.subheader("💳 Estado de Deudas")
    if not df_deudas.empty:
        for idx, row in df_deudas.iterrows():
            if row['ESTADO'] == 'Activo':
                nombre = row['NOMBRE']
                
                # Diseño de Tarjeta
                with st.container():
                    st.markdown(f"#### {nombre} ({row['TIPO']})")
                    
                    # 1. TARJETAS (Fluctuante)
                    if "Tarjeta" in row['TIPO']:
                        saldo_card = 0
                        if not df_movs.empty:
                            saldo_card = df_movs[df_movs['BANCO'] == nombre]['IMPORTE_REAL'].sum()
                        deuda = abs(saldo_card) if saldo_card < 0 else 0
                        
                        c1, c2 = st.columns(2)
                        c1.metric("Deuda al corte", f"${deuda:,.2f}")
                        c2.caption(f"Corte: Día {int(row.get('DIA_CORTE',0))} | Pago: Día {int(row.get('DIA_PAGO',0))}")
                        st.progress(min(deuda/20000, 1.0)) # Barra visual (ej. tope 20k)
                    
                    # 2. PRÉSTAMOS (Fijos)
                    else:
                        total = row.get('MONTO_TOTAL', 0)
                        abonado = row.get('ABONADO', 0)
                        pendiente = total - abonado
                        interes = row.get('INTERES_ORIGINAL', 0)
                        
                        k1, k2, k3 = st.columns(3)
                        k1.metric("Total Deuda", f"${total:,.2f}", help=f"Incluye {interes}% de interés")
                        k2.metric("Pagado", f"${abonado:,.2f}")
                        k3.metric("Pendiente", f"${pendiente:,.2f}", delta_color="inverse")
                        
                        if total > 0:
                            st.progress(min(abonado/total, 1.0))
                        
                        # Botón para registrar abono rápido
                        plazo = max(int(row.get('PLAZO_MESES', 1)), 1)
                        cuota = total / plazo
                        if st.button(f"Pagar Mensualidad (${cuota:,.2f})", key=f"pay_{idx}"):
                            # Sumar al abonado en Google Sheets
                            nuevo_abono = abonado + cuota
                            # Columna 7 es ABONADO en tu estructura estándar (verificar en tu sheet si cambia)
                            try:
                                # Buscamos la celda exacta para ser precisos
                                cell = sh_obj.worksheet("Deudas").find(nombre)
                                sh_obj.worksheet("Deudas").update_cell(cell.row, 7, nuevo_abono) 
                                st.toast("✅ Abono registrado correctamente")
                                time.sleep(1)
                                st.rerun()
                            except:
                                st.error("No se pudo sincronizar el pago.")
                    
                    st.divider()
    else:
        st.info("No hay deudas activas. Agrega una en el menú lateral.")
