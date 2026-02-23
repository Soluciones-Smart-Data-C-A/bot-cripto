"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 5.0 - Soporte Multiusuario (Base de datos de suscriptores)
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

# Configuración de avisos
warnings.filterwarnings('ignore')

# ==========================================
# CONFIGURACIÓN DE NOTIFICACIONES (TELEGRAM)
# ==========================================
TELEGRAM_TOKEN = "8327248294:AAGvexslS_stn3B-THAbmhqKHswJyCFnFK4"
BINANCE_USDT_ADDRESS = "0xb49a1a0447e6e90018611342156232d26509528a"
USUARIOS_FILE = "usuarios.txt"

def obtener_suscriptores():
    """Lee los IDs de chat del archivo local."""
    if not os.path.exists(USUARIOS_FILE):
        return []
    try:
        with open(USUARIOS_FILE, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        print(f"❌ Error leyendo {USUARIOS_FILE}: {e}")
        return []

def guardar_suscriptor(chat_id):
    """Guarda un nuevo ID si no existe."""
    suscriptores = obtener_suscriptores()
    chat_id_str = str(chat_id)
    if chat_id_str not in suscriptores:
        try:
            with open(USUARIOS_FILE, "a") as f:
                f.write(f"{chat_id_str}\n")
            print(f"✅ Nuevo suscriptor registrado: {chat_id_str}")
            return True
        except Exception as e:
            print(f"❌ Error guardando suscriptor: {e}")
    return False

def enviar_telegram(mensaje):
    """Envía una notificación a todos los usuarios registrados."""
    ids = obtener_suscriptores()
    if not ids:
        print(f"📢 [Consola]: No hay suscriptores. {mensaje}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    for chat_id in ids:
        payload = {
            "chat_id": chat_id, 
            "text": mensaje, 
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"❌ Error enviando a {chat_id}: {e}")

def escuchador_mensajes():
    """Hilo secundario para registrar nuevos usuarios que den /start."""
    # Primero limpiamos actualizaciones antiguas para evitar bucles con mensajes pasados
    offset = -1
    print("👂 Iniciando escuchador de mensajes...")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            response = requests.get(url, timeout=35).json()
            
            if "result" in response:
                for update in response["result"]:
                    # Actualizamos el offset para marcar el mensaje como leído
                    offset = update["update_id"] + 1
                    
                    if "message" in update and "text" in update["message"]:
                        chat_id = update["message"]["chat"]["id"]
                        texto = update["message"].get("text", "")
                        
                        if texto.startswith("/start"):
                            if guardar_suscriptor(chat_id):
                                bienvenida = (
                                    "✅ *¡Suscripción Exitosa!*\n\n"
                                    "Bienvenido a la Estrategia CRT. Recibirás señales de "
                                    "BTC, EURUSD, GBPUSD y AUDUSD automáticamente."
                                )
                                # Enviar mensaje de confirmación solo al nuevo usuario
                                try:
                                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                                  json={"chat_id": chat_id, "text": bienvenida, "parse_mode": "Markdown"}, 
                                                  timeout=10)
                                except:
                                    pass
        except Exception as e:
            # En caso de error de red, esperamos un poco antes de reintentar
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
            
            df_12h = df.resample('12h', offset='5h').agg({
                'Open':'first', 'High':'max', 'Low':'min', 'Close':'last'
            }).dropna()
            
            if len(df_12h) < 2: return False
            v1, v2 = df_12h.iloc[-2], df_12h.iloc[-1]
            
            if v2['Low'] < v1['Low'] and v2['Close'] > v1['Low']:
                self.bias_12h, self.objetivo_12h = 'COMPRA', float(v1['High'])
            elif v2['High'] > v1['High'] and v2['Close'] < v1['High']:
                self.bias_12h, self.objetivo_12h = 'VENTA', float(v1['Low'])
            else:
                self.bias_12h = None
            
            return True
        except Exception as e:
            print(f"Error {self.simbolo}: {e}")
            return False

    def chequear_entrada(self):
        if not self.bias_12h: 
            return
        
        if any(op['simbolo'] == self.simbolo for op in operaciones_activas):
            return

        v1_1h, v2_1h = self.datos_1h.iloc[-2], self.datos_1h.iloc[-1]
        precio = float(v2_1h['Close'])
        
        nueva_op = None
        if self.bias_12h == 'COMPRA' and v2_1h['Low'] < v1_1h['Low'] and v2_1h['Close'] > v1_1h['Low']:
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'LONG', 'entrada': precio,
                'tp': self.objetivo_12h, 'sl': float(v2_1h['Low']), 'estado': 'ABIERTA'
            }
        elif self.bias_12h == 'VENTA' and v2_1h['High'] > v1_1h['High'] and v2_1h['Close'] < v1_1h['High']:
            nueva_op = {
                'simbolo': self.simbolo, 'tipo': 'SHORT', 'entrada': precio,
                'tp': self.objetivo_12h, 'sl': float(v2_1h['Low']), 'estado': 'ABIERTA'
            }
        
        if nueva_op:
            operaciones_activas.append(nueva_op)
            msg = (f"🚀 *SEÑAL {nueva_op['tipo']} ({nueva_op['simbolo']})*\n"
                   f"💰 Entrada: {precio:.5f}\n🎯 TP: {nueva_op['tp']:.5f}\n🛑 SL: {nueva_op['sl']:.5f}\n\n"
                   f"🕒 _Ventana NY_")
            enviar_telegram(msg)

def realizar_seguimiento():
    global operaciones_activas
    if not operaciones_activas: return

    for op in operaciones_activas[:]:
        try:
            ticker = yf.Ticker(op['simbolo'])
            precio_actual = ticker.fast_info['last_price']
            cerro, resultado = False, ""

            if op['tipo'] == 'LONG':
                if precio_actual >= op['tp']: cerro, resultado = True, "✅ TP ALCANZADO"
                elif precio_actual <= op['sl']: cerro, resultado = True, "❌ STOP LOSS TOCADO"
            elif op['tipo'] == 'SHORT':
                if precio_actual <= op['tp']: cerro, resultado = True, "✅ TP ALCANZADO"
                elif precio_actual >= op['sl']: cerro, resultado = True, "❌ STOP LOSS TOCADO"

            if cerro:
                msg = (f"🏁 *OPERACIÓN CERRADA - {op['simbolo']}*\n{resultado}\n💵 Salida: {precio_actual:.5f}\n\n"
                       f"☕ *Apoyo:* `{BINANCE_USDT_ADDRESS}`")
                enviar_telegram(msg)
                operaciones_activas.remove(op)
                
        except Exception as e:
            print(f"Error en seguimiento: {e}")

def ejecutar_bot():
    global notificado_fin_sesion
    activos = ['EURUSD=X', 'GBPUSD=X', 'BTC-USD', 'AUDUSD=X']
    tz_ve = pytz.timezone('America/Caracas')
    
    # Iniciar el escuchador de nuevos usuarios en paralelo
    threading.Thread(target=escuchador_mensajes, daemon=True).start()

    print("🤖 Bot iniciado. Verificando suscripciones...")
    enviar_telegram("🤖 *Bot CRT v5.0 Activo*\nSistema multiusuario iniciado.")

    while True:
        ahora_ve = datetime.now(tz_ve)
        realizar_seguimiento()

        if 6 <= ahora_ve.hour < 12:
            notificado_fin_sesion = False
            print(f"🔎 {ahora_ve.strftime('%H:%M:%S')} - Analizando Mercado...")
            for activo in activos:
                bot = EstrategiaCRT(activo)
                if bot.descargar_y_analizar():
                    bot.chequear_entrada()
            time.sleep(300) 
        else:
            if operaciones_activas:
                time.sleep(300)
            else:
                if not notificado_fin_sesion:
                    msg_fin = f"💤 *Sesión Finalizada ({ahora_ve.strftime('%H:%M')})*\nEntrando en modo ahorro hasta mañana."
                    enviar_telegram(msg_fin)
                    notificado_fin_sesion = True
                
                print(f"💤 {ahora_ve.strftime('%H:%M:%S')} - Hibernación activa.")
                time.sleep(60) # Revisión cada minuto para ser más responsivo al /start

if __name__ == "__main__":
    ejecutar_bot()