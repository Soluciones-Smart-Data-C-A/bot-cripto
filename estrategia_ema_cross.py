"""
Script de Trading Automático - Estrategia Cruce de EMAs (9 y 21)
Versión 1.5 - Incluye cálculo de SL y TP (Ratio 1:2)

Videos de referencia:
- Estrategia Base: https://www.youtube.com/shorts/roEy8Da2R1A
- Gestión y Contexto: https://www.youtube.com/watch?v=paOAuskpOLA (Mora Trader Conceptos)
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
    argumento = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if argumento == 'local':
        print("🏠 EMA Modo: LOCAL (Cargando .env_local)")
        load_dotenv('.env_local')
    elif argumento == 'produccion':
        print("🌐 EMA Modo: PRODUCCIÓN (Cargando .env)")
        load_dotenv('.env')
    else:
        if os.path.exists('.env_local'):
            print("🤖 EMA Modo Auto: Local detectado (.env_local)")
            load_dotenv('.env_local')
        else:
            print("🤖 EMA Modo Auto: Producción detectado (.env)")
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_ema_cross (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha_apertura DATETIME,
                simbolo VARCHAR(20),
                tipo VARCHAR(10),
                precio_entrada FLOAT,
                sl FLOAT,
                tp FLOAT,
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

def registrar_apertura_ema(simbolo, tipo, precio, sl, tp, e9, e21):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO historial_ema_cross (fecha_apertura, simbolo, tipo, precio_entrada, sl, tp, ema_9, ema_21)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (datetime.now(), simbolo, tipo, precio, sl, tp, e9, e21))
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

operaciones_activas = {}

def analizar_cruce(simbolo):
    try:
        # Descarga de datos para análisis
        df = yf.download(simbolo, period='2d', interval='15m', progress=False, auto_adjust=True)
        if df.empty: return

        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

        # Cálculo de indicadores según el video (EMA 9 y 21)
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()

        ult = df.iloc[-1]
        pen = df.iloc[-2]
        
        precio_actual = float(ult['Close'])
        e9_ult, e21_ult = float(ult['EMA9']), float(ult['EMA21'])
        e9_pen, e21_pen = float(pen['EMA9']), float(pen['EMA21'])

        # --- LÓGICA DE ENTRADA CON SL Y TP ---
        
        # COMPRA (LONG): Cruce hacia arriba
        if e9_pen <= e21_pen and e9_ult > e21_ult:
            if simbolo not in operaciones_activas:
                # SL dinámico sugerido: Mínimo de la vela previa al cruce
                sl = float(pen['Low'])
                riesgo = precio_actual - sl
                if riesgo <= 0: riesgo = precio_actual * 0.001
                tp = precio_actual + (riesgo * 2) # Ratio 1:2 según mejores prácticas
                
                operaciones_activas[simbolo] = {'tipo': 'LONG', 'entrada': precio_actual, 'sl': sl, 'tp': tp}
                registrar_apertura_ema(simbolo, 'LONG', precio_actual, sl, tp, e9_ult, e21_ult)
                
                enviar_telegram(
                    f"📈 *NUEVA COMPRA EMA 9/21*\n"
                    f"Par: {simbolo}\n"
                    f"Precio: {precio_actual:.5f}\n"
                    f"🚫 SL: {sl:.5f}\n"
                    f"🎯 TP: {tp:.5f}"
                )

        # VENTA (SHORT): Cruce hacia abajo
        elif e9_pen >= e21_pen and e9_ult < e21_ult:
            if simbolo not in operaciones_activas:
                # SL dinámico sugerido: Máximo de la vela previa al cruce
                sl = float(pen['High'])
                riesgo = sl - precio_actual
                if riesgo <= 0: riesgo = precio_actual * 0.001
                tp = precio_actual - (riesgo * 2)
                
                operaciones_activas[simbolo] = {'tipo': 'SHORT', 'entrada': precio_actual, 'sl': sl, 'tp': tp}
                registrar_apertura_ema(simbolo, 'SHORT', precio_actual, sl, tp, e9_ult, e21_ult)
                
                enviar_telegram(
                    f"📉 *NUEVA VENTA EMA 9/21*\n"
                    f"Par: {simbolo}\n"
                    f"Precio: {precio_actual:.5f}\n"
                    f"🚫 SL: {sl:.5f}\n"
                    f"🎯 TP: {tp:.5f}"
                )

        # --- LÓGICA DE SEGUIMIENTO ---
        if simbolo in operaciones_activas:
            op = operaciones_activas[simbolo]
            resultado = None
            
            if op['tipo'] == 'LONG':
                if precio_actual >= op['tp']: resultado = "TAKE PROFIT ✅"
                elif precio_actual <= op['sl']: resultado = "STOP LOSS ❌"
                elif e9_ult < e21_ult: resultado = "CIERRE POR CRUCE 🔄"
            else:
                if precio_actual <= op['tp']: resultado = "TAKE PROFIT ✅"
                elif precio_actual >= op['sl']: resultado = "STOP LOSS ❌"
                elif e9_ult > e21_ult: resultado = "CIERRE POR CRUCE 🔄"

            if resultado:
                registrar_cierre_ema(simbolo, precio_actual, resultado)
                enviar_telegram(f"🏁 *CIERRE {simbolo}*\nResultado: {resultado}\nPrecio: {precio_actual:.5f}")
                del operaciones_activas[simbolo]

    except Exception as e:
        print(f"⚠️ Error analizando {simbolo}: {e}")

def ejecutar_bot():
    inicializar_db()
    # Activos propuestos por volatilidad y respuesta a EMAs
    activos = ['BTC-USD', 'ETH-USD', 'EURUSD=X', 'GBPUSD=X']
    enviar_telegram(f"🚀 *Bot EMA Cross v1.5 Activo*\nAmbiente: {APP_ENV.upper()}")

    while True:
        for activo in activos:
            analizar_cruce(activo)
            time.sleep(2)
        time.sleep(300) # Revisión cada 5 minutos

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN no configurado.")
    else:
        ejecutar_bot()