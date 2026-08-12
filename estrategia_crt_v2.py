"""
Script de Trading Automático - Estrategia CRT (Create-Range-Trade)
Versión 7.1 - Basado estrictamente en: https://www.youtube.com/watch?v=pVOjzW1q1Ak
Concepto: Acumulación de Sesión, Manipulación de Extremos y Expansión.
Persistencia unificada en historial_operaciones vía common.
"""

import sys
import time
import traceback
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
        try:
            h1 = descargar(self.simbolo, '5d', '1h')
            common.dlog(f"  {self.simbolo}: {len(h1)} velas 1h descargadas")
            if h1.empty:
                print(f"⚠️ {self.simbolo}: 0 velas de yfinance (1h)")
                return False

            ma20 = float(h1['Close'].rolling(20).mean().iloc[-1])
            self.bias = 'BULL' if float(h1['Close'].iloc[-1]) > ma20 else 'BEAR'

            session_data = h1.iloc[-10:-5]
            self.rango_alto = float(session_data['High'].max())
            self.rango_bajo = float(session_data['Low'].min())

            common.dlog(f"  {self.simbolo}: bias={self.bias} | rango={self.rango_bajo:.2f}-{self.rango_alto:.2f}")
            return True
        except Exception as e:
            print(f"⚠️ Error CRT estableciendo rango {self.simbolo}: {e}")
            traceback.print_exc()
            return False

    def analizar_manipulacion(self):
        df_m5 = descargar(self.simbolo, '1d', '5m')
        common.dlog(f"  {self.simbolo}: {len(df_m5)} velas 5m descargadas")
        if df_m5.empty:
            print(f"⚠️ {self.simbolo}: 0 velas de yfinance (5m)")
            return None

        precio_actual = float(df_m5['Close'].iloc[-1])
        low_min = float(df_m5['Low'].min())
        high_max = float(df_m5['High'].max())

        common.dlog(f"  {self.simbolo}: precio={precio_actual:.5f} | low_min={low_min:.2f} | high_max={high_max:.2f}")

        if self.bias == 'BULL':
            manipula_bajo = low_min < self.rango_bajo and precio_actual > self.rango_bajo
            common.dlog(f"  {self.simbolo}: manipula_bajo={manipula_bajo} (low_min={low_min:.2f} < rango_bajo={self.rango_bajo:.2f})")
            if manipula_bajo:
                return 'LONG'
        else:
            manipula_alto = high_max > self.rango_alto and precio_actual < self.rango_alto
            common.dlog(f"  {self.simbolo}: manipula_alto={manipula_alto} (high_max={high_max:.2f} > rango_alto={self.rango_alto:.2f})")
            if manipula_alto:
                return 'SHORT'

        return None

# ==========================================
# LÓGICA DE ENTRADAS Y GESTIÓN
# ==========================================
def chequear_entradas():
    for activo in common.ACTIVOS:
        if common.es_accion_o_etf(activo) and not common.horario_mercado():
            common.dlog(f"  {activo}: mercado cerrado, saltando")
            continue
        if any(op['simbolo'] == activo for op in operaciones_activas):
            common.dlog(f"  {activo}: trade activo en memoria, saltando")
            continue

        trades_abiertos = common.obtener_trades_abiertos(ESTRATEGIA, activo)
        if trades_abiertos:
            common.dlog(f"  {activo}: trade abierto #{trades_abiertos[0]['id']} en BD, saltando")
            continue

        bot = EstrategiaCRT(activo)
        if bot.establecer_rango_y_bias():
            bias_actual[activo] = bot.bias
            signal = bot.analizar_manipulacion()
            common.dlog(f"  {activo}: signal={signal}")
            if signal:
                p_entrada = float(descargar(activo, '1d', '1m')['Close'].iloc[-1])

                sl = p_entrada * (0.998 if signal == 'LONG' else 1.002)
                tp = p_entrada * (1.006 if signal == 'LONG' else 0.994)

                common.dlog(f"  {activo}: SEÑAL {signal} | entrada={p_entrada:.5f} sl={sl:.5f} tp={tp:.5f}")

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
                    f"🎯 *SEÑAL CRT_V7 ({activo})*\n"
                    f"Dirección: {signal}\n"
                    f"Entrada: {p_entrada:.5f}\nTP: {tp:.5f}\nSL: {sl:.5f}\n"
                    f"ID: {nueva_op['id']}",
                    posicion={'entrada': p_entrada, 'sl': sl, 'tp': tp})

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
                if common.registrar_cierre(op['id'], p_actual, msg):
                    common.enviar_telegram(ESTRATEGIA, op['simbolo'],
                        f"🏁 *CIERRE CRT ({op['simbolo']})*\nMotivo: {msg}\nPrecio: {p_actual:.5f}\n"
                        f"ID: {op['id']}")
                operaciones_activas.remove(op)
        except Exception as e:
            print(f"⚠️ Error CRT gestionando {op['simbolo']}: {e}")
            traceback.print_exc()

def ejecutar_bot():
    common.inicializar_db()
    print(f"🚀 {ESTRATEGIA} iniciado | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | ACTIVOS: {len(common.ACTIVOS)} | mercado_abierto: {common.horario_mercado()}")

    cerrados = common.cerrar_trades_trabados(ESTRATEGIA, max_horas=24)
    if cerrados:
        print(f"🔄 {len(cerrados)} trade(s) cerrados automáticamente al iniciar")

    trades = common.obtener_trades_abiertos(ESTRATEGIA)
    for t in trades:
        operaciones_activas.append({
            'simbolo': t['simbolo'], 'tipo': t['tipo'], 'entrada': t['entrada'],
            'sl': t['sl'], 'tp': t['tp'], 'id': t['id']
        })
    if trades:
        print(f"🔄 {len(trades)} trade(s) abierto(s) recuperado(s) de BD")
        common.enviar_telegram(ESTRATEGIA, None,
            f"🔄 *TRADES REANUDADOS (CRT)*\n{len(trades)} operación(es) recuperada(s)")

    common.enviar_telegram(ESTRATEGIA, None,
        "🤖 *Bot CRT v7.1 Activo*\nEstrategia: Create-Range-Trade (Accumulation/Manipulation).")

    mercado_anterior = common.horario_mercado()
    while True:
        mercado_actual = common.horario_mercado()
        if mercado_actual != mercado_anterior:
            if mercado_actual:
                print("🔔 Mercado ABIERTO — escaneando acciones/ETF")
            else:
                print("⏰ Mercado CERRADO — pausando acciones/ETF")
            mercado_anterior = mercado_actual

        chequear_entradas()
        gestionar_operaciones()

        estado_bias = ' | '.join(f"{a}: {b}" for a, b in bias_actual.items())
        print(f"💓 Heartbeat CRT {datetime.now().strftime('%H:%M:%S')} [Bias: {estado_bias}] [Operaciones activas: {len(operaciones_activas)}]")

        time.sleep(60)

if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
