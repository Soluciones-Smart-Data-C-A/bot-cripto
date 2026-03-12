"""
Script de Trading Automático - Estrategia Mora Trader (Apertura 9:30 AM NY)
Basado en: https://www.youtube.com/watch?v=paOAuskpOLA
Versión 2.0 - Foco en la vela de apertura y manipulación institucional.
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
    """Crea la tabla específica para la estrategia de Apertura NY."""
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_mora_trader (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha_apertura DATETIME,
                simbolo VARCHAR(20),
                tipo VARCHAR(10),
                apertura_930_high FLOAT,
                apertura_930_low FLOAT,
                entrada FLOAT,
                tp FLOAT,
                sl FLOAT,
                fecha_cierre DATETIME NULL,
                salida FLOAT NULL,
                resultado VARCHAR(20) DEFAULT 'ABIERTA',
                beneficio_rr FLOAT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id VARCHAR(50) PRIMARY KEY,
                first_name VARCHAR(100)
            )
        """)
        conn.commit()
    except Error as e:
        print(f"❌ Error inicializando tablas: {e}")
    finally:
        conn.close()

def registrar_apertura_mora(op):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO historial_mora_trader 
            (fecha_apertura, simbolo, tipo, apertura_930_high, apertura_930_low, entrada, tp, sl)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            datetime.now(), op['simbolo'], op['tipo'], 
            op['range_high'], op['range_low'],
            op['entrada'], op['tp'], op['sl']
        ))
        conn.commit()
    finally:
        conn.close()

def registrar_cierre_mora(simbolo, salida, resultado):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, entrada, sl FROM historial_mora_trader 
            WHERE simbolo = %s AND resultado = 'ABIERTA' 
            ORDER BY fecha_apertura DESC LIMIT 1
        """, (simbolo,))
        row = cursor.fetchone()
        if row:
            tid, entrada, sl = row
            riesgo = abs(entrada - sl)
            beneficio = abs(salida - entrada) / riesgo if riesgo != 0 else 0
            cursor.execute("""
                UPDATE historial_mora_trader 
                SET fecha_cierre = %s, salida = %s, resultado = %s, beneficio_rr = %s
                WHERE id = %s
            """, (datetime.now(), salida, resultado, round(beneficio, 2), tid))
            conn.commit()
    finally:
        conn.close()

def enviar_telegram(mensaje):
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM usuarios")
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    if not ids: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try: requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=5)
        except: pass

# ==========================================
# LÓGICA DE LA VELA DE LAS 9:30 AM NY
# ==========================================
operaciones_mora = []

class BotMoraTrader:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.tz_ny = pytz.timezone('America/New_York')
        self.range_high = None
        self.range_low = None

    def obtener_rango_apertura(self):
        """Identifica el High/Low de la vela de las 9:30 AM NY."""
        try:
            df = yf.download(self.simbolo, period='1d', interval='5m', progress=False)
            if df.empty: return False
            df.index = df.index.tz_convert(self.tz_ny)
            
            # Buscamos la vela exacta de las 09:30
            vela_930 = df[df.index.strftime('%H:%M') == '09:30']
            
            if not vela_930.empty:
                self.range_high = float(vela_930['High'].iloc[0])
                self.range_low = float(vela_930['Low'].iloc[0])
                return True
            return False
        except Exception as e:
            print(f"Error rango 9:30 {self.simbolo}: {e}")
            return False

    def buscar_entrada(self):
        """Busca la manipulación de la vela de 9:30 AM."""
        if any(op['simbolo'] == self.simbolo for op in operaciones_mora): return
        if self.range_high is None: return

        try:
            df_curr = yf.download(self.simbolo, period='1d', interval='1m', progress=False)
            if df_curr.empty: return
            
            precio_actual = float(df_curr['Close'].iloc[-1])
            v_reciente = df_curr.iloc[-2] # Vela cerrada anterior para confirmar reingreso

            nueva_op = None
            
            # LÓGICA: Manipulación por debajo del mínimo de las 9:30 y reingreso
            if v_reciente['Low'] < self.range_low and precio_actual > self.range_low:
                nueva_op = {
                    'simbolo': self.simbolo, 'tipo': 'LONG',
                    'range_high': self.range_high, 'range_low': self.range_low,
                    'entrada': precio_actual, 'tp': self.range_high, 'sl': float(v_reciente['Low'])
                }

            # LÓGICA: Manipulación por encima del máximo de las 9:30 y reingreso
            elif v_reciente['High'] > self.range_high and precio_actual < self.range_high:
                nueva_op = {
                    'simbolo': self.simbolo, 'tipo': 'SHORT',
                    'range_high': self.range_high, 'range_low': self.range_low,
                    'entrada': precio_actual, 'tp': self.range_low, 'sl': float(v_reciente['High'])
                }

            if nueva_op:
                operaciones_mora.append(nueva_op)
                registrar_apertura_mora(nueva_op)
                enviar_telegram(f"⚡ *APERTURA NY DETECTADA (9:30 AM)*\n"
                               f"📊 Par: {nueva_op['simbolo']}\n"
                               f"📍 Rango Vela 9:30: {self.range_low:.5f} - {self.range_high:.5f}\n"
                               f"🚀 Entrada: {precio_actual:.5f}\n"
                               f"🎯 TP: {nueva_op['tp']:.5f} | 🛑 SL: {nueva_op['sl']:.5f}")
        except Exception as e:
            print(f"Error entrada {self.simbolo}: {e}")

def seguimiento_trades():
    global operaciones_mora
    for op in operaciones_mora[:]:
        try:
            ticker = yf.Ticker(op['simbolo'])
            p = ticker.fast_info['last_price']
            cerro, res = False, ""
            if op['tipo'] == 'LONG':
                if p >= op['tp']: cerro, res = True, "TAKE PROFIT"
                elif p <= op['sl']: cerro, res = True, "STOP LOSS"
            else:
                if p <= op['tp']: cerro, res = True, "TAKE PROFIT"
                elif p >= op['sl']: cerro, res = True, "STOP LOSS"
            
            if cerro:
                registrar_cierre_mora(op['simbolo'], p, res)
                enviar_telegram(f"🏁 *TRADE FINALIZADO*\nResultado: {res}\nPar: {op['simbolo']}\nPrecio: {p:.5f}")
                operaciones_mora.remove(op)
        except: pass

def ejecutar_bot():
    inicializar_db()
    activos = ['EURUSD=X', 'GBPUSD=X', 'BTC-USD', 'SOL-USD']
    tz_ny = pytz.timezone('America/New_York')
    enviar_telegram("📊 *Bot Mora Trader v2.0 Activo*\nEstrategia: Vela de Apertura 9:30 AM NY.")

    while True:
        ahora_ny = datetime.now(tz_ny)
        
        # El bot empieza a buscar el rango a partir de las 9:31 AM
        # Y busca entradas durante los siguientes 90 minutos (hasta las 11:00 AM)
        if 9 <= ahora_ny.hour <= 11:
            if ahora_ny.hour == 9 and ahora_ny.minute < 31:
                # Esperando a que cierre la vela de las 9:30
                time.sleep(30)
                continue
                
            for simbolo in activos:
                bot = BotMoraTrader(simbolo)
                if bot.obtener_rango_apertura():
                    bot.buscar_entrada()
            seguimiento_trades()
            time.sleep(60) 
        else:
            if operaciones_mora:
                seguimiento_trades()
            time.sleep(60)

if __name__ == "__main__":
    ejecutar_bot()