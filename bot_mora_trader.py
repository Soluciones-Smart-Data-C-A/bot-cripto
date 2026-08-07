"""
Script de Trading Automático - Estrategia Mora Trader (Cruce de EMAs)
Basado en: https://www.youtube.com/shorts/roEy8Da2R1A
Versión 2.7 - Cruce rápido de EMA 9 y 21 con SL/TP ratio 1:3.
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

ESTRATEGIA = 'MORA_EMA_CROSS'
operaciones_activas = {}
MAX_HORAS_ABIERTO = 24  # Cerrar después de 24 horas

def calcular_sl_tp(tipo, precio):
    """SL/TP ratio 1:3 (0.2% de stop, 0.6% de target)."""
    if tipo == 'LONG':
        return round(precio * 0.998, 8), round(precio * 1.006, 8)
    return round(precio * 1.002, 8), round(precio * 0.994, 8)

def analizar_estrategia(simbolo):
    try:
        # Verificar si ya hay trade abierto en BD para esta estrategia y símbolo
        trades_abiertos = common.obtener_trades_abiertos(ESTRATEGIA, simbolo)
        if trades_abiertos:
            return  # Ya hay trade abierto, no evaluar

        # Descarga de datos para análisis de tendencia
        df = yf.download(simbolo, period='2d', interval='15m', progress=False, auto_adjust=True)
        if df.empty:
            return

        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

        # Indicadores de la estrategia
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()

        ult = df.iloc[-1]
        pen = df.iloc[-2]

        precio_actual = float(ult['Close'])
        e9_ult, e21_ult = float(ult['EMA9']), float(ult['EMA21'])
        e9_pen, e21_pen = float(pen['EMA9']), float(pen['EMA21'])

        # --- LÓGICA DE CRUCE ---

        # 1. Entrada en COMPRA
        if e9_pen <= e21_pen and e9_ult > e21_ult:
            if simbolo not in operaciones_activas:
                sl, tp = calcular_sl_tp('LONG', precio_actual)
                id_op = common.registrar_apertura(ESTRATEGIA, simbolo, 'LONG', precio_actual,
                                                  sl=sl, tp=tp, ema_9=e9_ult, ema_21=e21_ult)
                operaciones_activas[simbolo] = {
                    'tipo': 'LONG',
                    'entrada': precio_actual,
                    'id': id_op,
                    'fecha_apertura': datetime.now()
                }
                common.enviar_telegram(ESTRATEGIA, simbolo,
                    f"🚀 *MORA: CRUCE ALCISTA (9/21)*\n"
                    f"Par: {simbolo}\nPrecio: {precio_actual:.5f}\n"
                    f"SL: {sl:.5f}\nTP: {tp:.5f}\n"
                    f"ID: {id_op}")

        # 2. Entrada en VENTA
        elif e9_pen >= e21_pen and e9_ult < e21_ult:
            if simbolo not in operaciones_activas:
                sl, tp = calcular_sl_tp('SHORT', precio_actual)
                id_op = common.registrar_apertura(ESTRATEGIA, simbolo, 'SHORT', precio_actual,
                                                  sl=sl, tp=tp, ema_9=e9_ult, ema_21=e21_ult)
                operaciones_activas[simbolo] = {
                    'tipo': 'SHORT',
                    'entrada': precio_actual,
                    'id': id_op,
                    'fecha_apertura': datetime.now()
                }
                common.enviar_telegram(ESTRATEGIA, simbolo,
                    f"📉 *MORA: CRUCE BAJISTA (9/21)*\n"
                    f"Par: {simbolo}\nPrecio: {precio_actual:.5f}\n"
                    f"SL: {sl:.5f}\nTP: {tp:.5f}\n"
                    f"ID: {id_op}")

        # 3. Cierre de operaciones
        if simbolo in operaciones_activas:
            op = operaciones_activas[simbolo]
            cierre = False
            res = ""

            # 3.1 Verificar SL/TP
            if op['tipo'] == 'LONG':
                if precio_actual >= op.get('tp', float('inf')):
                    cierre, res = True, "TP ✅"
                elif precio_actual <= op.get('sl', 0):
                    cierre, res = True, "SL ❌"
            else:
                if precio_actual <= op.get('tp', 0):
                    cierre, res = True, "TP ✅"
                elif precio_actual >= op.get('sl', float('inf')):
                    cierre, res = True, "SL ❌"

            # 3.2 Verificar tiempo máximo
            if not cierre and 'fecha_apertura' in op:
                horas_abierto = (datetime.now() - op['fecha_apertura']).total_seconds() / 3600
                if horas_abierto >= MAX_HORAS_ABIERTO:
                    cierre, res = True, f"TIEMPO MÁXIMO ({MAX_HORAS_ABIERTO}h) ⏱️"

            # 3.3 Verificar cruce contrario
            if not cierre:
                if op['tipo'] == 'LONG' and e9_ult < e21_ult:
                    cierre, res = True, "CRUCE CONTRARIO 📉"
                elif op['tipo'] == 'SHORT' and e9_ult > e21_ult:
                    cierre, res = True, "CRUCE CONTRARIO 📈"

            if cierre:
                common.registrar_cierre(op['id'], precio_actual, res)
                common.enviar_telegram(ESTRATEGIA, simbolo,
                    f"🏁 *CIERRE MORA ({simbolo})*\nMotivo: {res}\nPrecio: {precio_actual:.5f}\n"
                    f"ID: {op['id']}")
                del operaciones_activas[simbolo]

    except Exception as e:
        print(f"⚠️ Error Mora analizando {simbolo}: {e}")

def ejecutar_bot():
    common.inicializar_db()

    # Cerrar trades trabados automáticamente
    cerrados = common.cerrar_trades_trabados(ESTRATEGIA, max_horas=MAX_HORAS_ABIERTO)

    # Reanudar trades abiertos de BD
    trades = common.obtener_trades_abiertos(ESTRATEGIA)
    for t in trades:
        operaciones_activas[t['simbolo']] = {
            'tipo': t['tipo'],
            'entrada': t['entrada'],
            'id': t['id'],
            'fecha_apertura': t.get('fecha_apertura')
        }
    if trades:
        common.enviar_telegram(ESTRATEGIA, None,
            f"🔄 *TRADES REANUDADOS (MORA)*\n{len(trades)} operación(es) recuperada(s)")

    common.enviar_telegram(ESTRATEGIA, None,
        "📊 *Bot Mora Trader EMA v2.6 Activo*\nEstrategia: Cruce EMA 9/21 (SL/TP 1:3)")

    while True:
        for activo in common.ACTIVOS:
            analizar_estrategia(activo)
            time.sleep(2)
        # Escaneo cada 5 minutos para velas de 15m
        time.sleep(300)

if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
