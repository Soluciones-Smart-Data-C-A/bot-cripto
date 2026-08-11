"""
Script de Trading Automático - Estrategia Mora Trader (Cruce de EMAs)
Basado en: https://www.youtube.com/shorts/roEy8Da2R1A
Versión 2.7 - Cruce rápido de EMA 9 y 21 con SL/TP ratio 1:3.
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
        trades_abiertos = common.obtener_trades_abiertos(ESTRATEGIA, simbolo)
        if trades_abiertos:
            common.dlog(f"  {simbolo}: trade abierto #{trades_abiertos[0]['id']}, saltando")
            return

        df = yf.download(simbolo, period='2d', interval='15m', progress=False, auto_adjust=True)
        common.dlog(f"  {simbolo}: {len(df)} velas 15m descargadas")
        if df.empty:
            print(f"⚠️ {simbolo}: 0 velas de yfinance (15m)")
            return

        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()

        ult = df.iloc[-1]
        pen = df.iloc[-2]

        precio_actual = float(ult['Close'])
        e9_ult, e21_ult = float(ult['EMA9']), float(ult['EMA21'])
        e9_pen, e21_pen = float(pen['EMA9']), float(pen['EMA21'])

        common.dlog(f"  {simbolo}: precio={precio_actual:.5f} | EMA9={e9_ult:.5f} EMA21={e21_ult:.5f} | prev_EMA9={e9_pen:.5f} prev_EMA21={e21_pen:.5f}")
        common.dlog(f"  {simbolo}: cruce_long={e9_pen <= e21_pen and e9_ult > e21_ult} | cruce_short={e9_pen >= e21_pen and e9_ult < e21_ult}")

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
                pos = common.calcular_posicion(precio_actual, sl, tp)
                pos_msg = ""
                if pos:
                    pos_msg = f"\n📐 Invertir: ${pos['usd_invertir']:.0f} | Riesgo: -${pos['perdida']:.2f} | Ganancia: +${pos['ganancia']:.2f}"
                common.enviar_telegram(ESTRATEGIA, simbolo,
                    f"🎯 *SEÑAL MORA_EMA_CROSS ({simbolo})*\n"
                    f"Dirección: LONG\n"
                    f"Entrada: {precio_actual:.5f}\nTP: {tp:.5f}\nSL: {sl:.5f}\n"
                    f"ID: {id_op}\n"
                    f"Motivo: Cruce alcista EMA 9/21{pos_msg}")

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
                pos = common.calcular_posicion(precio_actual, sl, tp)
                pos_msg = ""
                if pos:
                    pos_msg = f"\n📐 Invertir: ${pos['usd_invertir']:.0f} | Riesgo: -${pos['perdida']:.2f} | Ganancia: +${pos['ganancia']:.2f}"
                common.enviar_telegram(ESTRATEGIA, simbolo,
                    f"🎯 *SEÑAL MORA_EMA_CROSS ({simbolo})*\n"
                    f"Dirección: SHORT\n"
                    f"Entrada: {precio_actual:.5f}\nTP: {tp:.5f}\nSL: {sl:.5f}\n"
                    f"ID: {id_op}\n"
                    f"Motivo: Cruce bajista EMA 9/21{pos_msg}")

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
                    f"🏁 *CIERRE MORA ({simbolo})*\nMotivo: {res} {common.icono_cierre(res)}\nPrecio: {precio_actual:.5f}\n"
                    f"ID: {op['id']}")
                del operaciones_activas[simbolo]

    except Exception as e:
        print(f"⚠️ Error Mora analizando {simbolo}: {e}")
        traceback.print_exc()

def ejecutar_bot():
    common.inicializar_db()
    print(f"🚀 {ESTRATEGIA} iniciado | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | ACTIVOS: {len(common.ACTIVOS)} | mercado_abierto: {common.horario_mercado()}")

    cerrados = common.cerrar_trades_trabados(ESTRATEGIA, max_horas=MAX_HORAS_ABIERTO)
    if cerrados:
        print(f"🔄 {len(cerrados)} trade(s) cerrados automáticamente al iniciar")

    trades = common.obtener_trades_abiertos(ESTRATEGIA)
    for t in trades:
        operaciones_activas[t['simbolo']] = {
            'tipo': t['tipo'],
            'entrada': t['entrada'],
            'id': t['id'],
            'sl': t.get('sl'),
            'tp': t.get('tp'),
            'fecha_apertura': t.get('fecha_apertura')
        }
    if trades:
        print(f"🔄 {len(trades)} trade(s) abierto(s) recuperado(s) de BD")
        common.enviar_telegram(ESTRATEGIA, None,
            f"🔄 *TRADES REANUDADOS (MORA)*\n{len(trades)} operación(es) recuperada(s)")

    common.enviar_telegram(ESTRATEGIA, None,
        "📊 *Bot Mora Trader EMA v2.6 Activo*\nEstrategia: Cruce EMA 9/21 (SL/TP 1:3)")

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
            analizar_estrategia(activo)
            time.sleep(2)

        print(f"💓 Heartbeat MORA {datetime.now().strftime('%H:%M:%S')} [abiertas: {len(operaciones_activas)}]")
        time.sleep(300)

if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
