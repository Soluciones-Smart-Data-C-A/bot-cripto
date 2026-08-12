"""
Script de Trading Automático - Estrategia SMC (Smart Money Concepts)
Basado en: https://www.youtube.com/watch?v=f55zVH388z4
Concepto: FVG (Fair Value Gaps) + BOS (Break of Structure).
Persistencia unificada en historial_operaciones vía common.
"""

import sys
import time
import traceback
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

import common

warnings.filterwarnings('ignore')

ESTRATEGIA = 'SMC_FVG_BOS'
operaciones_activas = {}

BOS_WINDOW = 5


def detect_fair_value_gaps(df):
    """Detecta Ineficiencias FVG (BISI y SIBI) y calcula el punto medio (50%)."""
    df = df.copy()

    # BISI (Fair Value Gap Alcista): High de vela 1 < Low de vela 3
    df['BISI'] = df['High'].shift(2) < df['Low']
    df['BISI_top'] = np.where(df['BISI'], df['Low'], np.nan)
    df['BISI_bottom'] = np.where(df['BISI'], df['High'].shift(2), np.nan)
    df['BISI_mid'] = (df['BISI_top'] + df['BISI_bottom']) / 2

    # SIBI / CBI (Fair Value Gap Bajista): Low de vela 1 > High de vela 3
    df['SIBI'] = df['Low'].shift(2) > df['High']
    df['SIBI_top'] = np.where(df['SIBI'], df['Low'].shift(2), np.nan)
    df['SIBI_bottom'] = np.where(df['SIBI'], df['High'], np.nan)
    df['SIBI_mid'] = (df['SIBI_top'] + df['SIBI_bottom']) / 2

    return df


def detect_break_of_structure(df, window=BOS_WINDOW):
    """Identifica cierres con cuerpo por encima/debajo de máximos/mínimos anteriores (BOS)."""
    df['prev_high'] = df['High'].shift(1).rolling(window=window).max()
    df['prev_low'] = df['Low'].shift(1).rolling(window=window).min()

    df['BOS_Bullish'] = df['Close'] > df['prev_high']
    df['BOS_Bearish'] = df['Close'] < df['prev_low']

    return df


def generate_smc_signals(df):
    """Genera señales de Trading basadas en FVG + Respeta 50% + BOS."""
    df = detect_fair_value_gaps(df)
    df = detect_break_of_structure(df)

    df['Signal'] = 0
    df['Stop_Loss'] = np.nan
    df['Take_Profit'] = np.nan      # TP ratio 1:3
    df['TP_Final'] = np.nan         # Tope del rango (FVG)

    active_sibi_mid = None
    active_bisi_mid = None

    for i in range(2, len(df)):
        if df['SIBI'].iloc[i]:
            active_sibi_mid = df['SIBI_mid'].iloc[i]
        if df['BISI'].iloc[i]:
            active_bisi_mid = df['BISI_mid'].iloc[i]

        # Estrategia Bajista (Short)
        if df['BOS_Bearish'].iloc[i] and active_sibi_mid is not None:
            if df['Close'].iloc[i] < active_sibi_mid:
                entry = float(df['Close'].iloc[i])
                sl = float(df['High'].iloc[i-2:i+1].max())
                risk = sl - entry
                tp_1_3 = entry - (risk * 3)
                tp_fvg = float(df['SIBI_bottom'].iloc[i])

                # Validar que TP FVG esté por debajo del entry para SHORT
                if tp_fvg >= entry:
                    tp_final = tp_1_3  # Usar TP 1:3 como fallback
                else:
                    # Usar el más cercano al entry (el MAYOR entre los dos)
                    tp_final = max(tp_1_3, tp_fvg)

                df.at[df.index[i], 'Signal'] = -1
                df.at[df.index[i], 'Stop_Loss'] = sl
                df.at[df.index[i], 'Take_Profit'] = tp_1_3
                df.at[df.index[i], 'TP_Final'] = tp_final
                active_sibi_mid = None

        # Estrategia Alcista (Long)
        elif df['BOS_Bullish'].iloc[i] and active_bisi_mid is not None:
            if df['Close'].iloc[i] > active_bisi_mid:
                entry = float(df['Close'].iloc[i])
                sl = float(df['Low'].iloc[i-2:i+1].min())
                risk = entry - sl
                tp_1_3 = entry + (risk * 3)
                tp_fvg = float(df['BISI_top'].iloc[i])

                # Validar que TP FVG esté por encima del entry para LONG
                if tp_fvg <= entry:
                    tp_final = tp_1_3  # Usar TP 1:3 como fallback
                else:
                    # Usar el más cercano al entry (el MENOR entre los dos)
                    tp_final = min(tp_1_3, tp_fvg)

                df.at[df.index[i], 'Signal'] = 1
                df.at[df.index[i], 'Stop_Loss'] = sl
                df.at[df.index[i], 'Take_Profit'] = tp_1_3
                df.at[df.index[i], 'TP_Final'] = tp_final
                active_bisi_mid = None

    return df


def analizar_smc(simbolo):
    try:
        trades_abiertos = common.obtener_trades_abiertos(ESTRATEGIA, simbolo)
        if trades_abiertos:
            common.dlog(f"  {simbolo}: trade abierto #{trades_abiertos[0]['id']}, saltando")
            return

        df = yf.download(simbolo, period='5d', interval='15m', progress=False, auto_adjust=True)
        common.dlog(f"  {simbolo}: {len(df)} velas 15m descargadas")
        if df.empty:
            print(f"⚠️ {simbolo}: 0 velas de yfinance (15m)")
            return

        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

        df = generate_smc_signals(df)

        ult = df.iloc[-1]
        signal = int(ult['Signal'])
        precio_actual = float(ult['Close'])

        common.dlog(f"  {simbolo}: precio={precio_actual:.5f} | signal={signal}")

        if signal == 0:
            return

        tipo = 'LONG' if signal == 1 else 'SHORT'
        sl = float(ult['Stop_Loss'])
        tp_1_3 = float(ult['Take_Profit'])
        tp_final = float(ult['TP_Final'])

        common.dlog(f"  {simbolo}: SEÑAL {tipo} | sl={sl:.5f} tp={tp_final:.5f}")

        if simbolo in operaciones_activas:
            return

        id_op = common.registrar_apertura(ESTRATEGIA, simbolo, tipo, precio_actual,
                                          sl=sl, tp=tp_final)
        if id_op is None:
            return

        operaciones_activas[simbolo] = {'tipo': tipo, 'entrada': precio_actual,
                                         'sl': sl, 'tp': tp_final, 'id': id_op}

        common.enviar_telegram(ESTRATEGIA, simbolo,
            f"🎯 *SEÑAL SMC_FVG_BOS ({simbolo})*\n"
            f"Dirección: {tipo}\n"
            f"Entrada: {precio_actual:.5f}\n"
            f"SL: {sl:.5f}\n"
            f"TP (1:3): {tp_1_3:.5f}\n"
            f"TP Final: {tp_final:.5f}\n"
            f"ID: {id_op}",
            posicion={'entrada': precio_actual, 'sl': sl, 'tp': tp_final})

    except Exception as e:
        print(f"⚠️ Error SMC analizando {simbolo}: {e}")
        traceback.print_exc()


def gestionar_operaciones():
    for simbolo in list(operaciones_activas.keys()):
        try:
            op = operaciones_activas[simbolo]

            trades_abiertos = common.obtener_trades_abiertos(ESTRATEGIA, simbolo)
            if not trades_abiertos:
                del operaciones_activas[simbolo]
                continue

            df = yf.download(simbolo, period='1d', interval='1m', progress=False, auto_adjust=True)
            if df.empty:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

            p_actual = float(df['Close'].iloc[-1])
            cerrar = False
            msg = ""

            if op['tipo'] == 'LONG':
                if p_actual >= op.get('tp', float('inf')):
                    cerrar, msg = True, "TP ✅"
                elif p_actual <= op.get('sl', 0):
                    cerrar, msg = True, "SL ❌"
            else:
                if p_actual <= op.get('tp', 0):
                    cerrar, msg = True, "TP ✅"
                elif p_actual >= op.get('sl', float('inf')):
                    cerrar, msg = True, "SL ❌"

            if cerrar:
                if common.registrar_cierre(op['id'], p_actual, msg):
                    common.enviar_telegram(ESTRATEGIA, simbolo,
                        f"🏁 *CIERRE SMC ({simbolo})*\nMotivo: {msg}\nPrecio: {p_actual:.5f}\n"
                        f"ID: {op['id']}")
                del operaciones_activas[simbolo]

        except Exception as e:
            print(f"⚠️ Error SMC gestionando {simbolo}: {e}")
            traceback.print_exc()


def ejecutar_bot():
    common.inicializar_db()
    print(f"🚀 {ESTRATEGIA} iniciado | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | ACTIVOS: {len(common.ACTIVOS)} | mercado_abierto: {common.horario_mercado()}")

    cerrados = common.cerrar_trades_trabados(ESTRATEGIA, max_horas=24)
    if cerrados:
        print(f"🔄 {len(cerrados)} trade(s) cerrados automáticamente al iniciar")

    trades = common.obtener_trades_abiertos(ESTRATEGIA)
    for t in trades:
        operaciones_activas[t['simbolo']] = {'tipo': t['tipo'], 'entrada': t['entrada'],
                                              'sl': t['sl'], 'tp': t['tp'], 'id': t['id']}
    if trades:
        print(f"🔄 {len(trades)} trade(s) abierto(s) recuperado(s) de BD")
        common.enviar_telegram(ESTRATEGIA, None,
            f"🔄 *TRADES REANUDADOS (SMC)*\n{len(trades)} operación(es) recuperada(s)")

    common.enviar_telegram(ESTRATEGIA, None,
        "📈 *Bot SMC FVG/BOS Activo*\nEstrategia: Fair Value Gaps + Break of Structure.")

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
            analizar_smc(activo)
            time.sleep(2)

        gestionar_operaciones()

        print(f"💓 Heartbeat SMC {datetime.now().strftime('%H:%M:%S')} [Operaciones activas: {len(operaciones_activas)}]")

        time.sleep(60)


if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
