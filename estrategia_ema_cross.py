"""
Script de Trading Automático - Estrategia Mora Trader (Apertura 9:30 AM NY)
Basado en: https://www.youtube.com/watch?v=paOAuskpOLA
Versión 3.1 - Foco en manipulación del rango de apertura (Judas Swing).
Persistencia unificada en historial_operaciones vía common.
"""

import sys
import time
import warnings
from datetime import datetime

import pandas as pd
import pytz
import yfinance as yf

import common

warnings.filterwarnings('ignore')

ESTRATEGIA = 'NY_OPEN'
rangos_dia = {}  # High/Low de la vela de las 9:30 AM NY
operaciones_activas = {}

def analizar_apertura_ny(simbolo):
    try:
        tz_ny = pytz.timezone('America/New_York')
        ahora_ny = datetime.now(tz_ny)

        # 1. Definir el Rango de la Vela de las 9:30 AM NY (15 min)
        if ahora_ny.hour == 9 and 45 <= ahora_ny.minute < 50:
            df = yf.download(simbolo, period='1d', interval='15m', progress=False)
            if df.empty:
                return

            vela_930 = df.between_time('09:30', '09:31')
            if not vela_930.empty:
                rangos_dia[simbolo] = {
                    'alto': float(vela_930['High'].iloc[0]),
                    'bajo': float(vela_930['Low'].iloc[0]),
                    'manipulado_alto': False,
                    'manipulado_bajo': False
                }
                common.enviar_telegram(ESTRATEGIA, simbolo,
                    f"📌 *RANGO NY SET ({simbolo})*\n"
                    f"Alto: {rangos_dia[simbolo]['alto']:.5f}\n"
                    f"Bajo: {rangos_dia[simbolo]['bajo']:.5f}")

        # 2. Buscar Manipulación y Entrada (Post 9:45 AM NY)
        if simbolo in rangos_dia and (ahora_ny.hour >= 9 and ahora_ny.minute >= 45) and ahora_ny.hour < 12:
            rango = rangos_dia[simbolo]
            df_actual = yf.download(simbolo, period='1d', interval='1m', progress=False)
            if df_actual.empty:
                return

            precio_actual = float(df_actual['Close'].iloc[-1])

            # Detectar Manipulación Superior (Busca Ventas)
            if precio_actual > rango['alto']:
                rango['manipulado_alto'] = True

            # Entrada en Venta: Manipuló el alto y vuelve al rango
            if rango['manipulado_alto'] and precio_actual < (rango['alto'] - (rango['alto'] * 0.0001)):
                if simbolo not in operaciones_activas:
                    sl = precio_actual * 1.002
                    tp = rango['bajo']  # Target: extremo opuesto del rango
                    id_op = common.registrar_apertura(ESTRATEGIA, simbolo, 'SHORT', precio_actual,
                                                      sl=sl, tp=tp,
                                                      rango_alto=rango['alto'], rango_bajo=rango['bajo'])
                    operaciones_activas[simbolo] = {'tipo': 'SHORT', 'entrada': precio_actual, 'id': id_op}
                    common.enviar_telegram(ESTRATEGIA, simbolo,
                        f"📉 *ENTRADA SHORT (NY OPEN)*\nPar: {simbolo}\nPrecio: {precio_actual:.5f}\n"
                        f"SL: {sl:.5f}\nTP: {tp:.5f}\nID: {id_op}\n"
                        f"Motivo: Recuperación tras manipulación superior.")

            # Detectar Manipulación Inferior (Busca Compras)
            if precio_actual < rango['bajo']:
                rango['manipulado_bajo'] = True

            # Entrada en Compra: Manipuló el bajo y vuelve al rango
            if rango['manipulado_bajo'] and precio_actual > (rango['bajo'] + (rango['bajo'] * 0.0001)):
                if simbolo not in operaciones_activas:
                    sl = precio_actual * 0.998
                    tp = rango['alto']  # Target: extremo opuesto del rango
                    id_op = common.registrar_apertura(ESTRATEGIA, simbolo, 'LONG', precio_actual,
                                                      sl=sl, tp=tp,
                                                      rango_alto=rango['alto'], rango_bajo=rango['bajo'])
                    operaciones_activas[simbolo] = {'tipo': 'LONG', 'entrada': precio_actual, 'id': id_op}
                    common.enviar_telegram(ESTRATEGIA, simbolo,
                        f"🚀 *ENTRADA LONG (NY OPEN)*\nPar: {simbolo}\nPrecio: {precio_actual:.5f}\n"
                        f"SL: {sl:.5f}\nTP: {tp:.5f}\nID: {id_op}\n"
                        f"Motivo: Recuperación tras manipulación inferior.")

        # 3. Gestión de Cierre (Target: extremo opuesto del rango)
        if simbolo in operaciones_activas:
            op = operaciones_activas[simbolo]
            rango = rangos_dia[simbolo]
            df_cierre = yf.download(simbolo, period='1d', interval='1m', progress=False)
            if df_cierre.empty:
                return
            p_actual = float(df_cierre['Close'].iloc[-1])

            cerrar = False
            res = ""

            if op['tipo'] == 'LONG':
                if p_actual >= rango['alto']:  # Take Profit en el alto del rango
                    cerrar, res = True, "TP: ALTO DEL RANGO ✅"
                elif p_actual < (op['entrada'] * 0.998):  # Stop Loss
                    cerrar, res = True, "SL: MANIPULACIÓN FALLIDA ❌"
            else:
                if p_actual <= rango['bajo']:  # Take Profit en el bajo del rango
                    cerrar, res = True, "TP: BAJO DEL RANGO ✅"
                elif p_actual > (op['entrada'] * 1.002):  # Stop Loss
                    cerrar, res = True, "SL: MANIPULACIÓN FALLIDA ❌"

            if cerrar:
                common.registrar_cierre(op['id'], p_actual, res)
                common.enviar_telegram(ESTRATEGIA, simbolo,
                    f"🏁 *CIERRE NY OPEN ({simbolo})*\nResultado: {res}\nPrecio: {p_actual:.5f}\n"
                    f"ID: {op['id']}")
                del operaciones_activas[simbolo]
                del rangos_dia[simbolo]  # Una operación por día según la estrategia

    except Exception as e:
        print(f"⚠️ Error en análisis NY: {e}")

def ejecutar_bot():
    common.inicializar_db()
    common.enviar_telegram(ESTRATEGIA, None,
        "🏛️ *Mora Trader NY Open Activo*\nEsperando vela de las 9:30 AM NY...")

    while True:
        for activo in common.ACTIVOS:
            analizar_apertura_ny(activo)
            time.sleep(1)
        time.sleep(60)

if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
