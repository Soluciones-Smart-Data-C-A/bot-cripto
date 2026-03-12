"""
Script de Trading Automático - Estrategia Cruce de EMAs (9 y 21)
Versión 1.4 - Soporte para Argumentos de Terminal (Local/Producción)
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
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
    # Capturar argumento: python estrategia_ema_cross.py local/produccion
    argumento = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if argumento == 'local':
        print("🏠 EMA Modo: LOCAL (Cargando .env_local)")
        load_dotenv('.env_local')
    elif argumento == 'produccion':
        print("🌐 EMA Modo: PRODUCCIÓN (Cargando .env)")
        load_dotenv('.env')
    else:
        # Detección automática por existencia de archivo si no hay argumento
        if os.path.exists('.env_local'):
            print("🤖 EMA Modo Auto: Local detectado (.env_local)")
            load_dotenv('.env_local')
        else:
            print("🤖 EMA Modo Auto: Producción detectado (.env)")
            load_dotenv('.env')
except ImportError:
    print("⚠️ Librería python-dotenv no instalada. Usando variables de entorno del sistema.")

warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURACIÓN (Desde archivos .env)
# ==========================================
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '45.22.208.171'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'M4JpWsOEoXZI8YKzAWYZd2a6iLYfyva4AX1EEmFFlg7OyXenl885ej2SVeexnBjM'),
    'database': os.getenv('DB_NAME', 'trades'),
    'port': int(os.getenv('DB_PORT', 3306))
}

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG, connect_timeout=5)
        return conn
    except Error as e:
        print(f"❌ Error DB EMA Cross: {e}")
        return None

def inicializar_db():
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        # Tabla específica para esta estrategia
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_ema_cross (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha_apertura DATETIME,
                simbolo VARCHAR(20),
                tipo VARCHAR(10),
                precio_entrada FLOAT,
                ema_9 FLOAT,
                ema_21 FLOAT,
                fecha_cierre DATETIME NULL,
                precio_salida FLOAT NULL,
                resultado VARCHAR(20) DEFAULT 'ABIERTA'
            )
        """)
        conn.commit()
    except Error as e:
        print(f"❌ Error inicializando tablas EMA: {e}")
    finally:
        conn.close()

def obtener_suscriptores():
    conn = get_db_connection()
    ids = []
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM usuarios")
            ids = [str(row[0]) for row in cursor.fetchall()]
        finally:
            conn.close()
    return ids

def enviar_telegram(mensaje):
    ids = obtener_suscriptores()
    if not ids or not TELEGRAM_TOKEN: 
        print(f"📢 [EMA MSG]: {mensaje}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            print(f"⚠️ Error enviando Telegram: {e}")

def registrar_apertura_ema(simbolo, tipo, precio, e9, e21):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO historial_ema_cross (fecha_apertura, simbolo, tipo, precio_entrada, ema_9, ema_21)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (datetime.now(), simbolo, tipo, precio, e9, e21))
        conn.commit()
    finally:
        conn.close()

def registrar_cierre_ema(simbolo, precio_salida, resultado):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE historial_ema_cross 
            SET fecha_cierre = %s, precio_salida = %s, resultado = %s
            WHERE simbolo = %s AND resultado = 'ABIERTA'
            ORDER BY fecha_apertura DESC LIMIT 1
        """, (datetime.now(), precio_salida, resultado, simbolo))
        conn.commit()
    finally:
        conn.close()

# Estado de operaciones en memoria
operaciones_activas = {}

def analizar_cruce(simbolo):
    try:
        df = yf.download(simbolo, period='2d', interval='15m', progress=False, auto_adjust=True)
        if df.empty: return

        # Manejo de MultiIndex si es necesario
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

        # Calcular EMAs
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()

        ult = df.iloc[-1]
        pen = df.iloc[-2]
        
        precio_actual = float(ult['Close'])
        e9_ult, e21_ult = float(ult['EMA9']), float(ult['EMA21'])
        e9_pen, e21_pen = float(pen['EMA9']), float(pen['EMA21'])

        # Lógica de Cruce
        # COMPRA: EMA9 cruza hacia arriba EMA21
        if e9_pen <= e21_pen and e9_ult > e21_ult:
            if simbolo not in operaciones_activas:
                operaciones_activas[simbolo] = {'tipo': 'LONG', 'entrada': precio_actual}
                registrar_apertura_ema(simbolo, 'LONG', precio_actual, e9_ult, e21_ult)
                enviar_telegram(f"📈 *CRUCE ALCISTA EMA 9/21*\nPar: {simbolo}\nPrecio: {precio_actual:.5f}")

        # VENTA: EMA9 cruza hacia abajo EMA21
        elif e9_pen >= e21_pen and e9_ult < e21_ult:
            if simbolo not in operaciones_activas:
                operaciones_activas[simbolo] = {'tipo': 'SHORT', 'entrada': precio_actual}
                registrar_apertura_ema(simbolo, 'SHORT', precio_actual, e9_ult, e21_ult)
                enviar_telegram(f"📉 *CRUCE BAJISTA EMA 9/21*\nPar: {simbolo}\nPrecio: {precio_actual:.5f}")

        # Lógica de Cierre por Cruce Contrario
        if simbolo in operaciones_activas:
            op = operaciones_activas[simbolo]
            if (op['tipo'] == 'LONG' and e9_ult < e21_ult) or \
               (op['tipo'] == 'SHORT' and e9_ult > e21_ult):
                registrar_cierre_ema(simbolo, precio_actual, "CRUCE CONTRARIO")
                enviar_telegram(f"🏁 *CIERRE POR CRUCE* ({simbolo})\nPrecio: {precio_actual:.5f}")
                del operaciones_activas[simbolo]

    except Exception as e:
        print(f"⚠️ Error analizando {simbolo}: {e}")

def ejecutar_bot():
    inicializar_db()
    activos = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'EURUSD=X', 'GBPUSD=X']
    enviar_telegram(f"🚀 *Bot EMA Cross v1.4 Activo*\nAmbiente: {os.getenv('APP_ENV', 'producción')}")

    while True:
        for activo in activos:
            analizar_cruce(activo)
            time.sleep(2) # Evitar rate limit de API
        
        # Esperar 5 minutos para la próxima vela de 15m (o según el intervalo deseado)
        time.sleep(300)

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN no encontrado en el entorno.")
    else:
        ejecutar_bot()