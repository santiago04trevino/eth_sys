# ===============================================
# Importamos las librerías
# ===============================================
import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import os
import google.generativeai as genai

# Configuración inicial de la página web
st.set_page_config(page_title="Simulador de Etanol", page_icon="🌱", layout="wide")

st.title("🌱 Simulador Web: Planta de Etanol")
st.markdown("Esta aplicación ejecuta una simulación termodinámica en tiempo real utilizando **Biosteam** y analiza los resultados con IA.")

# ===============================================
# 1. PARÁMETROS DINÁMICOS (INTERFAZ WEB)
# ===============================================
st.sidebar.header("Parámetros de Operación")
st.sidebar.markdown("Modifica los valores para recalcular el balance de materia y energía.")

temp_mosto = st.sidebar.slider("Temperatura inicial del Mosto (°C)", 20.0, 40.0, 25.0, step=1.0)
flujo_agua = st.sidebar.slider("Flujo de Agua en Mosto (kmol/h)", 30.0, 60.0, 43.2, step=0.1)

# ===============================================
# 2. FUNCIÓN PRINCIPAL DE SIMULACIÓN
# ===============================================
def run_simulation(t_mosto, f_agua):
    # ¡CRÍTICO! Limpiar la memoria de Biosteam en cada recarga para evitar errores de ID duplicado
    bst.main_flowsheet.clear()
    
    # Definir compuestos y termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes
    mosto = bst.Stream("1-MOSTO", Water=f_agua, Ethanol=4.9, units="kmol/h",
                       T=t_mosto + 273.15, P=101325)
    
    vinazas_retorno = bst.Stream("Vinazas-Retorno", Water=43.335, Ethanol=0, units="kmol/h",
                                 T=90 + 273.15, P=300000)

    # Equipos
    P100 = bst.Pump("P-100", ins=mosto, P=4*101325)
    
    W210 = bst.HXprocess("W-210", ins=(P100-0, vinazas_retorno),
                         outs=("3-MOSTO-PRE", "DRENAJE"),
                         phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15 # Especificación de diseño
    
    W220 = bst.HXutility("W-220", ins=W210-0, outs="Mezcla", T=95+273.15)
    
    V100 = bst.IsenthalpicValve("V-100", ins=W220-0, outs="Mezcla-Bifásica", P=101325)
    
    V1 = bst.Flash("V-1", ins=V100-0, outs=("Vapor Caliente", "Vinazas"), P=101325, Q=0)
    
    W310 = bst.HXutility("W-310", ins=V1-0, outs="Producto Final", T=25+273.15)
    
    P200 = bst.Pump("P-200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Creación y ejecución del sistema
    eth_sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    eth_sys.simulate()
    
    return eth_sys

# ===============================================
# 3. FUNCIÓN PARA GENERAR REPORTES
# ===============================================
def generar_reporte(sistema):
    # --- TABLA DE CORRIENTES ---
    datos_mat = []
    for s in sistema.streams:
        if s.F_mass > 0:
            datos_mat.append({
                "ID Corriente": s.ID,
                "Temperatura (°C)": f"{s.T-273.15:.2f}",
                "Presión (bar)": f"{s.P/1e5:.2f}",
                "Flujo (kg/h)": f"{s.F_mass:.2f}",
                "Porcentaje Etanol (%)": f"{(s.imass['Ethanol']/s.F_mass)*100:.1f}",
                "Porcentaje Agua (%)": f"{(s.imass['Water']/s.F_mass)*100:.1f}"
            })
    df_mat = pd.DataFrame(datos_mat).set_index("ID Corriente")

    # --- TABLA DE ENERGÍA ---
    datos_en = []
    for u in sistema.units:
        calor_kw = 0.0
        tipo_servicio = "-"
        potencia = 0.0

        # Intercambiadores de proceso (recuperación interna)
        if isinstance(u, bst.HXprocess):
            calor_kw = (u.outs[0].H - u.ins[0].H) / 3600
            tipo_servicio = "Recuperación Interna"
            
        # Equipos con duty (Servicios auxiliares) - Modificado para evitar error en Flash isentálpico
        elif getattr(u, "duty", None) is not None:
            calor_kw = u.duty / 3600
            if calor_kw > 0.01: tipo_servicio = "Calentamiento (Vapor)"
            elif calor_kw < -0.01: tipo_servicio = "Enfriamiento (Agua)"
            else: tipo_servicio = "Adiabático"

        # Potencia Eléctrica (Motores/Bombas)
        if hasattr(u, "power_utility") and u.power_utility:
            potencia = u.power_utility.rate

        # Filtrar y agregar solo equipos que consumen o transfieren energía
        if abs(calor_kw) > 0.01:
            datos_en.append({"ID Equipo": u.ID, "Función": tipo_servicio, "Energía Térmica (kW)": f"{calor_kw:.2f}", "Energía Eléctrica (kW)": "-"})
        if potencia > 0.01:
            datos_en.append({"ID Equipo": u.ID, "Función": "Motor bomba", "Energía Térmica (kW)": "-", "Energía Eléctrica (kW)": f"{potencia:.2f}"})

    df_en = pd.DataFrame(datos_en).set_index("ID Equipo")
    return df_mat, df_en

# ===============================================
# 4. EJECUCIÓN Y VISUALIZACIÓN EN LA WEB
# ===============================================
try:
    # Mostramos un spinner de carga mientras Biosteam hace los cálculos
    with st.spinner("Ejecutando simulación termodinámica..."):
        sistema_simulado = run_simulation(temp_mosto, flujo_agua)
        df_materia, df_energia = generar_reporte(sistema_simulado)
        
    st.success("✅ ¡Convergencia exitosa! El balance ha finalizado.")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📦 Balance de Materia")
        st.dataframe(df_materia, use_container_width=True)
    with col2:
        st.subheader("⚡ Balance de Energía")
        st.dataframe(df_energia, use_container_width=True)

except Exception as e:
    st.error(f"⚠️ Error en la convergencia del sistema: {e}")

# ===============================================
# 5. DIAGRAMA DE FLUJO
# ===============================================
st.divider()
st.subheader("🗺️ Diagrama de Flujo del Proceso")
try:
    nombre_archivo = "diagrama_etanol"
    sistema_simulado.diagram(file=nombre_archivo, format="png")
    st.image(f"{nombre_archivo}.png", use_container_width=True)
except Exception as e:
    st.warning(f"No se pudo renderizar el diagrama gráfico. Asegúrate de tener 'graphviz' instalado en el sistema. Error técnico: {e}")

# ===============================================
# 6. INTEGRACIÓN DE IA (TUTOR VIRTUAL GEMINI)
# ===============================================
st.divider()
st.subheader("🤖 Tutor Virtual de Ingeniería Química")
st.markdown("Envía los resultados actuales a Gemini para obtener un análisis técnico del proceso.")

if st.button("Solicitar Análisis a Gemini", type="primary"):
    # Verifica si la clave API está configurada en los Secrets de Streamlit
    if "GEMINI_API_KEY" not in st.secrets:
        st.error("Falta configurar la GEMINI_API_KEY en los Secrets de Streamlit.")
    else:
        with st.spinner("Gemini está analizando los balances..."):
            try:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                modelo = genai.GenerativeModel('gemini-2.5-pro')
                
                # Prompt estructurado con las tablas en formato Markdown
                prompt = f"""
                Actúa como un tutor experto en Ingeniería Química. 
                El usuario acaba de simular una planta de producción de etanol usando Biosteam.
                
                Aquí están los resultados del Balance de Materia (las columnas son características de flujo):
                {df_materia.to_markdown()}
                
                Aquí están los resultados del Balance de Energía:
                {df_energia.to_markdown()}
                
                Por favor, revisa brevemente la viabilidad térmica del sistema, 
                identifica cuál es el equipo que más energía consume, y da 1 recomendación 
                para optimizar la recuperación de calor en este proceso. Explícalo con un tono profesional pero accesible.
                """
                
                respuesta = modelo.generate_content(prompt)
                st.info("💡 **Análisis de Gemini:**")
                st.write(respuesta.text)
                
            except Exception as e:
                st.error(f"Error al conectar con la API de Gemini: {e}")
