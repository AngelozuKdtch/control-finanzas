import streamlit as st
import pandas as pd
import gspread
import plotly.express as px
from datetime import datetime, date
from fpdf import FPDF
import base64
import json
from io import BytesIO

# ================= CONFIGURACIÓN VISUAL =================
st.set_page_config(page_title="Control Financiero Pro", page_icon="💎", layout="wide")

# ================= CONEXIÓN A GOOGLE SHEETS =================
def conectar_google():
    try:
        # 1. INTENTO NUBE: Buscamos credenciales en los Secretos de Streamlit
        if 'gcp_credentials' in st.secrets:
            # Leemos el secreto como un texto JSON y lo convertimos a diccionario
            creds_dict = json.loads(st.secrets['gcp_credentials'])
            gc = gspread.service_account_from_dict(creds_dict)
        
        # 2. INTENTO LOCAL: Si no hay secretos, buscamos el archivo en la PC
        else:
            gc = gspread.service_account(filename='credentials.json')

        sh = gc.open("BaseDatos_Maestra")
        return sh.sheet1

    except Exception as e:
        st.error(f"❌ Error de conexión: {e}")
        return None

# ================= GENERADOR DE PDF (RECIBOS) =================
def crear_recibo_pdf(fecha, cuenta, monto, concepto):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    # Diseño del Recibo
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "COMPROBANTE DE MOVIMIENTO", ln=1, align='C')
    pdf.ln(10)
    
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Fecha: {fecha}", ln=1)
    pdf.cell(0, 10, f"Concepto: {concepto}", ln=1)
    pdf.cell(0, 10, f"Monto: ${monto:,.2f}", ln=1)
    pdf.cell(0, 10, f"Cuenta: {cuenta}", ln=1)
    
    pdf.ln(20)
    pdf.cell(0, 10, "_" * 40, ln=1, align='C')
    pdf.cell(0, 10, "Firma de Conformidad", ln=1, align='C')
    
    return pdf.output(dest='S').encode('latin-1')

# ================= EXPORTAR EXCEL PRO =================
def convertir_df_a_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Reporte')
        workbook = writer.book
        worksheet = writer.sheets['Reporte']
        
        # Formatos
        money_fmt = workbook.add_format({'num_format': '$#,##0.00'})
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#4F8BF9', 'font_color': 'white', 'border': 1})
        
        for i, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.set_column(i, i, max_len)
            worksheet.write(0, i, col, header_fmt)
            if 'IMPORTE' in str(col).upper():
                worksheet.set_column(i, i, max_len, money_fmt)
    return output.getvalue()

# ================= CARGAR DATOS (MOTOR BLINDADO) =================
@st.cache_data(ttl=2)
def cargar_datos():
    hoja = conectar_google()
    if not hoja: return pd.DataFrame()
    
    datos = hoja.get_all_records()
    df = pd.DataFrame(datos).astype(str)
    
    # 1. LIMPIEZA DE IMPORTES
    if 'IMPORTE' in df.columns:
        df['IMPORTE'] = pd.to_numeric(df['IMPORTE'], errors='coerce').fillna(0).abs()

    # 2. ASIGNACIÓN DE SIGNOS
    if 'TIPO' in df.columns:
        mask_gasto = df['TIPO'].str.strip().str.upper().str.contains('GASTO')
        df.loc[mask_gasto, 'IMPORTE'] *= -1
    else:
        df['IMPORTE'] *= -1 

    # 3. FECHAS
    if 'FECHA' in df.columns:
        df['FECHA'] = pd.to_datetime(df['FECHA'], errors='coerce', dayfirst=True)
        df = df.dropna(subset=['FECHA'])

    return df

# ================= GUARDAR (FORMULARIO) =================
def guardar_movimiento(banco, fecha, concepto, monto, tipo_real):
    hoja = conectar_google()
    if hoja:
        fecha_str = fecha.strftime("%Y-%m-%d")
        etiqueta_tipo = "Gasto" if tipo_real == "Gasto (Salida)" else "Pago"
        
        nueva_fila = ["Manual", fecha_str, concepto, abs(monto), "-", "-", etiqueta_tipo, banco]
        
        try:
            hoja.append_row(nueva_fila)
            st.toast(f"✅ ¡Guardado! {concepto}", icon="💾")
            st.cache_data.clear()
            return True
        except Exception as e:
            st.error(f"Error al guardar: {e}")
            return False

# ========================================================
#                  INTERFAZ GRÁFICA
# ========================================================

st.title("💎 Panel de Control Financiero")

df_full = cargar_datos()

# --- BARRA LATERAL ---
st.sidebar.header("🎛️ Centro de Mando")

# Filtros
hoy = datetime.now()
inicio_anio = datetime(hoy.year, 1, 1)
st.sidebar.caption("📅 Periodo de Análisis")
f_inicio = st.sidebar.date_input("Desde", inicio_anio)
f_fin = st.sidebar.date_input("Hasta", hoy)

cuentas = ["Todas"] + list(df_full['BANCO'].unique()) if not df_full.empty else ["Todas"]
filtro_banco = st.sidebar.selectbox("Filtrar Cuenta", cuentas)

st.sidebar.markdown("---")

# Presupuesto
st.sidebar.subheader("🎯 Meta Mensual")
presupuesto = st.sidebar.number_input("Límite de Gasto ($)", value=5000, step=500)

st.sidebar.markdown("---")

# Formulario
with st.sidebar.expander("📝 Registrar Movimiento", expanded=False):
    with st.form("form_add", clear_on_submit=True):
        tipo_input = st.radio("Tipo", ["Gasto (Salida)", "Ingreso (Pago)"])
        fecha_input = st.date_input("Fecha", hoy)
        desc_input = st.text_input("Concepto")
        monto_input = st.number_input("Monto ($)", min_value=0.0)
        
        list_bancos = list(df_full['BANCO'].unique()) if not df_full.empty else ["Efectivo"]
        banco_input = st.selectbox("Cuenta", list_bancos)
        
        if st.form_submit_button("Guardar"):
            if desc_input and monto_input > 0:
                guardar_movimiento(banco_input, fecha_input, desc_input, monto_input, tipo_input)
                st.rerun()

# --- PANEL PRINCIPAL ---

if df_full.empty:
    st.info("👋 Parece que no hay datos. Sube tu Excel o registra un movimiento.")
else:
    # FILTRADO
    mask = (df_full['FECHA'].dt.date >= f_inicio) & (df_full['FECHA'].dt.date <= f_fin)
    df_view = df_full.loc[mask]
    
    if filtro_banco != "Todas":
        df_view = df_view[df_view['BANCO'] == filtro_banco]

    # BARRA DE ESTADO
    gastos_totales = abs(df_view[df_view['IMPORTE'] < 0]['IMPORTE'].sum())
    progreso = (gastos_totales / presupuesto) if presupuesto > 0 else 0
    progreso_bar = min(progreso, 1.0)
    
    st.markdown(f"### 📊 Estado de Salud Financiera")
    c_bar1, c_bar2 = st.columns([4, 1])
    
    with c_bar1:
        st.write(f"Has gastado **${gastos_totales:,.0f}** de tu límite de **${presupuesto:,.0f}**")
        st.progress(progreso_bar)
        if progreso > 1.0:
            st.error(f"⚠️ ¡ALERTA! Te has excedido por ${gastos_totales - presupuesto:,.2f}")
    
    with c_bar2:
        excel_data = convertir_df_a_excel(df_view)
        st.download_button(
            label="📥 Bajar Excel",
            data=excel_data,
            file_name="Reporte_Financiero.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    st.markdown("---")

    # --- KPIs (AQUÍ ESTÁ EL CAMBIO DE COLOR) ---
    ingresos_totales = df_view[df_view['IMPORTE'] > 0]['IMPORTE'].sum()
    balance = ingresos_totales - gastos_totales

    k1, k2, k3 = st.columns(3)
    k1.metric("💸 Gastos Totales", f"${gastos_totales:,.2f}", delta=f"-${gastos_totales:,.2f}")
    k2.metric("💰 Pagos/Ingresos", f"${ingresos_totales:,.2f}", delta=f"+${ingresos_totales:,.2f}")
    
    # CORRECCIÓN DE COLOR PARA BALANCE:
    # Usamos el mismo valor del balance como 'delta'.
    # Streamlit automáticamente pone VERDE si es positivo y ROJO si es negativo.
    k3.metric("Balance Neto", f"${balance:,.2f}", delta=f"{balance:,.2f}")

    st.markdown("---")

    # --- GRÁFICOS ---
    g_col1, g_col2 = st.columns([2, 1])
    
    with g_col1:
        st.subheader("🍩 Distribución de Gastos")
        df_gastos = df_view[df_view['IMPORTE'] < 0].copy()
        
        if not df_gastos.empty:
            df_gastos['Monto'] = df_gastos['IMPORTE'].abs()
            df_gastos['Concepto'] = df_gastos['DESCRIPCION'].str.split().str[0]
            grafico_data = df_gastos.groupby('Concepto')['Monto'].sum().reset_index().sort_values('Monto', ascending=False).head(10)
            
            fig = px.pie(grafico_data, values='Monto', names='Concepto', hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("🎉 ¡Felicidades! Cero gastos en este periodo.")

    with g_col2:
        st.subheader("📄 Generar Recibo")
        if not df_view.empty:
            df_view['Etiqueta_PDF'] = df_view['FECHA'].dt.strftime('%d/%m') + " | " + df_view['DESCRIPCION'] + " | $" + df_view['IMPORTE'].astype(str)
            opcion_pdf = st.selectbox("Selecciona movimiento:", df_view['Etiqueta_PDF'])
            
            if st.button("🖨️ Crear PDF"):
                fila = df_view[df_view['Etiqueta_PDF'] == opcion_pdf].iloc[0]
                pdf_bytes = crear_recibo_pdf(
                    fila['FECHA'].strftime('%Y-%m-%d'),
                    fila['BANCO'],
                    abs(fila['IMPORTE']),
                    fila['DESCRIPCION']
                )
                b64 = base64.b64encode(pdf_bytes).decode()
                href = f'<a href="data:application/octet-stream;base64,{b64}" download="Recibo.pdf" style="background-color:#FF4B4B; color:white; padding:10px; text-decoration:none; border-radius:5px; font-weight:bold; display:block; text-align:center;">⬇️ DESCARGAR PDF</a>'
                st.markdown(href, unsafe_allow_html=True)

    # --- TABLA ---
    with st.expander("📂 Ver Tabla Detallada de Movimientos"):
        st.dataframe(df_view.style.format({"IMPORTE": "${:,.2f}"}), use_container_width=True)
