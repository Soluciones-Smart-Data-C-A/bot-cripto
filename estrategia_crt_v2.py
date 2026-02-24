"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 6.0 - Persistencia en MySQL (Compatible con Coolify/Docker)
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
        # Tabla de Usuarios/Suscriptores
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id VARCHAR(50) PRIMARY KEY,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                username VARCHAR(100),
                fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tabla de Historial de Trades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_trades (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha_apertura DATETIME,
                simbolo VARCHAR(20),
                tipo VARCHAR(10),
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
    """Obtiene lista de IDs desde MySQL."""
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
    """Guarda un nuevo suscriptor en MySQL."""
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
    """Registra la apertura de un trade en MySQL."""
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        query = """
            INSERT INTO historial_trades (fecha_apertura, simbolo, tipo, entrada, tp, sl)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            datetime.now(), op['simbolo'], op['tipo'], 
            op['entrada'], op['tp'], op['sl']
        ))
        conn.commit()
    finally:
        conn.close()

def registrar_cierre_db(simbolo, salida, resultado):
    """Actualiza el cierre del último trade abierto de un símbolo."""
    conn = get_db_connection()
    if not conn: return
    try:
        cursor = conn.cursor()
        # Obtener datos de la entrada para calcular pips
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
    if not ids:
        print(f"📢 [Consola]: {mensaje}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        payload = {"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"❌ Error enviando a {chat_id}: {e}")

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
                        msg_data = update["message"]
                        user_data = msg_data.get("from", {})
                        chat_id = msg_data["chat"]["id"]
                        texto = msg_data.get("text", "")
                        if texto.startswith("/start"):
                            if guardar_suscriptor(chat_id, user_data.get("first_name", ""), user_data.get("last_name", ""), user_data.get("username", "")):
                                bienvenida = "✅ *¡Suscripción Exitosa!*\nBienvenido a la Estrategia CRT."
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                              json={"chat_id": chat_id, "text": bienvenida, "parse_mode": "Markdown"})
        except:
            time.sleep(5)
        time.sleep(1)

# ==========================================
# LÓGICA DE TRADING
# ==========================================
operaciones_activas = [] 
notificado_fin_sesion = False

class EstrategiaCRT:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.tz_ny = pytz.timezone('America/New_York')
        self.bias_12h = None
        self.objetivo_12h = None
        self.datos_1h = None

    def descargar_y_analizar(self):
        try:
            df = yf.download(self.simbolo, period='10d', interval='1h', progress=False, auto_adjust=True)
            if df.empty: return False
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            df.index = df.index.tz_convert(self.tz_ny)
            self.datos_1h = df
            df_12h = df.resample('12h', offset='5h').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'}).dropna()
            if len(df_12h) < 2: return False
            v1, v2 = df_12h.iloc[-2], df_12h.iloc[-1]
            if v2['Low'] < v1['Low'] and v2['Close'] > v1['Low']:
                self.bias_12h, self.objetivo_12h = 'COMPRA', float(v1['High'])
            elif v2['High'] > v1['High'] and v2['Close'] < v1['High']:
                self.bias_12h, self.objetivo_12h = 'VENTA', float(v1['Low'])
            return True
        except Exception as e:
            print(f"Error {self.simbolo}: {e}")
            return False

    def chequear_entrada(self):
        if not self.bias_12h or any(op['simbolo'] == self.simbolo for op in operaciones_activas): return
        v1_1h, v2_1h = self.datos_1h.iloc[-2], self.datos_1h.iloc[-1]
        precio = float(v2_1h['Close'])
        ahora_str = datetime.now().strftime("%H:%M:%S")
        
        nueva_op = None
        if self.bias_12h == 'COMPRA' and v2_1h['Low'] < v1_1h['Low'] and v2_1h['Close'] > v1_1h['Low']:
            nueva_op = {'simbolo': self.simbolo, 'tipo': 'LONG', 'entrada': precio, 'tp': self.objetivo_12h, 'sl': float(v2_1h['Low'])}
        elif self.bias_12h == 'VENTA' and v2_1h['High'] > v1_1h['High'] and v2_1h['Close'] < v1_1h['High']:
            nueva_op = {'simbolo': self.simbolo, 'tipo': 'SHORT', 'entrada': precio, 'tp': self.objetivo_12h, 'sl': float(v2_1h['High'])}
        
        if nueva_op:
            operaciones_activas.append(nueva_op)
            registrar_apertura_db(nueva_op) # MySQL
            msg = (f"🚀 *SEÑAL {nueva_op['tipo']} ({nueva_op['simbolo']})*\n"
                   f"💰 Entrada: {precio:.5f}\n🎯 TP: {nueva_op['tp']:.5f}\n🛑 SL: {nueva_op['sl']:.5f}\n⏰ Hora: {ahora_str}")
            enviar_telegram(msg)

def realizar_seguimiento():
    global operaciones_activas
    for op in operaciones_activas[:]:
        try:
            ticker = yf.Ticker(op['simbolo'])
            precio_actual = ticker.fast_info['last_price']
            ahora_str = datetime.now().strftime("%H:%M:%S")
            cerro, resultado = False, ""
            if op['tipo'] == 'LONG':
                if precio_actual >= op['tp']: cerro, resultado = True, "GANANCIA"
                elif precio_actual <= op['sl']: cerro, resultado = True, "PERDIDA"
            elif op['tipo'] == 'SHORT':
                if precio_actual <= op['tp']: cerro, resultado = True, "GANANCIA"
                elif precio_actual >= op['sl']: cerro, resultado = True, "PERDIDA"

            if cerro:
                registrar_cierre_db(op['simbolo'], precio_actual, resultado) # MySQL
                emoji = "✅" if resultado == "GANANCIA" else "❌"
                enviar_telegram(f"🏁 *OPERACIÓN CERRADA*\n{emoji} {resultado}\n📈 Par: {op['simbolo']}\n💵 Salida: {precio_actual:.5f}\n⏰ Hora: {ahora_str}")
                operaciones_activas.remove(op)
        except Exception as e:
            print(f"Error seguimiento: {e}")

def ejecutar_bot():
    global notificado_fin_sesion
    inicializar_db()
    activos = ['EURUSD=X', 'GBPUSD=X', 'BTC-USD', 'AUDUSD=X']
    tz_ve = pytz.timezone('America/Caracas')
    threading.Thread(target=escuchador_mensajes, daemon=True).start()
    enviar_telegram("🤖 *Bot CRT v6.1 Activo*\nHorario: 5:00 AM - 5:00 PM VET.")

    while True:
        ahora_ve = datetime.now(tz_ve)
        realizar_seguimiento()
        
        # Horario extendido: de 5 AM (inclusive) a 5 PM (17:00 no inclusive)
        if 5 <= ahora_ve.hour < 17:
            notificado_fin_sesion = False
            for activo in activos:
                bot = EstrategiaCRT(activo)
                if bot.descargar_y_analizar(): bot.chequear_entrada()
            time.sleep(300) 
        else:
            # Si hay operaciones abiertas fuera de horario, seguimos haciéndoles seguimiento
            if operaciones_activas:
                time.sleep(60)
            else:
                if not notificado_fin_sesion:
                    enviar_telegram(f"💤 *Hibernando* hasta mañana a las 5:00 AM.")
                    notificado_fin_sesion = True
                time.sleep(60)

if __name__ == "__main__":
    ejecutar_bot()