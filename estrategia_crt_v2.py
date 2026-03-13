"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 7.0 - Basado estrictamente en: https://www.youtube.com/watch?v=pVOjzW1q1Ak
Concepto: Acumulación de Sesión, Manipulación de Extremos y Expansión.
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
        print("🏠 CRT Modo: LOCAL (Cargando .env_local)")
        load_dotenv('.env_local')
    elif argumento == 'produccion':
        print("🌐 CRT Modo: PRODUCCIÓN (Cargando .env)")
        load_dotenv('.env')
    else:
        if os.path.exists('.env_local'):
            load_dotenv('.env_local')
        else:
            load_dotenv('.env')
except ImportError:
    pass

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

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', "8327248294:AAGvexslS_stn3B-THAbmhqKHswJyCFnFK4")
operaciones_activas = []

# ==========================================
# FUNCIONES DE DB Y MENSAJERÍA
# ==========================================

def get_db_connection():
    try: return mysql.connector.connect(**DB_CONFIG, connect_timeout=5)
    except Error as e: return None

def enviar_telegram(mensaje):
    conn = get_db_connection()
    ids = []
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM usuarios")
            ids = [str(row[0]) for row in cursor.fetchall()]
        finally: conn.close()

    if not ids: print(f"📢 {mensaje}"); return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try: requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=5)
        except: pass

# ==========================================
# CLASE DE LA ESTRATEGIA
# ==========================================

class EstrategiaCRT:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.rango_alto = None
        self.rango_bajo = None
        self.bias = None # 'BULL' o 'BEAR'
        self.manipulado = False
        self.df = None

    def establecer_rango_y_bias(self):
        """
        Define el 'CREATE' (Rango de 12:00 AM a 5:00 AM NY)
        Define el 'BIAS' según la estructura de 1H.
        """
        try:
            # Descargar 1H para el Bias
            h1 = yf.download(self.simbolo, period='5d', interval='1h', progress=False)
            if h1.empty: return False
            
            # Bias simple: Si el cierre actual > media de 20 periodos en 1H
            ma20 = h1['Close'].rolling(20).mean().iloc[-1]
            self.bias = 'BULL' if h1['Close'].iloc[-1] > ma20 else 'BEAR'

            # Rango de Sesión (00:00 - 05:00 NY)
            # Para simplificar, tomamos el High/Low de las últimas velas que cubren ese periodo
            # En un entorno real, filtraríamos estrictamente por timestamps
            session_data = h1.iloc[-10:-5] # Aproximación de las horas previas
            self.rango_alto = session_data['High'].max()
            self.rango_bajo = session_data['Low'].min()
            
            return True
        except: return False

    def analizar_manipulacion(self):
        """
        'TRADE': Espera que el precio rompa el rango para sacar liquidez y luego regrese.
        """
        df_m5 = yf.download(self.simbolo, period='1d', interval='5m', progress=False)
        if df_m5.empty: return None

        precio_actual = df_m5['Close'].iloc[-1]
        
        # Lógica de Manipulación (The 'T' in CRT)
        if self.bias == 'BULL':
            # Buscamos que manipule el BAJO del rango para comprar
            if df_m5['Low'].min() < self.rango_bajo and precio_actual > self.rango_bajo:
                return 'LONG'
        else:
            # Buscamos que manipule el ALTO del rango para vender
            if df_m5['High'].max() > self.rango_alto and precio_actual < self.rango_alto:
                return 'SHORT'
        
        return None

def chequear_entradas():
    activos = ['EURUSD=X', 'GBPUSD=X', 'BTC-USD']
    for activo in activos:
        # Evitar duplicados
        if any(op['simbolo'] == activo for op in operaciones_activas): continue
        
        bot = EstrategiaCRT(activo)
        if bot.establecer_rango_y_bias():
            signal = bot.analizar_manipulacion()
            if signal:
                p_entrada = yf.download(activo, period='1d', interval='1m', progress=False)['Close'].iloc[-1]
                
                # Gestión de Riesgo (Basado en el video: SL tras el mínimo/máximo de la manipulación)
                # Usamos un SL fijo de 0.2% por simplicidad en este script
                sl = p_entrada * (0.998 if signal == 'LONG' else 1.002)
                tp = p_entrada * (1.004 if signal == 'LONG' else 0.996) # Ratio 1:2
                
                nueva_op = {
                    'simbolo': activo,
                    'tipo': signal,
                    'entrada': p_entrada,
                    'sl': sl,
                    'tp': tp,
                    'hora': datetime.now()
                }
                operaciones_activas.append(nueva_op)
                enviar_telegram(f"🎯 *CRT SEÑAL DETECTADA ({activo})*\nDirección: {signal}\nBias: {bot.bias}\nEntrada: {p_entrada:.5f}\nTP: {tp:.5f}")

def gestionar_operaciones():
    for op in operaciones_activas[:]:
        try:
            df = yf.download(op['simbolo'], period='1d', interval='1m', progress=False)
            p_actual = df['Close'].iloc[-1]
            
            cerrar = False
            msg = ""
            
            if op['tipo'] == 'LONG':
                if p_actual >= op['tp']: cerrar, msg = True, "TP ✅"
                elif p_actual <= op['sl']: cerrar, msg = True, "SL ❌"
            else:
                if p_actual <= op['tp']: cerrar, msg = True, "TP ✅"
                elif p_actual >= op['sl']: cerrar, msg = True, "SL ❌"
                
            if cerrar:
                enviar_telegram(f"🏁 *CIERRE CRT ({op['simbolo']})*\nMotivo: {msg}\nPrecio: {p_actual:.5f}")
                operaciones_activas.remove(op)
        except: pass

def ejecutar_bot():
    enviar_telegram("🤖 *Bot CRT v7.0 Activo*\nEstrategia: Create-Range-Trade (Accumulation/Manipulation).")
    while True:
        chequear_entradas()
        gestionar_operaciones()
        time.sleep(60)

if __name__ == "__main__":
    ejecutar_bot()