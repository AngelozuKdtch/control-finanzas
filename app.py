import streamlit as st
import pandas as pd
import gspread
import plotly.express as px
from datetime import datetime, date, timedelta
import calendar
import base64
from io import BytesIO
import json
import time
import requests

# ================= CONFIGURACIÓN =================
st.set_page_config(page_title="Asistente Financiero IA", page_icon="📅", layout="wide")

# ================= 🔒 LOGIN =================
def check_password():
    if st.session_state.get('password_correct', False):
        return True
    
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("### 🔐 Acceso Seguro")
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

# ================= 🧠 MOTOR DE FECHAS INTELIGENTE =================
def calcular_proxima_fecha(dia_objetivo):
    """
    Calcula la próxima fecha válida para un día específico (ej: día 30),
    ajustándose si el mes actual no tiene ese día (ej: febrero).
    """
    if not dia_objetivo or dia_objetivo == 0: return None
    
    hoy = datetime.now().date()
    anio = hoy.year
    mes = hoy.month
    
    # Intentamos crear la fecha en el mes actual
    try:
        # Función para obtener el último día válido del mes
        _, ultimo_dia_mes = calendar.monthrange(anio, mes)
        dia_ajustado = min(int(dia_objetivo), ultimo_dia_mes)
        fecha_tentativa = date(anio, mes, dia_ajustado)
    except:
        fecha_tentativa = hoy # Fallback

    # Si la fecha ya pasó este mes, nos vamos al siguiente
    if fecha_tentativa < hoy:
        mes += 1
        if mes > 12:
            mes = 1
            anio += 1
        _, ultimo_dia_mes = calendar.monthrange(anio, mes)
        dia_ajustado = min(int(dia_objetivo), ultimo_dia_mes)
        fecha_tentativa = date(anio, mes, dia_ajustado)
        
    return fecha_tentativa

def cargar_datos_procesados(sh):
    # 1. Movimientos
    try:
        df_movs = pd.DataFrame(sh.sheet1.get_all_records()).astype(str)
        if not df_movs.empty:
            df_movs['IMPORTE'] = pd.to_numeric(df_movs['IMPORTE'], errors='coerce').fillna(0).abs()
            df_movs['FECHA'] = pd.to_datetime(df_movs['FECHA'], errors='coerce', dayfirst=True)
            df_movs['IMPORTE_REAL'] = df_movs.apply(lambda x: -x['IMPORTE'] if 'GASTO' in str(x['TIPO']).upper() else x['IMPORTE'], axis=1)
    except: df_movs = pd.DataFrame()

    # 2. Deudas con Lógica Cíclica
    alertas = []
    calendario_pagos = []
    
    try:
        df_deudas = pd.DataFrame(sh.worksheet("Deudas").get_all_records())
        if not df_deudas.empty:
            df_deudas['MONTO_A_PAGAR'] = pd.to_numeric(df_deudas['MONTO_A_PAGAR'], errors='coerce').fillna(0)
            
            for idx, row in df_deudas.iterrows():
                if row['ESTADO'] != 'Activo': continue
                
                nombre = row['NOMBRE']
                tipo = row['TIPO']
                monto = row['MONTO_A_PAGAR']
                
                # A) LÓGICA CÍCLICA (Tarjetas)
                if "Tarjeta" in tipo or (row.get('DIA_PAGO') and str(row['DIA_PAGO']).isdigit()):
                    dia_corte = int(row['DIA_CORTE']) if str(row['DIA_CORTE']).isdigit() else None
                    dia_pago = int(row['DIA_PAGO']) if str(row['DIA_PAGO']).isdigit() else None
                    
                    prox_corte = calcular_proxima_fecha(dia_corte)
                    prox_pago = calcular_proxima_fecha(dia_pago)
                    
                    if prox_pago:
                        dias_rest = (prox_pago - datetime.now().date()).days
                        calendario_pagos.append({"Fecha": prox_pago, "Evento": f"Pago {nombre}", "Monto": monto, "Tipo": "Pago"})
                        
                        if 0 <= dias_rest <= 5:
                            alertas.append(f"🔥 **{nombre}**: Pagar antes del {prox_pago.strftime('%d/%m')} (Faltan {dias_rest} días)")
                    
                    if prox_corte:
                        calendario_pagos.append({"Fecha": prox_corte, "Evento": f"Corte {nombre}", "Monto": 0, "Tipo": "Corte"})

                # B) LÓGICA ÚNICA (Préstamos)
                else:
                    try:
                        fecha_venc = pd.to_datetime(row['FECHA_VENCIMIENTO']).date()
                        dias_rest = (fecha_venc - datetime.now().date()).days
                        calendario_pagos.append({"Fecha": fecha_venc, "Evento": f"Vence {nombre}", "Monto": monto, "Tipo": "Vencimiento"})
                        
                        if 0 <= dias_rest <= 5:
                            alertas.append(f"⚠️ **{nombre}**: Vence el {fecha_venc.strftime('%d/%m')} (Faltan {dias_rest} días)")
                        elif dias_rest < 0:
                            alertas.append(f"☠️ **{nombre}**: VENCIDO hace {abs(dias_rest)} días")
                    except: pass
                    
    except Exception as e:
        st.error(f"Error procesando deudas: {e}")
        df_deudas = pd.DataFrame()

    return df_movs, df_deudas, alertas, calendario_pagos, sh

def guardar_registro(sh, hoja, datos):
    try:
        sh.worksheet(hoja).append_row(datos)
        st.cache_data.clear()
        return True
    except: return False

# ================= INTERFAZ =================
df_movs, df_deudas, alertas, calendario, sh_obj = cargar_datos_procesados(conectar_google())

# --- SIDEBAR ---
with st.sidebar:
    st.title("🎛️ Panel de Control")
    st.caption("v5.0 - Motor Cíclico Activo")
    
    # Nuevo Registro Inteligente
    with st.expander("📝 Nuevo Compromiso"):
        with st.form("nuevo_comp"):
            c_nombre = st.text_input("Nombre (ej: Tarjeta Oro)")
            c_tipo = st.selectbox("Tipo", ["Tarjeta Crédito (Cíclico)", "Préstamo Único"])
            c_monto = st.number_input("Monto / Pago Mensual", min_value=0.0)
            
            col_a, col_b = st.columns(2)
            if "Tarjeta" in c_tipo:
                dia_corte = col_a.number_input("Día de Corte", 1, 31, 15)
                dia_pago = col_b.number_input("Día Límite Pago", 1, 31, 5)
                fecha_venc = ""
            else:
                dia_corte = ""
                dia_pago = ""
                fecha_venc = st.date_input("Fecha Vencimiento")
            
            if st.form_submit_button("Guardar"):
                # Guarda en formato compatible con la hoja
                guardar_registro(sh_obj, "Deudas", [c_nombre, c_tipo, c_monto, dia_corte, dia_pago, str(fecha_venc), "Activo"])
                st.toast("Guardado exitosamente")
                time.sleep(1)
                st.rerun()

# --- ALERTAS INTELIGENTES ---
st.subheader(f"Hola, {st.secrets.get('admin_user','Admin')}")

if alertas:
    for a in alertas:
        if "🔥" in a: st.error(a)
        elif "⚠️" in a: st.warning(a)
        else: st.info(a)
else:
    st.success("✅ Todo tranquilo. No hay vencimientos urgentes en los próximos 5 días.")

st.divider()

# --- CALENDARIO VISUAL ---
st.markdown("### 📅 Tu Calendario Financiero")

if calendario:
    df_cal = pd.DataFrame(calendario).sort_values("Fecha")
    
    # Creamos columnas para simular un calendario lista
    for i, row in df_cal.iterrows():
        hoy = datetime.now().date()
        delta = (row['Fecha'] - hoy).days
        
        # Estilos visuales según cercanía
        if delta < 0: color = "gray"
        elif delta == 0: color = "#FF4B4B" # Hoy
        elif delta <= 7: color = "#FFA726" # Esta semana
        else: color = "#66BB6A" # Futuro
        
        with st.container():
            c1, c2, c3, c4 = st.columns([1, 4, 2, 2])
            c1.markdown(f"**{row['Fecha'].strftime('%d %b')}**")
            c2.write(f"{'🔴' if row['Tipo']=='Pago' else '✂️'} {row['Evento']}")
            if row['Monto'] > 0:
                c3.write(f"**${row['Monto']:,.2f}**")
            else:
                c3.write("-")
            
            # Botón de acción rápida (simulado)
            if delta >= 0:
                c4.caption(f"En {delta} días")
            else:
                c4.caption("Pasado")
            
            st.markdown(f"<div style='height:2px; background-color:{color}; margin-bottom:10px;'></div>", unsafe_allow_html=True)

else:
    st.info("No hay eventos próximos. Agrega deudas o tarjetas en el menú lateral.")

# --- SECCIÓN DE GASTOS DIARIOS ---
st.divider()
st.markdown("### 💸 Movimientos Recientes")
if not df_movs.empty:
    st.dataframe(df_movs.sort_values('FECHA', ascending=False).head(5), use_container_width=True)
