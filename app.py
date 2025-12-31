import streamlit as st
import pandas as pd
import gspread
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date, timedelta
from fpdf import FPDF
import base64
from io import BytesIO
import json
import time

# ================= CONFIGURACIÓN VISUAL =================
st.set_page_config(page_title="Finanzas Master v3", page_icon="💎", layout="wide")

# ================= 🔒 LOGIN BLINDADO =================
def check_password():
    if st.session_state.get('password_correct', False):
        return True
    
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("### 💎 Acceso al Sistema Financiero")
        with st.form("login_form"):
            user = st.text_input("Usuario")
            pwd = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Entrar"):
                # Verificamos secretos
                if user == st.secrets.get("admin_user", "admin") and pwd == st.secrets.get("admin_pass", "1234"):
                    st.session_state['password_correct'] = True
                    st.rerun()
                else:
                    st.error("❌ Datos incorrectos")
    return False

if not check_password():
    st.stop()

# ================= CONEXIÓN Y DATOS =================
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
        st.error(f"Error crítico de conexión: {e}")
        st.stop()

@st.cache_data(ttl=5)
def cargar_todo():
    sh = conectar_google()
    
    # 1. MOVIMIENTOS (Hoja 1)
    df_movs = pd.DataFrame(sh.sheet1.get_all_records()).astype(str)
    if not df_movs.empty:
        if 'IMPORTE' in df_movs.columns:
            df_movs['IMPORTE'] = pd.to_numeric(df_movs['IMPORTE'], errors='coerce').fillna(0).abs()
        if 'FECHA' in df_movs.columns:
            df_movs['FECHA'] = pd.to_datetime(df_movs['FECHA'], errors='coerce', dayfirst=True)
            df_movs = df_movs.dropna(subset=['FECHA'])
        
        # Crear columna de importe real (Negativo para gastos)
        df_movs['IMPORTE_REAL'] = df_movs['IMPORTE']
        if 'TIPO' in df_movs.columns:
            mask_gasto = df_movs['TIPO'].str.upper().str.contains('GASTO')
            df_movs.loc[mask_gasto, 'IMPORTE_REAL'] *= -1
        else:
            df_movs['IMPORTE_REAL'] *= -1

    # 2. INVERSIONES (Hoja 'Inversiones')
    try:
        df_inv = pd.DataFrame(sh.worksheet("Inversiones").get_all_records())
        if not df_inv.empty:
            cols_num = ['MONTO_INICIAL', 'TASA_ANUAL']
            for col in cols_num:
                if col in df_inv.columns:
                    df_inv[col] = pd.to_numeric(df_inv[col], errors='coerce').fillna(0)
            df_inv['FECHA_INICIO'] = pd.to_datetime(df_inv['FECHA_INICIO'], errors='coerce', dayfirst=True)
    except:
        df_inv = pd.DataFrame()

    # 3. DEUDAS (Hoja 'Deudas')
    try:
        df_deudas = pd.DataFrame(sh.worksheet("Deudas").get_all_records())
        if not df_deudas.empty:
            cols_num = ['MONTO_TOTAL', 'ABONADO']
            for col in cols_num:
                if col in df_deudas.columns:
                    df_deudas[col] = pd.to_numeric(df_deudas[col], errors='coerce').fillna(0)
    except:
        df_deudas = pd.DataFrame() # Si no existe, vacía

    return df_movs, df_inv, df_deudas, sh

# ================= HERRAMIENTAS V1 (PDF/EXCEL) =================
def generar_pdf(fecha, cuenta, monto, concepto):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "COMPROBANTE DE MOVIMIENTO", ln=1, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Fecha: {fecha}", ln=1)
    pdf.cell(0, 10, f"Concepto: {concepto}", ln=1)
    pdf.cell(0, 10, f"Monto: ${monto:,.2f}", ln=1)
    pdf.cell(0, 10, f"Cuenta: {cuenta}", ln=1)
    pdf.ln(20)
    pdf.cell(0, 10, "__________________________", ln=1, align='C')
    pdf.cell(0, 10, "Firma Autorizada", ln=1, align='C')
    return pdf.output(dest='S').encode('latin-1')

def descargar_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()

# ================= GUARDADO RÁPIDO =================
def guardar_registro(sh, hoja, datos):
    try:
        ws = sh.worksheet(hoja)
        ws.append_row(datos)
        st.toast(f"✅ Guardado en {hoja}", icon="💾")
        st.cache_data.clear()
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.error(f"Error al guardar: {e}")

# ================= INTERFAZ PRINCIPAL =================
df_movs, df_inv, df_deudas, sh_obj = cargar_todo()

# --- SIDEBAR: CENTRO DE MANDO ---
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/1200px-Python-logo-notext.svg.png", width=50)
    st.title("Centro de Control")
    st.caption(f"Usuario: {st.secrets.get('admin_user','Admin')}")
    
    # Filtros Globales (V1 Style)
    st.header("📅 Filtros")
    hoy = datetime.now()
    f_inicio = st.date_input("Desde", date(hoy.year, 1, 1))
    f_fin = st.date_input("Hasta", hoy)
    
    cuentas_lista = sorted(list(df_movs['BANCO'].unique())) if not df_movs.empty else ["Efectivo"]
    filtro_cuenta = st.selectbox("Filtrar Cuenta", ["Todas"] + cuentas_lista)
    
    st.divider()
    
    # --- MENÚ DE ACCIONES RÁPIDAS (EXPANDERS) ---
    st.subheader("⚡ Acciones Rápidas")
    
    # 1. Registrar Gasto/Ingreso
    with st.expander("💸 Nuevo Gasto / Ingreso"):
        with st.form("form_gasto"):
            tipo = st.radio("Tipo", ["Gasto (Salida)", "Ingreso (Pago)"])
            monto = st.number_input("Monto $", min_value=0.0)
            desc = st.text_input("Concepto")
            cta = st.selectbox("Cuenta", cuentas_lista + ["Nueva..."])
            fecha = st.date_input("Fecha", hoy)
            if st.form_submit_button("Guardar Movimiento"):
                etiqueta = "Gasto" if tipo == "Gasto (Salida)" else "Pago"
                guardar_registro(sh_obj, "Hoja 1", ["Manual", str(fecha), desc, monto, "-", "-", etiqueta, cta])

    # 2. Registrar Inversión
    with st.expander("📈 Nueva Inversión"):
        with st.form("form_inv"):
            plat = st.selectbox("Plataforma", ["Nu", "Cetes", "GBM", "Banco"])
            prod = st.text_input("Producto (Cajita, Bonddia)")
            m_inv = st.number_input("Inversión Inicial $", min_value=0.0)
            tasa = st.number_input("Tasa Anual %", value=15.0)
            if st.form_submit_button("Guardar Inversión"):
                guardar_registro(sh_obj, "Inversiones", [plat, prod, str(hoy), m_inv, tasa, str(hoy + timedelta(days=365))])

    # 3. Registrar Deuda
    with st.expander("🤝 Nueva Deuda/Préstamo"):
        with st.form("form_deuda"):
            quien = st.text_input("Persona / Banco")
            d_tipo = st.selectbox("Tipo", ["Debo Yo", "Me Deben"])
            d_monto = st.number_input("Monto Total $", min_value=0.0)
            if st.form_submit_button("Registrar Deuda"):
                # Columnas: QUIEN, TIPO, TOTAL, ABONADO, LIMITE, ESTADO
                guardar_registro(sh_obj, "Deudas", [quien, d_tipo, d_monto, 0, str(hoy + timedelta(days=30)), "Activo"])
    
    st.divider()
    if st.button("🔒 Cerrar Sesión"):
        st.session_state['password_correct'] = False
        st.rerun()

# --- LÓGICA DE DATOS (Filtros) ---
df_filtrado = df_movs.copy()
if not df_filtrado.empty:
    mask = (df_filtrado['FECHA'].dt.date >= f_inicio) & (df_filtrado['FECHA'].dt.date <= f_fin)
    df_filtrado = df_filtrado.loc[mask]
    if filtro_cuenta != "Todas":
        df_filtrado = df_filtrado[df_filtrado['BANCO'] == filtro_cuenta]

# --- PESTAÑAS PRINCIPALES ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Resumen & Presupuesto", "📝 Control Detallado", "🚀 Inversiones", "🤝 Deudas y Préstamos"])

# ================= TAB 1: RESUMEN GLOBAL (V1 + V2) =================
with tab1:
    st.subheader("Visión General del Comandante")
    
    # Cálculos Globales
    saldo_liquido = df_movs['IMPORTE_REAL'].sum() if not df_movs.empty else 0
    
    valor_inversiones = 0
    ganancia_inv = 0
    if not df_inv.empty:
        for _, row in df_inv.iterrows():
            dias = (hoy - row['FECHA_INICIO']).days
            if dias > 0:
                val = row['MONTO_INICIAL'] * ((1 + ((row['TASA_ANUAL']/100)/365)) ** dias)
                valor_inversiones += val
                ganancia_inv += (val - row['MONTO_INICIAL'])
            else:
                valor_inversiones += row['MONTO_INICIAL']

    patrimonio = saldo_liquido + valor_inversiones

    # KPIs Principales
    k1, k2, k3 = st.columns(3)
    k1.metric("🏛️ Patrimonio Neto", f"${patrimonio:,.2f}", help="Dinero en Bancos + Valor Inversiones")
    k2.metric("💵 Liquidez (Bancos)", f"${saldo_liquido:,.2f}", delta="Disponible")
    k3.metric("📈 En Inversiones", f"${valor_inversiones:,.2f}", delta=f"+${ganancia_inv:,.2f} Ganado")
    
    st.divider()
    
    # Barra de Presupuesto (Feature de V1)
    st.markdown("### 🎯 Control de Presupuesto Mensual")
    presupuesto = st.number_input("Presupuesto del Periodo", value=5000, step=500)
    
    gastos_periodo = abs(df_filtrado[df_filtrado['IMPORTE_REAL'] < 0]['IMPORTE_REAL'].sum())
    
    col_bar1, col_bar2 = st.columns([3, 1])
    with col_bar1:
        progreso = min(gastos_periodo / presupuesto, 1.0) if presupuesto > 0 else 0
        st.progress(progreso)
        if gastos_periodo > presupuesto:
            st.error(f"🚨 ¡EXCEDIDO POR ${gastos_periodo - presupuesto:,.2f}!")
        else:
            st.success(f"✅ Te quedan ${presupuesto - gastos_periodo:,.2f}")
    
    with col_bar2:
        st.metric("Gastado", f"${gastos_periodo:,.2f}")

# ================= TAB 2: DETALLE Y HERRAMIENTAS (V1 Clásica) =================
with tab2:
    st.subheader("📝 Gestión de Movimientos")
    
    c1, c2 = st.columns([2, 1])
    with c1:
        # Gráfico de Dona (V1)
        if not df_filtrado.empty:
            df_gastos = df_filtrado[df_filtrado['IMPORTE_REAL'] < 0].copy()
            if not df_gastos.empty:
                df_gastos['Abs'] = df_gastos['IMPORTE'].abs()
                df_gastos['Concepto'] = df_gastos['DESCRIPCION'].str.split().str[0]
                fig = px.pie(df_gastos, values='Abs', names='Concepto', hole=0.4, title="Distribución de Gastos")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No hay gastos en este periodo.")
    
    with c2:
        st.info("🛠️ Herramientas")
        # Exportar Excel
        if not df_filtrado.empty:
            excel_data = descargar_excel(df_filtrado)
            st.download_button("📥 Descargar Reporte Excel", excel_data, "Reporte_Financiero.xlsx")
            
            st.divider()
            # Generador de Recibos (V1)
            st.write("**🖨️ Generar Recibo PDF**")
            opciones = df_filtrado.apply(lambda x: f"{x['FECHA'].strftime('%d/%m')} - {x['DESCRIPCION']} (${x['IMPORTE']})", axis=1)
            seleccion = st.selectbox("Selecciona movimiento:", opciones)
            
            if st.button("Crear PDF"):
                idx = opciones[opciones == seleccion].index[0]
                row = df_filtrado.loc[idx]
                pdf_bytes = generar_pdf(str(row['FECHA'].date()), row['BANCO'], row['IMPORTE'], row['DESCRIPCION'])
                b64_pdf = base64.b64encode(pdf_bytes).decode()
                href = f'<a href="data:application/octet-stream;base64,{b64_pdf}" download="Recibo.pdf">⬇️ Descargar PDF</a>'
                st.markdown(href, unsafe_allow_html=True)

    # Tabla Detallada
    st.dataframe(df_filtrado[['FECHA', 'DESCRIPCION', 'IMPORTE_REAL', 'BANCO', 'TIPO']].sort_values('FECHA', ascending=False), use_container_width=True)

# ================= TAB 3: INVERSIONES (V2) =================
with tab3:
    st.subheader("🚀 Portafolio de Crecimiento")
    if df_inv.empty:
        st.warning("No hay inversiones registradas. Usa el menú lateral.")
    else:
        # Gráfico de Barras de Inversiones
        df_inv['VALOR_ACTUAL'] = df_inv.apply(lambda r: r['MONTO_INICIAL'] * ((1 + ((r['TASA_ANUAL']/100)/365)) ** max((hoy - r['FECHA_INICIO']).days, 0)), axis=1)
        
        fig_inv = px.bar(df_inv, x='PLATAFORMA', y='VALOR_ACTUAL', color='PRODUCTO', title="Valor Actual por Plataforma", text_auto=',.0f')
        st.plotly_chart(fig_inv, use_container_width=True)
        
        # Lista detallada
        for _, row in df_inv.iterrows():
            with st.container(border=True):
                col_i1, col_i2 = st.columns([3, 1])
                ganancia = row['VALOR_ACTUAL'] - row['MONTO_INICIAL']
                with col_i1:
                    st.markdown(f"**{row['PLATAFORMA']}** - {row['PRODUCTO']}")
                    st.progress(min((hoy - row['FECHA_INICIO']).days / 365, 1.0))
                    st.caption(f"Tasa: {row['TASA_ANUAL']}% | Inicio: {row['FECHA_INICIO'].date()}")
                with col_i2:
                    st.metric("Valor Hoy", f"${row['VALOR_ACTUAL']:,.2f}", delta=f"+${ganancia:,.2f}")

# ================= TAB 4: DEUDAS Y PRÉSTAMOS (NUEVO) =================
with tab4:
    st.subheader("🤝 Gestión de Deudas (Personales y Bancarias)")
    
    if df_deudas.empty:
        st.info("Crea la hoja 'Deudas' en tu Excel y registra una deuda en el menú lateral.")
    else:
        col_d1, col_d2 = st.columns(2)
        
        # Lógica: Debo vs Me Deben
        debo_yo = df_deudas[df_deudas['TIPO'] == "Debo Yo"]
        me_deben = df_deudas[df_deudas['TIPO'] == "Me Deben"]
        
        total_deuda = debo_yo['MONTO_TOTAL'].sum() - debo_yo['ABONADO'].sum()
        total_recuperar = me_deben['MONTO_TOTAL'].sum() - me_deben['ABONADO'].sum()
        
        with col_d1:
            st.error(f"🔴 Yo Debo: ${total_deuda:,.2f}")
            if not debo_yo.empty:
                for _, d in debo_yo.iterrows():
                    pendiente = d['MONTO_TOTAL'] - d['ABONADO']
                    if pendiente > 0: # Solo mostrar si hay saldo
                        st.markdown(f"**{d['QUIEN']}**")
                        st.write(f"Deuda: ${d['MONTO_TOTAL']:,.0f} | Pagado: ${d['ABONADO']:,.0f}")
                        st.progress(d['ABONADO'] / d['MONTO_TOTAL'])
                        st.caption(f"Resta por pagar: ${pendiente:,.2f}")
                        st.divider()

        with col_d2:
            st.success(f"🟢 Me Deben: ${total_recuperar:,.2f}")
            if not me_deben.empty:
                for _, d in me_deben.iterrows():
                    pendiente = d['MONTO_TOTAL'] - d['ABONADO']
                    if pendiente > 0:
                        st.markdown(f"**{d['QUIEN']}**")
                        st.write(f"Presté: ${d['MONTO_TOTAL']:,.0f} | Me pagó: ${d['ABONADO']:,.0f}")
                        st.progress(d['ABONADO'] / d['MONTO_TOTAL'])
                        st.caption(f"Me debe: ${pendiente:,.2f}")
                        st.divider()
