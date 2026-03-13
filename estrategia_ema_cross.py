"""
Script de Trading Automático - Estrategia Mora Trader (Apertura 9:30 AM NY)
Basado en: https://www.youtube.com/watch?v=paOAuskpOLA
Versión 3.0 - Foco en manipulación del rango de apertura (Judas Swing).
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import time
import requests
import warnings
import os
import sys
import mysql.connector
from mysql.connector import Error

# ==========================================
# GESTIÓN DE ARGUMENTOS Y VARIABLES DE ENTORNO
# ==========================================
try:
    from dotenv import load_dotenv
    argumento = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if argumento == 'local':
        print("🏠 MORA Modo: LOCAL (Cargando .env_local)")
        load_dotenv('.env_local')
    elif argumento == 'produccion':
        print("🌐 MORA Modo: PRODUCCIÓN (Cargando .env)")
        load_dotenv('.env')
    else:
        if os.path.exists('.env_local'):
            print("🤖 MORA Modo Auto: Local detectado (.env_local)")
            load_dotenv('.env_local')
        else:
            print("🤖 MORA Modo Auto: Producción detectado (.env)")
            load_dotenv('.env')
except ImportError:
    print("⚠️ Librería python-dotenv no instalada.")

warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURACIÓN
# ==========================================
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '45.22.208.171'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'M4JpWsOEoXZI8YKzAWYZd2a6iLYfyva4AX1EEmFFlg7OyXenl885ej2SVeexnBjM'),
    'database': os.getenv('DB_NAME', 'trades'),
    'port': int(os.getenv('DB_PORT', 3306))
}

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
APP_ENV = os.getenv('APP_ENV', 'produccion')

# ==========================================
# FUNCIONES DE SOPORTE
# ==========================================

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG, connect_timeout=5)
        return conn
    except Error as e:
        print(f"❌ Error DB Mora Trader: {e}")
        return None

def inicializar_db():
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_mora_ny (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha_apertura DATETIME,
                simbolo VARCHAR(20),
                tipo VARCHAR(10),
                precio_entrada FLOAT,
                rango_alto FLOAT,
                rango_bajo FLOAT,
                fecha_cierre DATETIME NULL,
                precio_salida FLOAT NULL,
                resultado VARCHAR(50) DEFAULT 'ABIERTA'
            )
        """)
        conn.commit()
    except Error as e:
        print(f"❌ Error inicializando tablas: {e}")
    finally:
        conn.close()

def enviar_telegram(mensaje):
    ids = []
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM usuarios")
            ids = [str(row[0]) for row in cursor.fetchall()]
        finally: conn.close()

    if not ids or not TELEGRAM_TOKEN: 
        print(f"📢 [MORA MSG]: {mensaje}")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try: requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=5)
        except: pass

# ==========================================
# LÓGICA DE LA ESTRATEGIA (NY OPEN)
# ==========================================

rangos_dia = {} # Guardar el High/Low de la vela de las 9:30
operaciones_activas = {}

def analizar_apertura_ny(simbolo):
    try:
        tz_ny = pytz.timezone('America/New_York')
        ahora_ny = datetime.now(tz_ny)
        
        # 1. Definir el Rango de la Vela de las 9:30 (15 min)
        if ahora_ny.hour == 9 and 45 <= ahora_ny.minute < 50:
            df = yf.download(simbolo, period='1d', interval='15m', progress=False)
            if df.empty: return
            
            # Buscar la vela que abre a las 09:30:00
            vela_930 = df.between_time('09:30', '09:31')
            if not vela_930.empty:
                rangos_dia[simbolo] = {
                    'alto': float(vela_930['High'].iloc[0]),
                    'bajo': float(vela_930['Low'].iloc[0]),
                    'manipulado_alto': False,
                    'manipulado_bajo': False
                }
                enviar_telegram(f"📌 *RANGO NY SET ({simbolo})*\nAlto: {rangos_dia[simbolo]['alto']:.5f}\nBajo: {rangos_dia[simbolo]['bajo']:.5f}")

        # 2. Buscar Manipulación y Entrada (Post 9:45 AM)
        if simbolo in rangos_dia and (ahora_ny.hour >= 9 and ahora_ny.minute >= 45) and ahora_ny.hour < 12:
            rango = rangos_dia[simbolo]
            df_actual = yf.download(simbolo, period='1d', interval='1m', progress=False)
            if df_actual.empty: return
            
            precio_actual = float(df_actual['Close'].iloc[-1])
            
            # Detectar Manipulación Superior (Busca Ventas)
            if precio_actual > rango['alto']:
                rango['manipulado_alto'] = True
            
            # Entrada en Venta: Si manipuló el alto y ahora vuelve a entrar al rango
            if rango['manipulado_alto'] and precio_actual < (rango['alto'] - (rango['alto'] * 0.0001)):
                if simbolo not in operaciones_activas:
                    operaciones_activas[simbolo] = {'tipo': 'SHORT', 'entrada': precio_actual}
                    registrar_entrada(simbolo, 'SHORT', precio_actual, rango)
                    enviar_telegram(f"📉 *ENTRADA SHORT (NY OPEN)*\nPar: {simbolo}\nPrecio: {precio_actual:.5f}\nMotivo: Recuperación tras manipulación superior.")

            # Detectar Manipulación Inferior (Busca Compras)
            if precio_actual < rango['bajo']:
                rango['manipulado_bajo'] = True
                
            # Entrada en Compra: Si manipuló el bajo y ahora vuelve a entrar al rango
            if rango['manipulado_bajo'] and precio_actual > (rango['bajo'] + (rango['bajo'] * 0.0001)):
                if simbolo not in operaciones_activas:
                    operaciones_activas[simbolo] = {'tipo': 'LONG', 'entrada': precio_actual}
                    registrar_entrada(simbolo, 'LONG', precio_actual, rango)
                    enviar_telegram(f"🚀 *ENTRADA LONG (NY OPEN)*\nPar: {simbolo}\nPrecio: {precio_actual:.5f}\nMotivo: Recuperación tras manipulación inferior.")

        # 3. Gestión de Cierre (Target: Extremo opuesto del rango)
        if simbolo in operaciones_activas:
            op = operaciones_activas[simbolo]
            rango = rangos_dia[simbolo]
            df_cierre = yf.download(simbolo, period='1d', interval='1m', progress=False)
            p_actual = float(df_cierre['Close'].iloc[-1])
            
            cerrar = False
            res = ""
            
            if op['tipo'] == 'LONG':
                if p_actual >= rango['alto']: # Take Profit en el alto del rango
                    cerrar, res = True, "TP: ALTO DEL RANGO ✅"
                elif p_actual < (op['entrada'] * 0.998): # Stop Loss
                    cerrar, res = True, "SL: MANIPULACIÓN FALLIDA ❌"
            else:
                if p_actual <= rango['bajo']: # Take Profit en el bajo del rango
                    cerrar, res = True, "TP: BAJO DEL RANGO ✅"
                elif p_actual > (op['entrada'] * 1.002): # Stop Loss
                    cerrar, res = True, "SL: MANIPULACIÓN FALLIDA ❌"
                    
            if cerrar:
                registrar_cierre(simbolo, p_actual, res)
                enviar_telegram(f"🏁 *CIERRE NY OPEN ({simbolo})*\nResultado: {res}\nPrecio: {p_actual:.5f}")
                del operaciones_activas[simbolo]
                del rangos_dia[simbolo] # Solo una operación por día según la estrategia

    except Exception as e:
        print(f"⚠️ Error en análisis NY: {e}")

def registrar_entrada(simbolo, tipo, precio, rango):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO historial_mora_ny (fecha_apertura, simbolo, tipo, precio_entrada, rango_alto, rango_bajo)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (datetime.now(), simbolo, tipo, precio, rango['alto'], rango['bajo']))
        conn.commit()
    finally: conn.close()

def registrar_cierre(simbolo, precio, res):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE historial_mora_ny 
            SET fecha_cierre = %s, precio_salida = %s, resultado = %s
            WHERE simbolo = %s AND resultado = 'ABIERTA'
            ORDER BY fecha_apertura DESC LIMIT 1
        """, (datetime.now(), precio, res, simbolo))
        conn.commit()
    finally: conn.close()

def ejecutar_bot():
    inicializar_db()
    activos = ['EURUSD=X', 'GBPUSD=X', 'NQ=F', 'ES=F']
    enviar_telegram("🏛️ *Mora Trader NY Open Activo*\nEsperando vela de las 9:30 AM NY...")

    while True:
        for activo in activos:
            analizar_apertura_ny(activo)
            time.sleep(1)
        time.sleep(60)

if __name__ == "__main__":
    ejecutar_bot()