"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 6.7 - Almacenamiento de Rango 12H (High/Low) en DB.
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
BINANCE_USDT_ADDRESS = "0xb49a1a0447e6e90018611342156232d26509528a"

def get_db_connection():
    """Establece conexión con MySQL."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"❌ Error conectando a MySQL: {e}")
        return None

def inicializar_db():
    """Crea las tablas necesarias si no existen."""
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        # Tabla de Usuarios
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id VARCHAR(50) PRIMARY KEY,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                username VARCHAR(100),
                fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tabla de Historial (Incluye Rango de 12H)
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

def guardar_suscriptor(chat_id, first_name="", last_name="", username=""):
    conn = get_db_connection()
    if not conn: return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT IGNORE INTO usuarios (chat_id, first_name, last_name, username)
            VALUES (%s, %s, %s, %s)
        """, (str(chat_id), first_name, last_name, username))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()

def registrar_apertura_db(op):
    """Registra apertura guardando el rango de 12H analizado."""
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

def escuchador_mensajes():
    offset = -1
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            response = requests.get(url, timeout=35).json()
            if "result" in response:
                for update in response["result"]:
                    offset = update["update_id"] + 1
                    if "message" in update:
                        msg = update["message"]
                        if msg.get("text", "").startswith("/start"):
                            guardar_suscriptor(msg["chat"]["id"], msg["from"].get("first_name"), msg["from"].get("last_name"), msg["from"].get("username"))
        except: time.sleep(5)

# ==========================================
# LÓGICA DE TRADING
# ==========================================
operaciones_activas = []
notificado_fin_sesion = False
bias_memoria = {} 

class EstrategiaCRT:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.tz_ny = pytz.timezone('America/New_York')
        # Recuperamos datos de memoria si existen
        mem = bias_memoria.get(simbolo, {})
        self.bias_12h = mem.get('bias')
        self.objetivo_12h = mem.get('tp')
        self.rango_high = mem.get('r_high')
        self.rango_low = mem.get('r_low')
        self.datos_1h = None

    def descargar_y_analizar(self):
        try:
            df = yf.download(self.simbolo, period='10d', interval='1h', progress=False, auto_adjust=True)
            if df.empty: return False
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            df.index = df.index.tz_convert(self.tz_ny)
            self.datos_1h = df
            
            # Vela de 12H (Offset 5h para alinear con 5 AM NY)
            df_12h = df.resample('12h', offset='5h').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'}).dropna()
            if len(df_12h) < 2: return False
            
            v1, v2 = df_12h.iloc[-2], df_12h.iloc[-1]
            
            # Guardamos los límites del rango de 12H (Paso 1: Create)
            self.rango_high = float(v1['High'])
            self.rango_low = float(v1['Low'])
            
            nuevo_bias = None
            nuevo_tp = None
            
            # Paso 2: Range (Manipulación)
            if v2['Low'] < self.rango_low and v2['Close'] > self.rango_low:
                nuevo_bias, nuevo_tp = 'COMPRA', self.rango_high
            elif v2['High'] > self.rango_high and v2['Close'] < self.rango_high:
                nuevo_bias, nuevo_tp = 'VENTA', self.rango_low
            
            if nuevo_bias:
                bias_memoria[self.simbolo] = {
                    'bias': nuevo_bias, 'tp': nuevo_tp, 
                    'r_high': self.rango_high, 'r_low': self.rango_low
                }
                self.bias_12h = nuevo_bias
                self.objetivo_12h = nuevo_tp
                
            return True
        except Exception as e:
            print(f"Error {self.simbolo}: {e}")
            return False

    def chequear_entrada(self):
        if not self.bias_12h or any(op['simbolo'] == self.simbolo for op in operaciones_activas): return
        
        v1_1h, v2_1h = self.datos_1h.iloc[-2], self.datos_1h.iloc[-1]
        precio = float(v2_1h['Close'])
        
        nueva_op = None
        # Paso 3: Trade (Confirmación en 1H)
        if self.bias_12h == 'COMPRA' and v2_1h['Low'] < v1_1h['Low'] and v2_1h['Close'] > v1_1h['Low']:
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'LONG', 'bias_12h': self.bias_12h,
                'r_high': self.rango_high, 'r_low': self.rango_low,
                'entrada': precio, 'tp': self.objetivo_12h, 'sl': float(v2_1h['Low'])
            }
        elif self.bias_12h == 'VENTA' and v2_1h['High'] > v1_1h['High'] and v2_1h['Close'] < v1_1h['High']:
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'SHORT', 'bias_12h': self.bias_12h,
                'r_high': self.rango_high, 'r_low': self.rango_low,
                'entrada': precio, 'tp': self.objetivo_12h, 'sl': float(v2_1h['High'])
            }
        
        if nueva_op:
            operaciones_activas.append(nueva_op)
            registrar_apertura_db(nueva_op)
            msg = (f"🚀 *SEÑAL CRT ({nueva_op['simbolo']})*\n"
                   f"📊 Rango 12H: {self.rango_low:.5f} - {self.rango_high:.5f}\n"
                   f"🧠 Bias: {self.bias_12h}\n"
                   f"💰 Entrada: {precio:.5f}\n🎯 TP: {nueva_op['tp']:.5f}\n🛑 SL: {nueva_op['sl']:.5f}")
            enviar_telegram(msg)
            bias_memoria.pop(self.simbolo, None)

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
    activos = ['EURUSD=X', 'GBPUSD=X', 'BTC-USD', 'AUDUSD=X']
    tz_ve = pytz.timezone('America/Caracas')
    threading.Thread(target=escuchador_mensajes, daemon=True).start()
    enviar_telegram("🤖 *Bot CRT v6.7 Activo*\nRegistro de rangos 12H habilitado.")

    while True:
        ahora_ve = datetime.now(tz_ve)
        realizar_seguimiento()
        if 5 <= ahora_ve.hour < 17:
            notificado_fin_sesion = False
            for activo in activos:
                bot = EstrategiaCRT(activo)
                if bot.descargar_y_analizar(): bot.chequear_entrada()
            time.sleep(300) 
        else:
            if not operaciones_activas and not notificado_fin_sesion:
                enviar_telegram("💤 *Hibernando* hasta mañana.")
                notificado_fin_sesion = True
            time.sleep(60)

if __name__ == "__main__":
    ejecutar_bot()