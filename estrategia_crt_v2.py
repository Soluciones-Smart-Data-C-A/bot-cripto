"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 6.9 - Soporte para Argumentos de Terminal (Local/Producción)
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
        print("🏠 CRT Modo: LOCAL (Cargando .env_local)")
        load_dotenv('.env_local')
    elif argumento == 'produccion':
        print("🌐 CRT Modo: PRODUCCIÓN (Cargando .env)")
        load_dotenv('.env')
    else:
        if os.path.exists('.env_local'):
            print("🤖 CRT Modo Auto: Local detectado (.env_local)")
            load_dotenv('.env_local')
        else:
            print("🤖 CRT Modo Auto: Producción detectado (.env)")
            load_dotenv('.env')
except ImportError:
    print("⚠️ Librería python-dotenv no instalada. Usando variables de sistema.")

warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURACIÓN (Viene de archivos .env)
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
        print(f"❌ Error DB CRT: {e}")
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
        print(f"❌ Error inicializando tablas CRT: {e}")
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
    if not ids or not TELEGRAM_TOKEN: 
        print(f"📢 [CRT MSG]: {mensaje}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try: 
            requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=10)
        except: 
            pass

operaciones_activas = []
notificado_fin_sesion = False

class EstrategiaCRT:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.tz_ny = pytz.timezone('America/New_York')
        self.datos_1h = None
        self.rango_high = None
        self.rango_low = None

    def descargar_y_analizar(self):
        try:
            df = yf.download(self.simbolo, period='5d', interval='1h', progress=False, auto_adjust=True)
            if df.empty: return False
            if isinstance(df.columns, pd.MultiIndex):
                df = df.xs(self.simbolo, axis=1, level=1, drop_level=True).copy()
            df.index = df.index.tz_convert(self.tz_ny)
            self.datos_1h = df
            df_12h = df.resample('12h', offset='5h').agg({'High':'max', 'Low':'min', 'Close':'last'}).dropna()
            if len(df_12h) < 1: return False
            v_referencia = df_12h.iloc[-1]
            self.rango_high = float(v_referencia['High'])
            self.rango_low = float(v_referencia['Low'])
            return True
        except: return False

    def chequear_entrada(self):
        if any(op['simbolo'] == self.simbolo for op in operaciones_activas): return
        v_actual_1h = self.datos_1h.iloc[-1]
        precio_actual = float(v_actual_1h['Close'])
        nueva_op = None

        if v_actual_1h['Low'] < self.rango_low and v_actual_1h['Close'] > self.rango_low:
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'LONG', 'bias_12h': 'COMPRA',
                'r_high': self.rango_high, 'r_low': self.rango_low,
                'entrada': precio_actual, 'tp': self.rango_high, 'sl': float(v_actual_1h['Low'])
            }
        elif v_actual_1h['High'] > self.rango_high and v_actual_1h['Close'] < self.rango_high:
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'SHORT', 'bias_12h': 'VENTA',
                'r_high': self.rango_high, 'r_low': self.rango_low,
                'entrada': precio_actual, 'tp': self.rango_low, 'sl': float(v_actual_1h['High'])
            }

        if nueva_op:
            operaciones_activas.append(nueva_op)
            registrar_apertura_db(nueva_op)
            enviar_telegram(f"🚀 *SEÑAL CRT ({nueva_op['simbolo']})*\nEntrada: {precio_actual:.5f}\nTP: {nueva_op['tp']:.5f}")

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
                enviar_telegram(f"🏁 *CERRADA ({op['simbolo']})*\n{'✅' if res=='GANANCIA' else '❌'} {res}")
                operaciones_activas.remove(op)
        except: pass

def ejecutar_bot():
    global notificado_fin_sesion
    inicializar_db()
    activos = ['EURUSD=X', 'BTC-USD', 'SOL-USD']
    tz_ve = pytz.timezone('America/Caracas')
    enviar_telegram(f"🤖 *Bot CRT v6.9 Activo*\nAmbiente: {os.getenv('APP_ENV', 'producción')}")

    while True:
        ahora_ve = datetime.now(tz_ve)
        realizar_seguimiento()
        if 6 <= ahora_ve.hour < 16:
            notificado_fin_sesion = False
            for activo in activos:
                bot = EstrategiaCRT(activo)
                if bot.descargar_y_analizar(): bot.chequear_entrada()
            time.sleep(300) 
        else:
            if not operaciones_activas and not notificado_fin_sesion:
                enviar_telegram("💤 *Sesión CRT Finalizada.*")
                notificado_fin_sesion = True
            time.sleep(60)

if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN no definido en el entorno.")
    else:
        ejecutar_bot()