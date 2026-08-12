"""
Script de Trading Automático - Estrategia Mora Trader (Apertura 9:30 AM NY)
Basado en: https://www.youtube.com/watch?v=paOAuskpOLA
Versión 3.1 - Foco en manipulación del rango de apertura (Judas Swing).
Persistencia unificada en historial_operaciones vía common.
"""

import sys
import time
import traceback
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
        trades_abiertos = common.obtener_trades_abiertos(ESTRATEGIA, simbolo)
        if trades_abiertos:
            common.dlog(f"  {simbolo}: trade abierto #{trades_abiertos[0]['id']}, saltando")
            return

        tz_ny = pytz.timezone('America/New_York')
        ahora_ny = datetime.now(tz_ny)
        common.dlog(f"  {simbolo}: hora_ny={ahora_ny.strftime('%H:%M:%S')} | tiene_rango={simbolo in rangos_dia}")

        if ahora_ny.hour == 9 and 45 <= ahora_ny.minute < 50:
            df = yf.download(simbolo, period='1d', interval='15m', progress=False)
            common.dlog(f"  {simbolo}: {len(df)} velas 15m descargadas (definición rango)")
            if df.empty:
                print(f"⚠️ {simbolo}: 0 velas de yfinance (15m) - definición rango")
                return

            vela_930 = df.between_time('09:30', '09:31')
            if not vela_930.empty:
                rangos_dia[simbolo] = {
                    'alto': float(vela_930['High'].iloc[0]),
                    'bajo': float(vela_930['Low'].iloc[0]),
                    'manipulado_alto': False,
                    'manipulado_bajo': False
                }
                common.dlog(f"  {simbolo}: RANGO DEFINIDO alto={vela_930['High'].iloc[0]:.2f} bajo={vela_930['Low'].iloc[0]:.2f}")
            else:
                common.dlog(f"  {simbolo}: no se encontró vela 9:30 en el datos")

        if simbolo in rangos_dia and (ahora_ny.hour >= 9 and ahora_ny.minute >= 45) and ahora_ny.hour < 12:
            rango = rangos_dia[simbolo]
            df_actual = yf.download(simbolo, period='1d', interval='1m', progress=False)
            common.dlog(f"  {simbolo}: {len(df_actual)} velas 1m descargadas (evaluación)")
            if df_actual.empty:
                print(f"⚠️ {simbolo}: 0 velas de yfinance (1m) - evaluación")
                return

            precio_actual = float(df_actual['Close'].iloc[-1])
            common.dlog(f"  {simbolo}: precio={precio_actual:.2f} | rango={rango['bajo']:.2f}-{rango['alto']:.2f}")

            if precio_actual > rango['alto']:
                rango['manipulado_alto'] = True
                common.dlog(f"  {simbolo}: manipulado_alto=True (precio > alto)")

            if rango['manipulado_alto'] and precio_actual < (rango['alto'] - (rango['alto'] * 0.0001)):
                if simbolo not in operaciones_activas:
                    sl = precio_actual * 1.002
                    risk = sl - precio_actual
                    tp = precio_actual - (risk * 3)
                    common.dlog(f"  {simbolo}: SEÑAL SHORT (manipulación)")
                    id_op = common.registrar_apertura(ESTRATEGIA, simbolo, 'SHORT', precio_actual,
                                                      sl=sl, tp=tp,
                                                      rango_alto=rango['alto'], rango_bajo=rango['bajo'])
                    operaciones_activas[simbolo] = {'tipo': 'SHORT', 'entrada': precio_actual, 'id': id_op}
                    common.enviar_telegram(ESTRATEGIA, simbolo,
                        f"🎯 *SEÑAL NY_OPEN ({simbolo})*\n"
                        f"Dirección: SHORT\n"
                        f"Entrada: {precio_actual:.5f}\nTP: {tp:.5f}\nSL: {sl:.5f}\nID: {id_op}\n"
                        f"Motivo: Recuperación tras manipulación superior.",
                        posicion={'entrada': precio_actual, 'sl': sl, 'tp': tp})

            if precio_actual < rango['bajo']:
                rango['manipulado_bajo'] = True
                common.dlog(f"  {simbolo}: manipulado_bajo=True (precio < bajo)")

            if rango['manipulado_bajo'] and precio_actual > (rango['bajo'] + (rango['bajo'] * 0.0001)):
                if simbolo not in operaciones_activas:
                    sl = precio_actual * 0.998
                    risk = precio_actual - sl
                    tp = precio_actual + (risk * 3)
                    common.dlog(f"  {simbolo}: SEÑAL LONG (manipulación)")
                    id_op = common.registrar_apertura(ESTRATEGIA, simbolo, 'LONG', precio_actual,
                                                      sl=sl, tp=tp,
                                                      rango_alto=rango['alto'], rango_bajo=rango['bajo'])
                    operaciones_activas[simbolo] = {'tipo': 'LONG', 'entrada': precio_actual, 'id': id_op}
                    common.enviar_telegram(ESTRATEGIA, simbolo,
                        f"🎯 *SEÑAL NY_OPEN ({simbolo})*\n"
                        f"Dirección: LONG\n"
                        f"Entrada: {precio_actual:.5f}\nTP: {tp:.5f}\nSL: {sl:.5f}\nID: {id_op}\n"
                        f"Motivo: Recuperación tras manipulación inferior.",
                        posicion={'entrada': precio_actual, 'sl': sl, 'tp': tp})

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
                if p_actual >= rango['alto']:
                    cerrar, res = True, "TP: ALTO DEL RANGO ✅"
                elif p_actual < (op['entrada'] * 0.998):
                    cerrar, res = True, "SL: MANIPULACIÓN FALLIDA ❌"
            else:
                if p_actual <= rango['bajo']:
                    cerrar, res = True, "TP: BAJO DEL RANGO ✅"
                elif p_actual > (op['entrada'] * 1.002):
                    cerrar, res = True, "SL: MANIPULACIÓN FALLIDA ❌"

            if cerrar:
                if common.registrar_cierre(op['id'], p_actual, res):
                    common.enviar_telegram(ESTRATEGIA, simbolo,
                        f"🏁 *CIERRE NY OPEN ({simbolo})*\nMotivo: {res}\nPrecio: {p_actual:.5f}\n"
                        f"ID: {op['id']}")
                del operaciones_activas[simbolo]
                del rangos_dia[simbolo]

    except Exception as e:
        print(f"⚠️ Error en análisis NY: {e}")
        traceback.print_exc()

def ejecutar_bot():
    common.inicializar_db()
    print(f"🚀 {ESTRATEGIA} iniciado | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | ACTIVOS: {len(common.ACTIVOS)} | mercado_abierto: {common.horario_mercado()}")

    cerrados = common.cerrar_trades_trabados(ESTRATEGIA, max_horas=24)
    if cerrados:
        print(f"🔄 {len(cerrados)} trade(s) cerrados automáticamente al iniciar")

    trades = common.obtener_trades_abiertos(ESTRATEGIA)
    for t in trades:
        operaciones_activas[t['simbolo']] = {'tipo': t['tipo'], 'entrada': t['entrada'], 'id': t['id']}
    if trades:
        print(f"🔄 {len(trades)} trade(s) abierto(s) recuperado(s) de BD")
        common.enviar_telegram(ESTRATEGIA, None,
            f"🔄 *TRADES REANUDADOS (NY OPEN)*\n{len(trades)} operación(es) recuperada(s)")

    common.enviar_telegram(ESTRATEGIA, None,
        "🏛️ *Mora Trader NY Open Activo*\nEsperando vela de las 9:30 AM NY...")

    mercado_anterior = common.horario_mercado()
    while True:
        mercado_actual = common.horario_mercado()
        if mercado_actual != mercado_anterior:
            if mercado_actual:
                print("🔔 Mercado ABIERTO — escaneando acciones/ETF")
            else:
                print("⏰ Mercado CERRADO — pausando acciones/ETF")
            mercado_anterior = mercado_actual

        for activo in common.ACTIVOS:
            if common.es_accion_o_etf(activo) and not mercado_actual:
                common.dlog(f"  {activo}: mercado cerrado, saltando")
                continue
            analizar_apertura_ny(activo)
            time.sleep(1)

        print(f"💓 Heartbeat NY_OPEN {datetime.now().strftime('%H:%M:%S')} [rango definido: {len(rangos_dia)}] [abiertas: {len(operaciones_activas)}]")
        time.sleep(60)

if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
