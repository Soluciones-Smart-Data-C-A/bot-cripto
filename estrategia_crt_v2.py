"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 5.3 - Registro de eficiencia y exportación a CSV para análisis.
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
import csv

# Configuración de avisos
warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURACIÓN DE ARCHIVOS Y NOTIFICACIONES
# ==========================================
TELEGRAM_TOKEN = "8327248294:AAGvexslS_stn3B-THAbmhqKHswJyCFnFK4"
BINANCE_USDT_ADDRESS = "0xb49a1a0447e6e90018611342156232d26509528a"
USUARIOS_FILE = "usuarios.txt"
HISTORIAL_FILE = "historial_trades.csv"

def inicializar_csv():
    """Crea el encabezado del historial si no existe."""
    if not os.path.exists(HISTORIAL_FILE):
        with open(HISTORIAL_FILE, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Fecha_Apertura', 'Simbolo', 'Tipo', 'Entrada', 'TP', 'SL', 'Fecha_Cierre', 'Salida', 'Resultado', 'Pips_Profit'])

def registrar_apertura_csv(op):
    """Guarda el inicio de una operacion."""
    try:
        with open(HISTORIAL_FILE, mode='a', newline='') as f:
            writer = csv.writer(f)
            # Dejamos campos de cierre vacios temporalmente
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                op['simbolo'], op['tipo'], op['entrada'], op['tp'], op['sl'],
                '', '', 'ABIERTA', ''
            ])
    except Exception as e:
        print(f"❌ Error guardando apertura en CSV: {e}")

def registrar_cierre_csv(simbolo, salida, resultado):
    """Actualiza la ultima operacion abierta del simbolo con su resultado."""
    try:
        df = pd.read_csv(HISTORIAL_FILE)
        # Buscar la ultima fila de ese simbolo que este 'ABIERTA'
        mask = (df['Simbolo'] == simbolo) & (df['Resultado'] == 'ABIERTA')
        if not df[mask].empty:
            idx = df[mask].index[-1]
            entrada = df.at[idx, 'Entrada']
            tipo = df.at[idx, 'Tipo']
            
            # Calcular Pips/Puntos de profit
            pips = (salida - entrada) if tipo == 'LONG' else (entrada - salida)
            
            df.at[idx, 'Fecha_Cierre'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            df.at[idx, 'Salida'] = salida
            df.at[idx, 'Resultado'] = resultado
            df.at[idx, 'Pips_Profit'] = round(pips, 5)
            
            df.to_csv(HISTORIAL_FILE, index=False)
    except Exception as e:
        print(f"❌ Error actualizando cierre en CSV: {e}")

def obtener_suscriptores():
    if not os.path.exists(USUARIOS_FILE):
        return []
    try:
        ids = []
        with open(USUARIOS_FILE, "r") as f:
            for line in f:
                parts = line.strip().split(",")
                if parts and parts[0]:
                    ids.append(parts[0])
        return ids
    except Exception as e:
        print(f"❌ Error leyendo {USUARIOS_FILE}: {e}")
        return []

def guardar_suscriptor(chat_id, first_name="", last_name="", username=""):
    suscriptores = obtener_suscriptores()
    chat_id_str = str(chat_id)
    if chat_id_str not in suscriptores:
        try:
            fn = str(first_name).replace(",", " ")
            ln = str(last_name).replace(",", " ")
            un = str(username).replace(",", " ")
            with open(USUARIOS_FILE, "a") as f:
                f.write(f"{chat_id_str},{fn},{ln},{un}\n")
            return True
        except Exception as e:
            print(f"❌ Error guardando suscriptor: {e}")
    return False

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
                                bienvenida = "✅ *Suscripción Exitosa!*"
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": bienvenida, "parse_mode": "Markdown"})
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
        nueva_op = None
        if self.bias_12h == 'COMPRA' and v2_1h['Low'] < v1_1h['Low'] and v2_1h['Close'] > v1_1h['Low']:
            nueva_op = {'simbolo': self.simbolo, 'tipo': 'LONG', 'entrada': precio, 'tp': self.objetivo_12h, 'sl': float(v2_1h['Low'])}
        elif self.bias_12h == 'VENTA' and v2_1h['High'] > v1_1h['High'] and v2_1h['Close'] < v1_1h['High']:
            nueva_op = {'simbolo': self.simbolo, 'tipo': 'SHORT', 'entrada': precio, 'tp': self.objetivo_12h, 'sl': float(v2_1h['High'])}
        
        if nueva_op:
            operaciones_activas.append(nueva_op)
            registrar_apertura_csv(nueva_op) # REGISTRO DE EFICIENCIA
            enviar_telegram(f"🚀 *SEÑAL {nueva_op['tipo']} ({nueva_op['simbolo']})*\n💰 Entrada: {precio:.5f}\n🎯 TP: {nueva_op['tp']:.5f}\n🛑 SL: {nueva_op['sl']:.5f}")

def realizar_seguimiento():
    global operaciones_activas
    for op in operaciones_activas[:]:
        try:
            ticker = yf.Ticker(op['simbolo'])
            precio_actual = ticker.fast_info['last_price']
            cerro, resultado = False, ""
            if op['tipo'] == 'LONG':
                if precio_actual >= op['tp']: cerro, resultado = True, "GANANCIA"
                elif precio_actual <= op['sl']: cerro, resultado = True, "PERDIDA"
            elif op['tipo'] == 'SHORT':
                if precio_actual <= op['tp']: cerro, resultado = True, "GANANCIA"
                elif precio_actual >= op['sl']: cerro, resultado = True, "PERDIDA"

            if cerro:
                registrar_cierre_csv(op['simbolo'], precio_actual, resultado) # ACTUALIZAR EFICIENCIA
                emoji = "✅" if resultado == "GANANCIA" else "❌"
                enviar_telegram(f"🏁 *OPERACIÓN CERRADA*\n{emoji} {resultado}\n📈 Par: {op['simbolo']}\n💵 Salida: {precio_actual:.5f}")
                operaciones_activas.remove(op)
        except Exception as e:
            print(f"Error seguimiento: {e}")

def ejecutar_bot():
    global notificado_fin_sesion
    inicializar_csv()
    activos = ['EURUSD=X', 'GBPUSD=X', 'BTC-USD', 'AUDUSD=X']
    tz_ve = pytz.timezone('America/Caracas')
    threading.Thread(target=escuchador_mensajes, daemon=True).start()
    enviar_telegram("🤖 *Bot CRT v5.3 Activo*\nRegistro de eficiencia habilitado.")

    while True:
        ahora_ve = datetime.now(tz_ve)
        realizar_seguimiento()
        if 5 <= ahora_ve.hour < 12:
            notificado_fin_sesion = False
            for activo in activos:
                bot = EstrategiaCRT(activo)
                if bot.descargar_y_analizar(): bot.chequear_entrada()
            time.sleep(300) 
        else:
            if not operaciones_activas and not notificado_fin_sesion:
                enviar_telegram(f"💤 *Hibernando* hasta las 5:00 AM.")
                notificado_fin_sesion = True
            time.sleep(60)

if __name__ == "__main__":
    ejecutar_bot()