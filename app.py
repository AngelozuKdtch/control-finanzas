import streamlit as st
import pandas as pd
import gspread
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import calendar
from fpdf import FPDF
import base64
from io import BytesIO
import json
import time

# ================= CONFIGURACIÓN =================
st.set_page_config(page_title="Control Financiero IA", page_icon="🧠", layout="wide")

# ================= 🔒 SISTEMA DE LOGIN =================
def check_password():
    if st.session_state.get('password_correct', False):
        return True

    st.markdown("## 🔐 Acceso Blindado")
    
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        with st.form("login_form"):
            user = st.text_input("Usuario", placeholder="Usuario")
            pwd = st.text_input("Contraseña", type="password", placeholder="••••••")
            submit = st.form_submit_button("Entrar 🚀")
            
            if submit:
                if user == st.secrets["admin_user"] and pwd == st.secrets["admin_pass"]:
                    st.session_state['password_correct'] = True
                    st.toast("¡Acceso Concedido!", icon="🔓")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("❌ Acceso Denegado")

    return False

if not check_password():
    st.stop()

# ================= CONEXIÓN GOOGLE =================
def conectar_google():
    try:
        if 'credenciales_seguras' in st.secrets:
            try:
                b64_string = st.secrets['credenciales_seguras']
                decoded_bytes = base64.b64decode(b64_string)
                decoded_str = decoded_bytes.decode('utf-8')
                creds_dict = json.loads(decoded_str)
                gc = gspread.service_account_from_dict(creds_dict)
            except Exception as e:
                st.error(f"Error llave: {e}")
                return None
        else:
            gc = gspread.service_account(filename='credentials.json')

        sh = gc.open("BaseDatos_Maestra")
        return sh.sheet1
    except Exception as e:
        st.error(f"Error Conexión: {e}")
        st.stop()
        return None

# ================= FUNCIONES AUXILIARES =================
def crear_recibo_pdf(fecha, cuenta, monto, concepto):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "COMPROBANTE", ln=1, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Fecha: {fecha}", ln=1)
    pdf.cell(0, 10, f"Concepto: {concepto}", ln=1)
    pdf.cell(0, 10, f"Monto: ${monto:,.2f}", ln=1)
    pdf.cell(0, 10, f"Cuenta: {cuenta}", ln=1)
    return pdf.output(dest='S').encode('latin-1')

def convertir_df_a_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Reporte')
    return output.getvalue()

@st.cache_data(ttl=5)
def cargar_datos():
    hoja = conectar_google()
    if not hoja: return pd.DataFrame()
    datos = hoja.get_all_records()
    df = pd.DataFrame(datos).astype(str)
    
    if 'IMPORTE' in df.columns:
        df['IMPORTE'] = pd.to_numeric(df['IMPORTE'], errors='coerce').fillna(0).abs()
    
    # Lógica de Signos
    if 'TIPO' in df.columns:
        mask_gasto = df['TIPO'].str.strip().str.upper().str.contains('GASTO')
        df.loc[mask_gasto, 'IMPORTE'] *= -1
    else:
        df['IMPORTE'] *= -1 
        
    if 'FECHA' in df.columns:
        df['FECHA'] = pd.to_datetime(df['FECHA'], errors='coerce', dayfirst=True)
        df = df.dropna(subset=['FECHA'])
    return df

def guardar_movimiento(banco, fecha, concepto, monto, tipo_real):
    hoja = conectar_google()
    if hoja:
        fecha_str = fecha.strftime("%Y-%m-%d")
        etiqueta_tipo = "Gasto" if tipo_real == "Gasto (Salida)" else "Pago"
        # Estructura ajustada a tu Excel
        nueva_fila = ["Manual", fecha_str, concepto, abs(monto), "-", "-", etiqueta_tipo, banco]
        try:
            hoja.append_row(nueva_fila)
            st.toast(f"✅ Guardado: {concepto}", icon="💾")
            st.cache_data.clear()
            return True
        except Exception as e:
            st.error(f"Error guardando: {e}")
            return False

# ================= 🧠 MOTOR DE PREDICCIONES =================
def generar_prediccion(df, presupuesto):
    hoy = datetime.now()
    inicio_mes = datetime(hoy.year, hoy.month, 1)
    
    # 1. Obtener último día del mes
    _, last_day = calendar.monthrange(hoy.year, hoy.month)
    fin_mes = datetime(hoy.year, hoy.month, last_day)
    
    # 2. Filtrar solo gastos de ESTE mes hasta hoy
    mask_mes = (df['FECHA'] >= inicio_mes) & (df['FECHA'] <= hoy) & (df['IMPORTE'] < 0)
    df_mes = df.loc[mask_mes].copy()
    
    gasto_actual = abs(df_mes['IMPORTE'].sum())
    dias_transcurridos = hoy.day
    dias_totales = last_day
    dias_restantes = dias_totales - dias_transcurridos
    
    # 3. Cálculo de Proyección
    velocidad_diaria = gasto_actual / dias_transcurridos if dias_transcurridos > 0 else 0
    proyeccion_cierre = gasto_actual + (velocidad_diaria * dias_restantes)
    
    return gasto_actual, proyeccion_cierre, velocidad_diaria, dias_restantes, fin_mes

# ================= INTERFAZ PRINCIPAL =================
st.title(f"💎 Panel Financiero de {st.secrets['admin_user']}")

df_full = cargar_datos()

# --- SIDEBAR ---
st.sidebar.header("🎛️ Controles")
if st.sidebar.button("🔒 Salir"):
    st.session_state['password_correct'] = False
    st.rerun()

hoy = datetime.now()
inicio_anio = datetime(hoy.year, 1, 1)
f_inicio = st.sidebar.date_input("Desde", inicio_anio)
f_fin = st.sidebar.date_input("Hasta", hoy)

cuentas = ["Todas"] + list(df_full['BANCO'].unique()) if not df_full.empty else ["Todas"]
filtro_banco = st.sidebar.selectbox("Cuenta", cuentas)

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Meta Mensual")
presupuesto = st.sidebar.number_input("Presupuesto ($)", value=5000, step=500)

with st.sidebar.expander("📝 Registrar Rápido"):
    with st.form("add_fast", clear_on_submit=True):
        tipo = st.radio("Tipo", ["Gasto (Salida)", "Ingreso (Pago)"])
        monto = st.number_input("$ Monto", min_value=0.0)
        desc = st.text_input("Concepto")
        cta = st.selectbox("Cuenta", list(df_full['BANCO'].unique()) if not df_full.empty else ["Efectivo"])
        if st.form_submit_button("Guardar"):
            if monto > 0:
                guardar_movimiento(cta, hoy, desc, monto, tipo)
                st.rerun()

# --- PESTAÑAS (NUEVO DISEÑO) ---
tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🔮 Predicciones IA", "💳 Deudas e Ingresos"])

# PESTAÑA 1: DASHBOARD (Lo clásico)
with tab1:
    if df_full.empty:
        st.info("No hay datos aún.")
    else:
        # Filtros visuales
        mask = (df_full['FECHA'].dt.date >= f_inicio) & (df_full['FECHA'].dt.date <= f_fin)
        df_view = df_full.loc[mask]
        if filtro_banco != "Todas":
            df_view = df_view[df_view['BANCO'] == filtro_banco]

        gastos = abs(df_view[df_view['IMPORTE'] < 0]['IMPORTE'].sum())
        ingresos = df_view[df_view['IMPORTE'] > 0]['IMPORTE'].sum()
        balance = ingresos - gastos
        
        # Alerta Presupuesto
        progreso = min(gastos / presupuesto, 1.0) if presupuesto > 0 else 0
        st.write(f"**Progreso del Presupuesto:** ${gastos:,.0f} / ${presupuesto:,.0f}")
        st.progress(progreso)
        if gastos > presupuesto:
            st.error(f"⚠️ ¡EXCEDIDO por ${gastos-presupuesto:,.2f}!")

        # KPIs
        k1, k2, k3 = st.columns(3)
        k1.metric("💸 Gastos", f"${gastos:,.2f}")
        k2.metric("💰 Pagos/Ingresos", f"${ingresos:,.2f}")
        k3.metric("Balance Neto", f"${balance:,.2f}", delta=f"{balance:,.2f}")

        # Gráfico
        c1, c2 = st.columns([2,1])
        with c1:
            df_g = df_view[df_view['IMPORTE'] < 0].copy()
            if not df_g.empty:
                df_g['Abs'] = df_g['IMPORTE'].abs()
                df_g['Cat'] = df_g['DESCRIPCION'].str.split().str[0]
                fig = px.pie(df_g, values='Abs', names='Cat', hole=0.4, title="Gastos por Categoría")
                st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.write("📥 **Descargas**")
            excel = convertir_df_a_excel(df_view)
            st.download_button("Bajar Excel", excel, "Finanzas.xlsx")

# PESTAÑA 2: PREDICCIONES (NUEVO)
with tab2:
    st.subheader("🔮 Bola de Cristal Financiera")
    if df_full.empty:
        st.warning("Necesito datos para predecir tu futuro.")
    else:
        # Ejecutamos la predicción
        actual, proyectado, velocidad, dias_rest, fecha_fin = generar_prediccion(df_full, presupuesto)
        
        # Tarjetas de Predicción
        col_p1, col_p2, col_p3 = st.columns(3)
        col_p1.metric("📅 Día del Mes", datetime.now().day)
        col_p2.metric("🚀 Velocidad de Gasto", f"${velocidad:,.2f} / día")
        
        # Color del futuro
        delta_color = "normal"
        if proyectado > presupuesto:
            delta_color = "inverse" # Rojo si te vas a pasar
            mensaje = f"⚠️ ALERTA: A este ritmo, te pasarás por **${proyectado - presupuesto:,.2f}**"
            icono = "🔥"
        else:
            mensaje = f"✅ Vas bien. Te sobrarán **${presupuesto - proyectado:,.2f}**"
            icono = "❄️"
            
        col_p3.metric("🏁 Cierre Estimado", f"${proyectado:,.2f}", delta=f"${proyectado - presupuesto:,.2f}", delta_color=delta_color)

        st.info(f"{icono} {mensaje}")
        
        # Gráfico de Tendencia
        st.markdown("### 📈 Tu Tendencia este Mes")
        
        # Datos para el gráfico
        datos_grafico = pd.DataFrame({
            "Escenario": ["Gasto Actual", "Presupuesto", "Proyección Fin de Mes"],
            "Monto": [actual, presupuesto, proyectado],
            "Color": ["blue", "green", "red" if proyectado > presupuesto else "blue"]
        })
        
        fig_pred = px.bar(datos_grafico, x="Escenario", y="Monto", color="Escenario", text_auto=',.0f',
                          color_discrete_map={"Gasto Actual": "#3498db", "Presupuesto": "#2ecc71", "Proyección Fin de Mes": "#e74c3c"})
        st.plotly_chart(fig_pred, use_container_width=True)

# PESTAÑA 3: DEUDAS E INGRESOS (PRÓXIMAMENTE)
with tab3:
    st.subheader("💳 Deudas e Ingresos (En Construcción)")
    st.info("🚧 Aquí implementaremos el control de tus deudas totales y fuentes de ingresos.")
    st.markdown("""
    **Plan de Trabajo:**
    1.  Crear una tabla para registrar tus **Ingresos Fijos** (Sueldo, Negocios).
    2.  Crear un panel de **Deudas Totales** (Cuánto debes en total a las tarjetas vs. cuánto has pagado).
    """)
    
    # Un pequeño adelanto visual de lo que vendrá
    st.write("---")
    c_d1, c_d2 = st.columns(2)
    c_d1.metric("Sueldo Mensual (Ejemplo)", "$20,000", "Fijo")
    c_d2.metric("Deuda Total Tarjetas (Ejemplo)", "$45,000", "-$2,000 pago min.")
