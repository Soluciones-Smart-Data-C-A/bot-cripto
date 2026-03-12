"""
Script de Trading Automático - Estrategia Cruce de EMAs (9 y 21)
Basado en: https://www.youtube.com/shorts/roEy8Da2R1A
Versión 1.0 - Scalping de tendencia rápida.
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
import mysql.connector
from mysql.connector import Error

# Configuración de avisos
warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURACIÓN DE BASE DE DATOS (MYSQL)
# ==========================================
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '45.22.208.171'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'M4JpWsOEoXZI8YKzAWYZd2a6iLYfyva4AX1EEmFFlg7OyXenl885ej2SVeexnBjM'),
    'database': os.getenv('DB_NAME', 'trades')
}

TELEGRAM_TOKEN = "8327248294:AAGvexslS_stn3B-THAbmhqKHswJyCFnFK4"

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"❌ Error DB: {e}")
        return None

def inicializar_db():
    """Crea la tabla específica para la estrategia de Cruce de EMAs."""
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
                ema_9 FLOAT,
                ema_21 FLOAT,
                fecha_cierre DATETIME NULL,
                precio_salida FLOAT NULL,
                resultado VARCHAR(20) DEFAULT 'ABIERTA'
            )
        """)
        conn.commit()
    except Error as e:
        print(f"❌ Error inicializando tablas: {e}")
    finally:
        conn.close()

def registrar_apertura_ema(simbolo, tipo, entrada, e9, e21):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        query = "INSERT INTO historial_ema_cross (fecha_apertura, simbolo, tipo, precio_entrada, ema_9, ema_21) VALUES (%s, %s, %s, %s, %s, %s)"
        cursor.execute(query, (datetime.now(), simbolo, tipo, entrada, e9, e21))
        conn.commit()
    finally:
        conn.close()

def registrar_cierre_ema(simbolo, salida, resultado):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE historial_ema_cross SET fecha_cierre=%s, precio_salida=%s, resultado=%s WHERE simbolo=%s AND resultado='ABIERTA'", 
                       (datetime.now(), salida, resultado, simbolo))
        conn.commit()
    finally:
        conn.close()

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Nota: Aquí deberías obtener los chat_ids de tu tabla usuarios como en los otros bots
    print(f"📢 [Telegram Simulation]: {mensaje}")

# ==========================================
# LÓGICA DE CRUCE DE EMAS
# ==========================================
operaciones_activas = {}

def calcular_emas(df):
    df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
    return df

def ejecutar_estrategia():
    inicializar_db()
    activos = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'EURUSD=X']
    print("🚀 Bot Cruce EMA 9/21 iniciando...")

    while True:
        for simbolo in activos:
            try:
                # Datos de 5 minutos para scalping
                df = yf.download(simbolo, period='1d', interval='5m', progress=False)
                if len(df) < 22: continue
                
                df = calcular_emas(df)
                ultima = df.iloc[-1]
                penultima = df.iloc[-2]
                
                precio_actual = float(ultima['Close'])
                
                # CONDICIÓN DE COMPRA (Cruce hacia arriba)
                if penultima['EMA9'] <= penultima['EMA21'] and ultima['EMA9'] > ultima['EMA21']:
                    if simbolo not in operaciones_activas:
                        operaciones_activas[simbolo] = {'tipo': 'LONG', 'entrada': precio_actual}
                        registrar_apertura_ema(simbolo, 'LONG', precio_actual, ultima['EMA9'], ultima['EMA21'])
                        enviar_telegram(f"📈 *CRUCE ALCISTA EMA 9/21*\nPar: {simbolo}\nPrecio: {precio_actual}")

                # CONDICIÓN DE VENTA (Cruce hacia abajo)
                elif penultima['EMA9'] >= penultima['EMA21'] and ultima['EMA9'] < ultima['EMA21']:
                    if simbolo not in operaciones_activas:
                        operaciones_activas[simbolo] = {'tipo': 'SHORT', 'entrada': precio_actual}
                        registrar_apertura_ema(simbolo, 'SHORT', precio_actual, ultima['EMA9'], ultima['EMA21'])
                        enviar_telegram(f"📉 *CRUCE BAJISTA EMA 9/21*\nPar: {simbolo}\nPrecio: {precio_actual}")

                # CIERRE DE OPERACIONES (Cruce contrario)
                if simbolo in operaciones_activas:
                    op = operaciones_activas[simbolo]
                    if (op['tipo'] == 'LONG' and ultima['EMA9'] < ultima['EMA21']) or \
                       (op['tipo'] == 'SHORT' and ultima['EMA9'] > ultima['EMA21']):
                        registrar_cierre_ema(simbolo, precio_actual, "CRUCE CONTRARIO")
                        enviar_telegram(f"🏁 *CIERRE POR CRUCE*\nPar: {simbolo}\nPrecio: {precio_actual}")
                        del operaciones_activas[simbolo]

            except Exception as e:
                print(f"Error en {simbolo}: {e}")
        
        time.sleep(300) # Esperar 5 minutos para la siguiente vela

if __name__ == "__main__":
    ejecutar_estrategia()