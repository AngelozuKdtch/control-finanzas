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
st.set_page_config(page_title="Control Financiero Pro", page_icon="🏦", layout="wide")

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
                    time.sleep(0.5)
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

# ================= FUNCIONES =================
def convertir_df_a_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Reporte')
    return output.getvalue()

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

@st.cache_data(ttl=5)
def cargar_datos():
    hoja = conectar_google()
    if not hoja: return pd.DataFrame()
    datos = hoja.get_all_records()
    df = pd.DataFrame(datos).astype(str)
    
    # Limpieza numérica
    if 'IMPORTE' in df.columns:
        df['IMPORTE'] = pd.to_numeric(df['IMPORTE'], errors='coerce').fillna(0).abs()
    
    # Lógica de Signos para la App (Visualización)
    # Creamos una columna 'IMPORTE_REAL' que tenga el signo matemático correcto
    # Gasto = Negativo, Ingreso = Positivo
    if 'TIPO' in df.columns:
        mask_gasto = df['TIPO'].str.strip().str.upper().str.contains('GASTO')
        df['IMPORTE_REAL'] = df['IMPORTE']
        df.loc[mask_gasto, 'IMPORTE_REAL'] *= -1
        
        # Para visualización gráfica (todo positivo) usamos IMPORTE
        # Para cálculos de saldo usamos IMPORTE_REAL
    else:
        df['IMPORTE_REAL'] = df['IMPORTE'] * -1 # Asumimos gasto si falla
        
    if 'FECHA' in df.columns:
        df['FECHA'] = pd.to_datetime(df['FECHA'], errors='coerce', dayfirst=True)
        df = df.dropna(subset=['FECHA'])
    return df

def guardar_movimiento(banco, fecha, concepto, monto, tipo_real):
    hoja = conectar_google()
    if hoja:
        fecha_str = fecha.strftime("%Y-%m-%d")
        etiqueta_tipo = "Gasto" if tipo_real == "Gasto (Salida)" else "Pago"
        nueva_fila = ["Manual", fecha_str, concepto, abs(monto), "-", "-", etiqueta_tipo, banco]
        try:
            hoja.append_row(nueva_fila)
            st.toast(f"✅ Guardado: {concepto}", icon="💾")
            st.cache_data.clear()
            return True
        except Exception as e:
            st.error(f"Error guardando: {e}")
            return False

# ================= INTERFAZ =================
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

# Lista inteligente de cuentas (incluyendo subcuentas si las nombras distinto)
cuentas_unicas = sorted(list(df_full['BANCO'].unique())) if not df_full.empty else ["Todas"]
cuentas = ["Todas"] + cuentas_unicas
filtro_banco = st.sidebar.selectbox("Cuenta Visualizada", cuentas)

st.sidebar.markdown("---")
presupuesto = st.sidebar.number_input("Presupuesto Mensual ($)", value=5000, step=500)

with st.sidebar.expander("📝 Registrar Rápido"):
    with st.form("add_fast", clear_on_submit=True):
        tipo = st.radio("Tipo", ["Gasto (Salida)", "Ingreso (Pago)"])
        monto = st.number_input("$ Monto", min_value=0.0)
        desc = st.text_input("Concepto")
        # Aquí permitimos elegir subcuentas existentes o escribir una nueva
        cta = st.selectbox("Cuenta / Subcuenta", cuentas_unicas + ["Efectivo", "Nu - Inversión", "Nu - Disponible"])
        if st.form_submit_button("Guardar"):
            if monto > 0:
                guardar_movimiento(cta, hoy, desc, monto, tipo)
                st.rerun()

# --- PESTAÑAS PRINCIPALES ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "💰 Mis Cuentas e Inversiones", "🔮 Predicciones IA", "🤝 Deudas (Próximamente)"])

# 1. DASHBOARD
with tab1:
    if df_full.empty:
        st.info("Sin datos.")
    else:
        mask = (df_full['FECHA'].dt.date >= f_inicio) & (df_full['FECHA'].dt.date <= f_fin)
        df_view = df_full.loc[mask]
        if filtro_banco != "Todas":
            df_view = df_view[df_view['BANCO'] == filtro_banco]

        # Cálculos del periodo seleccionado
        gastos = abs(df_view[df_view['IMPORTE_REAL'] < 0]['IMPORTE_REAL'].sum())
        ingresos = df_view[df_view['IMPORTE_REAL'] > 0]['IMPORTE_REAL'].sum()
        balance = ingresos - gastos
        
        col1, col2, col3 = st.columns(3)
        col1.metric("💸 Gastos (Periodo)", f"${gastos:,.2f}")
        col2.metric("💰 Ingresos (Periodo)", f"${ingresos:,.2f}")
        col3.metric("Balance (Periodo)", f"${balance:,.2f}", delta=f"{balance:,.2f}")

        st.progress(min(gastos / presupuesto, 1.0) if presupuesto > 0 else 0)
        
        # Gráficos
        c1, c2 = st.columns([2,1])
        with c1:
            df_g = df_view[df_view['IMPORTE_REAL'] < 0].copy()
            if not df_g.empty:
                df_g['Cat'] = df_g['DESCRIPCION'].str.split().str[0]
                fig = px.pie(df_g, values='IMPORTE', names='Cat', hole=0.4, title="Gastos por Categoría")
                st.plotly_chart(fig, use_container_width=True)

# 2. CUENTAS E INVERSIONES (NUEVO MODULO)
with tab2:
    st.subheader("💼 Estado Patrimonial (Saldos Totales)")
    st.caption("Aquí se muestra el dinero TOTAL acumulado en cada cuenta desde el inicio de los tiempos.")
    
    if not df_full.empty:
        # Agrupamos por BANCO y sumamos IMPORTE_REAL
        saldos = df_full.groupby('BANCO')['IMPORTE_REAL'].sum().reset_index()
        saldos = saldos.rename(columns={'IMPORTE_REAL': 'Saldo Actual'})
        saldos = saldos.sort_values('Saldo Actual', ascending=False)
        
        # Tarjeta de Total
        total_patrimonio = saldos['Saldo Actual'].sum()
        st.metric("💵 DINERO TOTAL DISPONIBLE", f"${total_patrimonio:,.2f}")
        
        # Gráfico de Barras de Saldos
        fig_saldos = px.bar(saldos, x='BANCO', y='Saldo Actual', color='Saldo Actual', 
                            color_continuous_scale='Greens', text_auto=',.2f')
        st.plotly_chart(fig_saldos, use_container_width=True)
        
        # Tabla detallada
        st.dataframe(saldos.style.format({"Saldo Actual": "${:,.2f}"}), use_container_width=True)
    
    st.markdown("---")
    st.subheader("📈 Calculadora de Rendimientos (Inversiones)")
    
    col_calc1, col_calc2 = st.columns(2)
    with col_calc1:
        inv_monto = st.number_input("Monto a Invertir ($)", value=1000.0)
        inv_tasa = st.number_input("Tasa Anual (%) (Ej: 15% Nu)", value=15.0)
    with col_calc2:
        inv_plazo = st.selectbox("Frecuencia de Pago", ["Diario", "Semanal", "Mensual", "Al Vencimiento (Anual)"])
        inv_dias = st.slider("Días de Proyección", 1, 365, 30)

    # Cálculo simple de interés compuesto diario
    tasa_diaria = (inv_tasa / 100) / 365
    
    if inv_plazo == "Diario":
        ganancia = inv_monto * ((1 + tasa_diaria) ** inv_dias) - inv_monto
        frecuencia_txt = "todos los días"
    elif inv_plazo == "Semanal":
        ganancia = (inv_monto * (inv_tasa/100) / 52) * (inv_dias/7)
        frecuencia_txt = "cada semana"
    else:
        # Simple mensual/anual para no complicar
        ganancia = (inv_monto * (inv_tasa/100)) * (inv_dias/365)
        frecuencia_txt = "en el periodo"

    st.success(f"🤑 En **{inv_dias} días**, ganarías aproximadamente: **${ganancia:,.2f}** de puro interés.")
    st.info(f"💡 Esto asumiendo una tasa del {inv_tasa}% anual con reinversión {inv_plazo}.")

# 3. PREDICCIONES
with tab3:
    st.subheader("🔮 Proyección de Fin de Mes")
    # (Lógica simplificada de predicción)
    hoy = datetime.now()
    inicio_mes = datetime(hoy.year, hoy.month, 1)
    mask_mes = (df_full['FECHA'] >= inicio_mes) & (df_full['FECHA'] <= hoy) & (df_full['IMPORTE_REAL'] < 0)
    gasto_mes = abs(df_full.loc[mask_mes]['IMPORTE_REAL'].sum())
    
    if hoy.day > 0:
        velocidad = gasto_mes / hoy.day
        _, last_day = calendar.monthrange(hoy.year, hoy.month)
        proyeccion = gasto_mes + (velocidad * (last_day - hoy.day))
        
        c_p1, c_p2 = st.columns(2)
        c_p1.metric("Velocidad de Gasto", f"${velocidad:,.2f} / día")
        delta_color = "inverse" if proyeccion > presupuesto else "normal"
        c_p2.metric("Cierre Estimado", f"${proyeccion:,.2f}", delta=f"${proyeccion - presupuesto:,.2f}", delta_color=delta_color)

# 4. DEUDAS (PREPARACIÓN)
with tab4:
    st.subheader("🤝 Gestor de Deudas y Préstamos")
    st.warning("🚧 Para activar este módulo, necesitaremos crear una Hoja Nueva en tu Google Sheets.")
    st.markdown("""
    **Próximo paso:** Crear hoja 'Deudas' con las columnas:
    * **QUIEN:** (Nombre de la persona o Banco)
    * **TIPO:** (Debo yo / Me deben a mí)
    * **MONTO_TOTAL:** (La deuda original)
    * **ABONADO:** (Cuánto se ha pagado ya)
    * **INTERES:** (Si aplica)
    """)

