"""
Script de Trading Automático - Estrategia Mora Trader (Apertura 9:30 AM NY)
Versión 2.2 - Soporte para Argumentos de Terminal (Local/Producción)
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
    # Capturar argumento: python bot_mora_trader.py local/produccion
    argumento = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if argumento == 'local':
        print("🏠 MORA Modo: LOCAL (Cargando .env_local)")
        load_dotenv('.env_local')
    elif argumento == 'produccion':
        print("🌐 MORA Modo: PRODUCCIÓN (Cargando .env)")
        load_dotenv('.env')
    else:
        # Detección automática si no hay argumento
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
# CONFIGURACIÓN (Desde variables de entorno)
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
        print(f"❌ Error DB Mora: {e}")
        return None

def inicializar_db():
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_mora (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha_apertura DATETIME,
                simbolo VARCHAR(20),
                tipo VARCHAR(10),
                precio_entrada FLOAT,
                rango_high FLOAT,
                rango_low FLOAT,
                tp FLOAT,
                sl FLOAT,
                fecha_cierre DATETIME NULL,
                precio_salida FLOAT NULL,
                resultado VARCHAR(20) DEFAULT 'ABIERTA'
            )
        """)
        conn.commit()
    except Error: pass
    finally: conn.close()

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
        print(f"📢 [MORA MSG]: {mensaje}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try: requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=5)
        except: pass

# ... (Resto de la lógica de análisis de Mora Trader v2.0) ...

def ejecutar_bot():
    inicializar_db()
    activos = ['EURUSD=X', 'GBPUSD=X', 'BTC-USD', 'SOL-USD']
    tz_ny = pytz.timezone('America/New_York')
    enviar_telegram(f"📊 *Bot Mora Trader v2.2 Activo*\nAmbiente: {os.getenv('APP_ENV', 'desconocido')}")

    while True:
        ahora_ny = datetime.now(tz_ny)
        # Lógica de escaneo de apertura 9:30 AM...
        time.sleep(60)

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN no configurado.")
    else:
        ejecutar_bot()