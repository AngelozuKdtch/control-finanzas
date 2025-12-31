import streamlit as st
import pandas as pd
import gspread
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date
import calendar
from fpdf import FPDF
import base64
from io import BytesIO
import json
import time

# ================= CONFIGURACIÓN =================
st.set_page_config(page_title="Control Financiero Pro", page_icon="📈", layout="wide")

# ================= 🔒 SISTEMA DE LOGIN =================
def check_password():
    if st.session_state.get('password_correct', False):
        return True
    
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("### 🔐 Acceso al Portafolio")
        with st.form("login_form"):
            user = st.text_input("Usuario")
            pwd = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Ingresar"):
                if user == st.secrets["admin_user"] and pwd == st.secrets["admin_pass"]:
                    st.session_state['password_correct'] = True
                    st.rerun()
                else:
                    st.error("❌ Datos incorrectos")
    return False

if not check_password():
    st.stop()

# ================= CONEXIÓN GOOGLE =================
def conectar_google(nombre_hoja="BaseDatos_Maestra"):
    try:
        if 'credenciales_seguras' in st.secrets:
            b64 = st.secrets['credenciales_seguras']
            creds = json.loads(base64.b64decode(b64).decode('utf-8'))
            gc = gspread.service_account_from_dict(creds)
        else:
            gc = gspread.service_account(filename='credentials.json')
            
        sh = gc.open(nombre_hoja)
        return sh
    except Exception as e:
        st.error(f"Error conexión: {e}")
        st.stop()

# ================= FUNCIONES DE CÁLCULO =================
@st.cache_data(ttl=5)
def cargar_datos_generales():
    sh = conectar_google()
    
    # 1. Cargar Movimientos (Gastos/Ingresos)
    ws_movs = sh.sheet1
    df_movs = pd.DataFrame(ws_movs.get_all_records()).astype(str)
    
    if not df_movs.empty:
        if 'IMPORTE' in df_movs.columns:
            df_movs['IMPORTE'] = pd.to_numeric(df_movs['IMPORTE'], errors='coerce').fillna(0).abs()
        
        # Signos
        if 'TIPO' in df_movs.columns:
            mask_gasto = df_movs['TIPO'].str.upper().str.contains('GASTO')
            df_movs['IMPORTE_REAL'] = df_movs['IMPORTE']
            df_movs.loc[mask_gasto, 'IMPORTE_REAL'] *= -1
        else:
            df_movs['IMPORTE_REAL'] = df_movs['IMPORTE'] * -1
            
        if 'FECHA' in df_movs.columns:
            df_movs['FECHA'] = pd.to_datetime(df_movs['FECHA'], errors='coerce', dayfirst=True)
            df_movs = df_movs.dropna(subset=['FECHA'])

    # 2. Cargar Inversiones (Nueva Hoja)
    try:
        ws_inv = sh.worksheet("Inversiones")
        df_inv = pd.DataFrame(ws_inv.get_all_records())
        if not df_inv.empty:
            # Convertir columnas numéricas
            cols_num = ['MONTO_INICIAL', 'TASA_ANUAL']
            for col in cols_num:
                df_inv[col] = pd.to_numeric(df_inv[col], errors='coerce').fillna(0)
            
            # Fechas
            df_inv['FECHA_INICIO'] = pd.to_datetime(df_inv['FECHA_INICIO'], errors='coerce', dayfirst=True)
            if 'FECHA_FIN' in df_inv.columns:
                df_inv['FECHA_FIN'] = pd.to_datetime(df_inv['FECHA_FIN'], errors='coerce', dayfirst=True)
            
    except:
        df_inv = pd.DataFrame() # Si no existe la hoja aún
        
    return df_movs, df_inv, sh

def calcular_rendimiento_actual(row):
    """Calcula cuánto vale HOY una inversión basada en interés compuesto diario"""
    hoy = datetime.now()
    dias_transcurridos = (hoy - row['FECHA_INICIO']).days
    
    if dias_transcurridos < 0: return row['MONTO_INICIAL'] # Fecha futura
    
    # Fórmula: Monto * (1 + (TasaAnual/365)) ^ Dias
    tasa_diaria = (row['TASA_ANUAL'] / 100) / 365
    valor_actual = row['MONTO_INICIAL'] * ((1 + tasa_diaria) ** dias_transcurridos)
    return valor_actual

def guardar_inversion_nueva(sh, plataforma, producto, fecha_ini, monto, tasa, fecha_fin):
    try:
        ws = sh.worksheet("Inversiones")
    except:
        # Si no existe, la creamos con encabezados
        ws = sh.add_worksheet(title="Inversiones", rows=100, cols=10)
        ws.append_row(["PLATAFORMA", "PRODUCTO", "FECHA_INICIO", "MONTO_INICIAL", "TASA_ANUAL", "FECHA_FIN"])
    
    f_ini_str = fecha_ini.strftime("%Y-%m-%d")
    f_fin_str = fecha_fin.strftime("%Y-%m-%d") if fecha_fin else ""
    
    ws.append_row([plataforma, producto, f_ini_str, monto, tasa, f_fin_str])

# ================= INTERFAZ =================
st.title("📈 Portafolio de Inversiones & Gastos")
st.caption(f"Bienvenido, {st.secrets['admin_user']}")

df_movs, df_inv, sh_obj = cargar_datos_generales()

# --- SIDEBAR (CONTROLES RÁPIDOS) ---
st.sidebar.header("🕹️ Acciones")

with st.sidebar.expander("💰 Nueva Inversión", expanded=False):
    with st.form("new_inv"):
        i_plat = st.selectbox("Plataforma", ["Nu", "Cetes", "GBM", "Mercado Pago", "Banco", "Otro"])
        i_prod = st.text_input("Producto (Ej: Cajita, Bonddia)")
        i_monto = st.number_input("Monto Inicial ($)", min_value=0.0)
        i_tasa = st.number_input("Tasa Anual (%)", min_value=0.0, value=15.0)
        i_fecha = st.date_input("Fecha Inicio", datetime.now())
        i_meta = st.date_input("Fecha Meta (Fin)", datetime.now() + timedelta(days=365))
        
        if st.form_submit_button("Registrar Inversión"):
            guardar_inversion_nueva(sh_obj, i_plat, i_prod, i_fecha, i_monto, i_tasa, i_meta)
            st.toast("Inversión registrada. Recargando...", icon="🚀")
            time.sleep(1)
            st.cache_data.clear()
            st.rerun()

# --- CÁLCULOS EN TIEMPO REAL ---
total_bancos = 0
total_inversiones = 0
ganancia_interes = 0

# 1. Saldo en Cuentas (Líquido)
if not df_movs.empty:
    total_bancos = df_movs['IMPORTE_REAL'].sum()

# 2. Valor Inversiones (Calculado al segundo actual)
if not df_inv.empty:
    # Aplicamos la función fila por fila
    df_inv['VALOR_ACTUAL'] = df_inv.apply(calcular_rendimiento_actual, axis=1)
    df_inv['GANANCIA'] = df_inv['VALOR_ACTUAL'] - df_inv['MONTO_INICIAL']
    
    total_inversiones = df_inv['VALOR_ACTUAL'].sum()
    ganancia_interes = df_inv['GANANCIA'].sum()

patrimonio_total = total_bancos + total_inversiones

# --- DASHBOARD ---
tab_resumen, tab_inv, tab_gastos = st.tabs(["🏛️ Patrimonio Total", "🚀 Mis Inversiones (Live)", "💸 Gastos Diarios"])

with tab_resumen:
    # Tarjetas Grandes
    c1, c2, c3 = st.columns(3)
    c1.metric("Patrimonio Neto Total", f"${patrimonio_total:,.2f}", help="Suma de tus cuentas + valor actual de inversiones")
    c2.metric("Dinero Líquido (Bancos)", f"${total_bancos:,.2f}", delta="Disponible para gastar")
    c3.metric("En Inversiones (Hoy)", f"${total_inversiones:,.2f}", delta=f"+${ganancia_interes:,.2f} Ganados", delta_color="normal")
    
    st.markdown("---")
    
    # Gráfico de composición
    if patrimonio_total > 0:
        labels = ["Dinero Líquido"]
        values = [total_bancos]
        
        if not df_inv.empty:
            # Agrupar inversiones por plataforma
            inv_group = df_inv.groupby('PLATAFORMA')['VALOR_ACTUAL'].sum()
            labels.extend(inv_group.index.tolist())
            values.extend(inv_group.values.tolist())
            
        fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=.3)])
        fig.update_layout(title_text="¿Dónde está mi dinero?")
        st.plotly_chart(fig, use_container_width=True)

with tab_inv:
    st.subheader("🚀 Seguimiento de Inversiones en Tiempo Real")
    
    if df_inv.empty:
        st.info("No tienes inversiones registradas. Usa el menú lateral para agregar una.")
    else:
        for index, row in df_inv.iterrows():
            # Tarjeta Individual por Inversión
            with st.container():
                cols = st.columns([1, 2, 1])
                
                # Icono y Nombre
                with cols[0]:
                    st.markdown(f"### {row['PLATAFORMA']}")
                    st.caption(row['PRODUCTO'])
                
                # Barra de Progreso Temporal
                with cols[1]:
                    hoy = datetime.now()
                    inicio = row['FECHA_INICIO']
                    fin = row['FECHA_FIN'] if pd.notnull(row['FECHA_FIN']) else inicio + timedelta(days=365)
                    
                    total_dias = (fin - inicio).days
                    dias_pasados = (hoy - inicio).days
                    progreso = min(max(dias_pasados / total_dias, 0.0), 1.0) if total_dias > 0 else 0
                    
                    st.write(f"**Progreso de Meta:** {progreso*100:.1f}% ({dias_pasados}/{total_dias} días)")
                    st.progress(progreso)
                    st.caption(f"Inicia: {inicio.date()} ➝ Termina: {fin.date()}")

                # Datos Financieros
                with cols[2]:
                    st.metric("Valor Hoy", f"${row['VALOR_ACTUAL']:,.2f}", delta=f"+${row['GANANCIA']:,.2f}")
                
                st.divider()
        
        # Tabla resumen
        st.expander("Ver tabla de detalles").dataframe(df_inv[['PLATAFORMA','MONTO_INICIAL','TASA_ANUAL','VALOR_ACTUAL','GANANCIA']])

with tab_gastos:
    st.info("Aquí sigue estando tu control de gastos normal (cargando datos de la hoja principal...)")
    # Reutilizamos lógica simple de visualización
    if not df_movs.empty:
        # Filtros básicos
        last_month = df_movs[df_movs['FECHA'] >= datetime.now() - timedelta(days=30)]
        gastos_mes = abs(last_month[last_month['IMPORTE_REAL'] < 0]['IMPORTE_REAL'].sum())
        st.metric("Gastos últimos 30 días", f"${gastos_mes:,.2f}")
        st.dataframe(last_month.tail(10))
