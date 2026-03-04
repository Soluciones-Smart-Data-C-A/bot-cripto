"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 6.8 - Bias dinámico basado en velas de 1H tras cierre de 12H (5 AM NY).
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import pytz
import time
import requests
import warnings
import threading
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

# ==========================================
# CONFIGURACIÓN DE NOTIFICACIONES (TELEGRAM)
# ==========================================
TELEGRAM_TOKEN = "8327248294:AAGvexslS_stn3B-THAbmhqKHswJyCFnFK4"

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"❌ Error conectando a MySQL: {e}")
        return None

def inicializar_db():
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id VARCHAR(50) PRIMARY KEY,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                username VARCHAR(100),
                fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_trades (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha_apertura DATETIME,
                simbolo VARCHAR(20),
                tipo VARCHAR(10),
                bias_12h VARCHAR(10),
                rango_high_12h FLOAT,
                rango_low_12h FLOAT,
                entrada FLOAT,
                tp FLOAT,
                sl FLOAT,
                fecha_cierre DATETIME NULL,
                salida FLOAT NULL,
                resultado VARCHAR(20) DEFAULT 'ABIERTA',
                pips_profit FLOAT NULL
            )
        """)
        conn.commit()
    except Error as e:
        print(f"❌ Error inicializando tablas: {e}")
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

def registrar_apertura_db(op):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO historial_trades 
            (fecha_apertura, simbolo, tipo, bias_12h, rango_high_12h, rango_low_12h, entrada, tp, sl)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            datetime.now(), op['simbolo'], op['tipo'], 
            op['bias_12h'], op['r_high'], op['r_low'],
            op['entrada'], op['tp'], op['sl']
        ))
        conn.commit()
    finally:
        conn.close()

def registrar_cierre_db(simbolo, salida, resultado):
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, entrada, tipo FROM historial_trades 
            WHERE simbolo = %s AND resultado = 'ABIERTA' 
            ORDER BY fecha_apertura DESC LIMIT 1
        """, (simbolo,))
        row = cursor.fetchone()
        if row:
            trade_id, entrada, tipo = row
            pips = (salida - entrada) if tipo == 'LONG' else (entrada - salida)
            cursor.execute("""
                UPDATE historial_trades 
                SET fecha_cierre = %s, salida = %s, resultado = %s, pips_profit = %s
                WHERE id = %s
            """, (datetime.now(), salida, resultado, round(pips, 5), trade_id))
            conn.commit()
    finally:
        conn.close()

def enviar_telegram(mensaje):
    ids = obtener_suscriptores()
    if not ids: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try: requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=10)
        except: pass

# ==========================================
# LÓGICA DE TRADING MEJORADA
# ==========================================
operaciones_activas = []
notificado_fin_sesion = False
bias_memoria = {} 

class EstrategiaCRT:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.tz_ny = pytz.timezone('America/New_York')
        self.datos_1h = None
        self.rango_high = None
        self.rango_low = None

    def descargar_y_analizar(self):
        """
        Obtiene el rango de la vela de 12H que cerró a las 5 AM NY.
        No espera el cierre de la vela actual.
        """
        try:
            df = yf.download(self.simbolo, period='5d', interval='1h', progress=False, auto_adjust=True)
            if df.empty: return False
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            df.index = df.index.tz_convert(self.tz_ny)
            self.datos_1h = df
            
            # Obtenemos velas de 12H cerradas (offset 5h alinea con 5am/5pm NY)
            df_12h = df.resample('12h', offset='5h').agg({
                'High':'max', 'Low':'min', 'Close':'last'
            }).dropna()
            
            if len(df_12h) < 1: return False
            
            # v_referencia es la vela que TERMINÓ a las 5 AM NY
            v_referencia = df_12h.iloc[-1]
            self.rango_high = float(v_referencia['High'])
            self.rango_low = float(v_referencia['Low'])
            
            return True
        except Exception as e:
            print(f"Error {self.simbolo}: {e}")
            return False

    def chequear_entrada(self):
        """
        Busca manipulación en las velas de 1H respecto al rango de 12H previo.
        """
        if any(op['simbolo'] == self.simbolo for op in operaciones_activas): return
        
        # Analizamos las últimas 2 velas de 1 hora para detectar el reingreso (Paso 2 y 3)
        v_previa_1h = self.datos_1h.iloc[-2]
        v_actual_1h = self.datos_1h.iloc[-1]
        
        precio_actual = float(v_actual_1h['Close'])
        bias_detectado = None
        nueva_op = None

        # LÓGICA DE COMPRA (LONG):
        # 1. La vela de 1H (o la anterior) bajó del Rango Low de 12H (Manipulación)
        # 2. El precio actual de 1H cerró por encima del Rango Low de 12H (Reingreso)
        if v_actual_1h['Low'] < self.rango_low and v_actual_1h['Close'] > self.rango_low:
            bias_detectado = 'COMPRA'
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'LONG', 'bias_12h': bias_detectado,
                'r_high': self.rango_high, 'r_low': self.rango_low,
                'entrada': precio_actual, 'tp': self.rango_high, 'sl': float(v_actual_1h['Low'])
            }

        # LÓGICA DE VENTA (SHORT):
        # 1. La vela de 1H (o la anterior) subió del Rango High de 12H (Manipulación)
        # 2. El precio actual de 1H cerró por debajo del Rango High de 12H (Reingreso)
        elif v_actual_1h['High'] > self.rango_high and v_actual_1h['Close'] < self.rango_high:
            bias_detectado = 'VENTA'
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'SHORT', 'bias_12h': bias_detectado,
                'r_high': self.rango_high, 'r_low': self.rango_low,
                'entrada': precio_actual, 'tp': self.rango_low, 'sl': float(v_actual_1h['High'])
            }

        if nueva_op:
            operaciones_activas.append(nueva_op)
            registrar_apertura_db(nueva_op)
            msg = (f"🚀 *SEÑAL CRT DETECTADA ({nueva_op['simbolo']})*\n"
                   f"📊 Rango Ref (5 AM): {self.rango_low:.5f} - {self.rango_high:.5f}\n"
                   f"🧠 Bias Confirmado en 1H: {bias_detectado}\n"
                   f"💰 Entrada: {precio_actual:.5f}\n🎯 TP: {nueva_op['tp']:.5f}\n🛑 SL: {nueva_op['sl']:.5f}")
            enviar_telegram(msg)

def realizar_seguimiento():
    global operaciones_activas
    for op in operaciones_activas[:]:
        try:
            ticker = yf.Ticker(op['simbolo'])
            p_actual = ticker.fast_info['last_price']
            cerro, res = False, ""
            if op['tipo'] == 'LONG':
                if p_actual >= op['tp']: cerro, res = True, "GANANCIA"
                elif p_actual <= op['sl']: cerro, res = True, "PERDIDA"
            else:
                if p_actual <= op['tp']: cerro, res = True, "GANANCIA"
                elif p_actual >= op['sl']: cerro, res = True, "PERDIDA"

            if cerro:
                registrar_cierre_db(op['simbolo'], p_actual, res)
                enviar_telegram(f"🏁 *CERRADA ({op['simbolo']})*\n{'✅' if res=='GANANCIA' else '❌'} {res}\n💵 Salida: {p_actual:.5f}")
                operaciones_activas.remove(op)
        except: pass

def ejecutar_bot():
    global notificado_fin_sesion
    inicializar_db()
    activos = ['EURUSD=X', 'BTC-USD', 'SOL-USD']
    tz_ve = pytz.timezone('America/Caracas')
    enviar_telegram("🤖 *Bot CRT v6.8 Activo*\nAnalizando manipulación en 1H tras rango de 5 AM NY.")

    while True:
        ahora_ve = datetime.now(tz_ve)
        realizar_seguimiento()
        # Escaneamos desde las 6 AM hasta las 4 PM para aprovechar la sesión de NY
        if 6 <= ahora_ve.hour < 16:
            notificado_fin_sesion = False
            for activo in activos:
                bot = EstrategiaCRT(activo)
                if bot.descargar_y_analizar(): 
                    bot.chequear_entrada()
            time.sleep(300) 
        else:
            if not operaciones_activas and not notificado_fin_sesion:
                enviar_telegram("💤 *Sesión Finalizada.* Hibernando hasta mañana.")
                notificado_fin_sesion = True
            time.sleep(60)

if __name__ == "__main__":
    ejecutar_bot()