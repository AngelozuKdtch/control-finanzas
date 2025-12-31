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
st.set_page_config(page_title="Finanzas Master v3.5", page_icon="💎", layout="wide")

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
        df_deudas = pd.DataFrame() 

    return df_movs, df_inv, df_deudas, sh

# ================= FUNCIONES AUXILIARES =================
def generar_pdf(fecha, cuenta, monto, concepto):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "COMPROBANTE DE PAGO", ln=1, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Fecha: {fecha}", ln=1)
    pdf.cell(0, 10, f"Concepto: {concepto}", ln=1)
    pdf.cell(0, 10, f"Monto: ${monto:,.2f}", ln=1)
    pdf.cell(0, 10, f"Cuenta: {cuenta}", ln=1)
    return pdf.output(dest='S').encode('latin-1')

def descargar_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()

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

# === NUEVA FUNCIÓN: PROCESAR ABONO ===
def procesar_abono(sh, nombre_deuda, monto_abono, cuenta_bancaria):
    try:
        ws_deudas = sh.worksheet("Deudas")
        datos_deudas = ws_deudas.get_all_records()
        
        # 1. Buscar la fila de la deuda
        fila_idx = -1
        deuda_info = None
        
        for i, d in enumerate(datos_deudas):
            if d['QUIEN'] == nombre_deuda and d['ESTADO'] == 'Activo':
                fila_idx = i + 2 # +2 porque sheets empieza en 1 y tiene encabezado
                deuda_info = d
                break
        
        if fila_idx == -1:
            st.error("No se encontró la deuda activa.")
            return

        # 2. Calcular nuevos valores
        nuevo_abonado = float(deuda_info['ABONADO']) + monto_abono
        monto_total = float(deuda_info['MONTO_TOTAL'])
        
        # 3. Actualizar Hoja Deudas
        # Columna 4 es ABONADO
        ws_deudas.update_cell(fila_idx, 4, nuevo_abonado)
        
        # Si se completó, cambiar estado a Pagado (Columna 6)
        estado_final = "Activo"
        if nuevo_abonado >= monto_total:
            ws_deudas.update_cell(fila_idx, 6, "Pagado")
            estado_final = "Pagado"
            st.balloons()
            
        # 4. Registrar el movimiento de dinero en Hoja 1
        hoy = datetime.now().strftime("%Y-%m-%d")
        
        if deuda_info['TIPO'] == "Debo Yo":
            # Si yo pago, sale dinero de mi cuenta (Gasto)
            concepto = f"Abono a deuda: {nombre_deuda}"
            guardar_registro(sh, "Hoja 1", ["Manual", hoy, concepto, monto_abono, "-", "-", "Gasto", cuenta_bancaria])
            
        elif deuda_info['TIPO'] == "Me Deben":
            # Si me pagan, entra dinero a mi cuenta (Ingreso)
            concepto = f"Cobro a: {nombre_deuda}"
            guardar_registro(sh, "Hoja 1", ["Manual", hoy, concepto, monto_abono, "-", "-", "Pago", cuenta_bancaria])
            
    except Exception as e:
        st.error(f"Error procesando abono: {e}")

# ================= INTERFAZ PRINCIPAL =================
df_movs, df_inv, df_deudas, sh_obj = cargar_todo()

# --- SIDEBAR: CENTRO DE MANDO ---
with st.sidebar:
    st.title("🎛️ Centro de Mando")
    st.caption(f"Usuario: {st.secrets.get('admin_user','Admin')}")
    
    # Filtros Globales
    hoy = datetime.now()
    f_inicio = st.date_input("Desde", date(hoy.year, 1, 1))
    f_fin = st.date_input("Hasta", hoy)
    
    cuentas_lista = sorted(list(df_movs['BANCO'].unique())) if not df_movs.empty else ["Efectivo"]
    filtro_cuenta = st.selectbox("Filtrar Cuenta", ["Todas"] + cuentas_lista)
    
    st.divider()
    
    # --- MENÚ DE ACCIONES RÁPIDAS ---
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

    # 2. Registrar Abono (NUEVO)
    with st.expander("💳 Abonar / Cobrar Deuda"):
        if df_deudas.empty:
            st.warning("No hay deudas registradas.")
        else:
            # Filtramos solo las activas
            deudas_activas = df_deudas[df_deudas['ESTADO'] == 'Activo']['QUIEN'].tolist()
            if not deudas_activas:
                st.info("¡Estás libre de deudas!")
            else:
                with st.form("form_abono"):
                    d_selec = st.selectbox("Selecciona la Deuda", deudas_activas)
                    a_monto = st.number_input("Monto del Abono $", min_value=0.0)
                    a_cta = st.selectbox("Cuenta de Origen/Destino", cuentas_lista)
                    if st.form_submit_button("Registrar Abono"):
                        if a_monto > 0:
                            procesar_abono(sh_obj, d_selec, a_monto, a_cta)

    # 3. Nueva Deuda
    with st.expander("🤝 Nueva Deuda"):
        with st.form("form_deuda"):
            quien = st.text_input("Persona / Banco")
            d_tipo = st.selectbox("Tipo", ["Debo Yo", "Me Deben"])
            d_monto = st.number_input("Monto Total $", min_value=0.0)
            if st.form_submit_button("Crear Deuda"):
                guardar_registro(sh_obj, "Deudas", [quien, d_tipo, d_monto, 0, str(hoy + timedelta(days=30)), "Activo"])

    # 4. Inversión
    with st.expander("📈 Nueva Inversión"):
        with st.form("form_inv"):
            plat = st.selectbox("Plataforma", ["Nu", "Cetes", "GBM", "Banco"])
            prod = st.text_input("Producto")
            m_inv = st.number_input("Inversión $", min_value=0.0)
            tasa = st.number_input("Tasa %", value=15.0)
            if st.form_submit_button("Guardar Inversión"):
                guardar_registro(sh_obj, "Inversiones", [plat, prod, str(hoy), m_inv, tasa, str(hoy + timedelta(days=365))])

    if st.button("🔒 Salir"):
        st.session_state['password_correct'] = False
        st.rerun()

# --- LÓGICA DE DATOS ---
df_filtrado = df_movs.copy()
if not df_filtrado.empty:
    mask = (df_filtrado['FECHA'].dt.date >= f_inicio) & (df_filtrado['FECHA'].dt.date <= f_fin)
    df_filtrado = df_filtrado.loc[mask]
    if filtro_cuenta != "Todas":
        df_filtrado = df_filtrado[df_filtrado['BANCO'] == filtro_cuenta]

# --- PESTAÑAS ---
tab1, tab2, tab3, tab4 = st.tabs(["📊 Resumen", "📝 Detalle", "🚀 Inversiones", "🤝 Deudas"])

# TAB 1: RESUMEN
with tab1:
    st.subheader("Visión General")
    saldo_liquido = df_movs['IMPORTE_REAL'].sum() if not df_movs.empty else 0
    
    valor_inv = 0
    if not df_inv.empty:
        for _, row in df_inv.iterrows():
            dias = (hoy - row['FECHA_INICIO']).days
            val = row['MONTO_INICIAL'] * ((1 + ((row['TASA_ANUAL']/100)/365)) ** max(dias, 0))
            valor_inv += val
            
    # Sumar deudas pendientes (para restar al patrimonio real si quisiéramos ser estrictos, 
    # pero usualmente patrimonio es activos - pasivos. Aquí mostramos activos brutos por simplicidad)
    
    k1, k2, k3 = st.columns(3)
    k1.metric("🏛️ Patrimonio (Activos)", f"${saldo_liquido + valor_inv:,.2f}")
    k2.metric("💵 Liquidez", f"${saldo_liquido:,.2f}")
    k3.metric("📈 Inversiones", f"${valor_inv:,.2f}")
    
    st.divider()
    st.markdown("### 🎯 Presupuesto")
    presupuesto = st.number_input("Presupuesto", value=5000, step=500)
    gastos = abs(df_filtrado[df_filtrado['IMPORTE_REAL'] < 0]['IMPORTE_REAL'].sum())
    st.progress(min(gastos / presupuesto, 1.0) if presupuesto > 0 else 0)
    st.caption(f"Gastado: ${gastos:,.2f} / ${presupuesto:,.2f}")

# TAB 2: DETALLE
with tab2:
    col_d1, col_d2 = st.columns([3,1])
    with col_d1:
        st.dataframe(df_filtrado[['FECHA', 'DESCRIPCION', 'IMPORTE_REAL', 'BANCO', 'TIPO']].sort_values('FECHA', ascending=False), use_container_width=True)
    with col_d2:
        if not df_filtrado.empty:
            excel_data = descargar_excel(df_filtrado)
            st.download_button("📥 Excel", excel_data, "Reporte.xlsx")
            
            st.divider()
            st.write("Generar Recibo")
            opcion = st.selectbox("Movimiento:", df_filtrado.index, format_func=lambda x: f"{df_filtrado.loc[x, 'DESCRIPCION']} (${df_filtrado.loc[x, 'IMPORTE']})")
            if st.button("PDF"):
                r = df_filtrado.loc[opcion]
                pdf = generar_pdf(str(r['FECHA'].date()), r['BANCO'], r['IMPORTE'], r['DESCRIPCION'])
                b64 = base64.b64encode(pdf).decode()
                st.markdown(f'<a href="data:application/pdf;base64,{b64}" download="Recibo.pdf">Descargar PDF</a>', unsafe_allow_html=True)

# TAB 3: INVERSIONES
with tab3:
    if not df_inv.empty:
        df_inv['VALOR_HOY'] = df_inv.apply(lambda r: r['MONTO_INICIAL'] * ((1 + ((r['TASA_ANUAL']/100)/365)) ** max((hoy - r['FECHA_INICIO']).days, 0)), axis=1)
        fig = px.bar(df_inv, x='PLATAFORMA', y='VALOR_HOY', color='PRODUCTO', title="Crecimiento de Inversiones")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_inv[['PLATAFORMA', 'PRODUCTO', 'MONTO_INICIAL', 'VALOR_HOY']])
    else:
        st.info("Sin inversiones.")

# TAB 4: DEUDAS
with tab4:
    if not df_deudas.empty:
        c1, c2 = st.columns(2)
        debo = df_deudas[df_deudas['TIPO'] == "Debo Yo"]
        me_deben = df_deudas[df_deudas['TIPO'] == "Me Deben"]
        
        with c1:
            st.error(f"🔴 Yo Debo: ${debo['MONTO_TOTAL'].sum() - debo['ABONADO'].sum():,.2f}")
            for _, d in debo.iterrows():
                resta = d['MONTO_TOTAL'] - d['ABONADO']
                if resta > 0:
                    st.write(f"**{d['QUIEN']}**: Restan ${resta:,.2f}")
                    st.progress(d['ABONADO']/d['MONTO_TOTAL'])
                    
        with c2:
            st.success(f"🟢 Me Deben: ${me_deben['MONTO_TOTAL'].sum() - me_deben['ABONADO'].sum():,.2f}")
            for _, d in me_deben.iterrows():
                resta = d['MONTO_TOTAL'] - d['ABONADO']
                if resta > 0:
                    st.write(f"**{d['QUIEN']}**: Restan ${resta:,.2f}")
                    st.progress(d['ABONADO']/d['MONTO_TOTAL'])
    else:
        st.info("Sin deudas.")

