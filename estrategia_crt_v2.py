"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 7.1 - Basado estrictamente en: https://www.youtube.com/watch?v=pVOjzW1q1Ak
Concepto: Acumulación de Sesión, Manipulación de Extremos y Expansión.
Persistencia unificada en historial_operaciones vía common.
"""

import sys
import time
import warnings
from datetime import datetime

import pandas as pd
import yfinance as yf

import common

warnings.filterwarnings('ignore')

ESTRATEGIA = 'CRT_V7'
operaciones_activas = []
bias_actual = {}

def descargar(simbolo, periodo, intervalo):
    df = yf.download(simbolo, period=periodo, interval=intervalo, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

# ==========================================
# CLASE DE LA ESTRATEGIA
# ==========================================
class EstrategiaCRT:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.rango_alto = None
        self.rango_bajo = None
        self.bias = None  # 'BULL' o 'BEAR'

    def establecer_rango_y_bias(self):
        """
        Define el 'CREATE' (Rango de 12:00 AM a 5:00 AM NY)
        Define el 'BIAS' según la estructura de 1H.
        """
        try:
            # Descargar 1H para el Bias
            h1 = descargar(self.simbolo, '5d', '1h')
            if h1.empty:
                return False

            # Bias simple: Si el cierre actual > media de 20 periodos en 1H
            ma20 = float(h1['Close'].rolling(20).mean().iloc[-1])
            self.bias = 'BULL' if float(h1['Close'].iloc[-1]) > ma20 else 'BEAR'

            # Rango de Sesión (00:00 - 05:00 NY)
            # Aproximación: High/Low de las últimas velas previas
            session_data = h1.iloc[-10:-5]
            self.rango_alto = float(session_data['High'].max())
            self.rango_bajo = float(session_data['Low'].min())

            return True
        except Exception as e:
            print(f"⚠️ Error CRT estableciendo rango {self.simbolo}: {e}")
            return False

    def analizar_manipulacion(self):
        """
        'TRADE': Espera que el precio rompa el rango para sacar liquidez y luego regrese.
        """
        df_m5 = descargar(self.simbolo, '1d', '5m')
        if df_m5.empty:
            return None

        precio_actual = float(df_m5['Close'].iloc[-1])

        if self.bias == 'BULL':
            # Manipula el BAJO del rango para comprar
            if df_m5['Low'].min() < self.rango_bajo and precio_actual > self.rango_bajo:
                return 'LONG'
        else:
            # Manipula el ALTO del rango para vender
            if df_m5['High'].max() > self.rango_alto and precio_actual < self.rango_alto:
                return 'SHORT'

        return None

# ==========================================
# LÓGICA DE ENTRADAS Y GESTIÓN
# ==========================================
def chequear_entradas():
    for activo in common.ACTIVOS:
        # Evitar duplicados en memoria
        if any(op['simbolo'] == activo for op in operaciones_activas):
            continue

        # Verificar si ya hay trade abierto en BD para esta estrategia y símbolo
        trades_abiertos = common.obtener_trades_abiertos(ESTRATEGIA, activo)
        if trades_abiertos:
            continue  # Ya hay trade abierto en BD, no evaluar

        bot = EstrategiaCRT(activo)
        if bot.establecer_rango_y_bias():
            bias_actual[activo] = bot.bias
            signal = bot.analizar_manipulacion()
            if signal:
                p_entrada = float(descargar(activo, '1d', '1m')['Close'].iloc[-1])

                # Gestión de Riesgo (SL fijo de 0.2%, TP de 0.4% -> ratio 1:2)
                sl = p_entrada * (0.998 if signal == 'LONG' else 1.002)
                tp = p_entrada * (1.004 if signal == 'LONG' else 0.996)

                nueva_op = {
                    'simbolo': activo,
                    'tipo': signal,
                    'entrada': p_entrada,
                    'sl': sl,
                    'tp': tp,
                    'hora': datetime.now()
                }
                nueva_op['id'] = common.registrar_apertura(ESTRATEGIA, activo, signal, p_entrada,
                                                           sl=sl, tp=tp,
                                                           rango_alto=bot.rango_alto, rango_bajo=bot.rango_bajo)
                operaciones_activas.append(nueva_op)
                common.enviar_telegram(ESTRATEGIA, activo,
                    f"🎯 *CRT SEÑAL DETECTADA ({activo})*\n"
                    f"Dirección: {signal}\nBias: {bot.bias}\n"
                    f"Entrada: {p_entrada:.5f}\nTP: {tp:.5f}\nSL: {sl:.5f}\n"
                    f"ID: {nueva_op['id']}")

def gestionar_operaciones():
    for op in operaciones_activas[:]:
        try:
            df = descargar(op['simbolo'], '1d', '1m')
            if df.empty:
                continue
            p_actual = float(df['Close'].iloc[-1])

            cerrar = False
            msg = ""

            if op['tipo'] == 'LONG':
                if p_actual >= op['tp']:
                    cerrar, msg = True, "TP ✅"
                elif p_actual <= op['sl']:
                    cerrar, msg = True, "SL ❌"
            else:
                if p_actual <= op['tp']:
                    cerrar, msg = True, "TP ✅"
                elif p_actual >= op['sl']:
                    cerrar, msg = True, "SL ❌"

            if cerrar:
                common.registrar_cierre(op['id'], p_actual, msg)
                common.enviar_telegram(ESTRATEGIA, op['simbolo'],
                    f"🏁 *CIERRE CRT ({op['simbolo']})*\nMotivo: {msg}\nPrecio: {p_actual:.5f}\n"
                    f"ID: {op['id']}")
                operaciones_activas.remove(op)
        except Exception as e:
            print(f"⚠️ Error CRT gestionando {op['simbolo']}: {e}")

def ejecutar_bot():
    common.inicializar_db()
    common.enviar_telegram(ESTRATEGIA, None,
        "🤖 *Bot CRT v7.1 Activo*\nEstrategia: Create-Range-Trade (Accumulation/Manipulation).")

    while True:
        chequear_entradas()
        gestionar_operaciones()

        estado_bias = ' | '.join(f"{a}: {b}" for a, b in bias_actual.items())
        print(f"💓 Heartbeat CRT {datetime.now().strftime('%H:%M:%S')} "
              f"[Bias: {estado_bias}] [Operaciones activas: {len(operaciones_activas)}]")

        time.sleep(60)

if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
