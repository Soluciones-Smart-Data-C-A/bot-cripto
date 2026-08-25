"""
Bot de Trading Automático - Estrategia ICT / SMC Asian Liquidity Sweep & 1m FVG Retest
Basado estrictamente en el Reel de Instagram: https://www.instagram.com/reel/DbLdtjnC7Zf/?igsh=ZGJ3bjN1bWllYmdh

Lógica Exacta del Video:
1. Rango Asiático (7:00 PM EST/Mercado): Se determinan el Asian High y Asian Low.
2. Barrido de Liquidez (Sweep): El precio liquida el máximo (Buyside) o mínimo (Sellside) asiático.
3. Desplazamiento & Market Structure Shift (MSS): Creación del 1er Fair Value Gap (FVG) en velas de 1m.
4. Entrada en Retest: Orden cuando el precio regresa/retestea la zona del FVG.
5. Objetivos: SL en el extremo del barrido, TP en el extremo opuesto del Rango Asiático (ratio mín 1:2).
"""

import sys
import time
import traceback
import warnings
from datetime import datetime, time as dtime

import pandas as pd
import yfinance as yf

# Módulo de integración común para base de datos, notificaciones y utilidades
import common

# Desactivar advertencias innecesarias de Pandas
warnings.filterwarnings('ignore')

# Identificador único de la estrategia
ESTRATEGIA = 'INSTA_SWEEP_V1'

# Colecciones globales para monitoreo de operaciones en memoria
operaciones_activas = []
estado_bias = {}

def descargar(simbolo, periodo, intervalo):
    """
    Descarga datos de mercado desde yfinance y normaliza el DataFrame aplanando MultiIndex.
    """
    try:
        df = yf.download(simbolo, period=periodo, interval=intervalo, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        common.dlog(f"⚠️ Error descargando {simbolo}: {e}")
        return pd.DataFrame()

def calcular_atr(df, periodo=14):
    """
    Calcula el Average True Range (ATR) para filtrado y márgenes de seguridad.
    """
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(periodo).mean()

class EstrategiaLiquiditySweep:
    """
    Estrategia de Inteligencia Institucional ICT:
    Asian Session Sweep + 1-Minute Displacement + First FVG Retest
    """
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.asian_high = None
        self.asian_low = None
        self.sweep_extreme = None
        self.fvg_zona = None  # Dict: {'top': float, 'bottom': float, 'tipo': str}
        self.sweep_type = None  # 'HIGH_SWEEP' o 'LOW_SWEEP'
        self.atr = None

    def identificar_rango_asia(self, df_1m):
        """
        Calcula el Asian High y Asian Low tomando como referencia la acumulación desde las 7:00 PM (19:00).
        """
        try:
            if df_1m.empty or len(df_1m) < 60:
                return False

            df = df_1m.copy()
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)

            # Filtrar las velas pertenecientes a la sesión asiática (desde 19:00 en adelante)
            velas_asia = df[df.index.time >= dtime(19, 0)]

            if len(velas_asia) < 15:
                # Si no hay suficiente data histórica de las 7PM en el DF actual, tomar las 3 horas previas al inicio
                velas_asia = df.iloc[-180:-30]

            self.asian_high = float(velas_asia['High'].max())
            self.asian_low = float(velas_asia['Low'].min())

            common.dlog(f"  {self.simbolo}: Rango Asia 7PM => High={self.asian_high:.5f} | Low={self.asian_low:.5f}")
            return True
        except Exception as e:
            common.dlog(f"⚠️ Error identificando Rango Asiático para {self.simbolo}: {e}")
            return False

    def detectar_primer_fvg(self, df_1m, direccion):
        """
        Escanea la temporalidad de 1 minuto tras el barrido para hallar el 1er Fair Value Gap (FVG):
        - Bullish FVG: Vela 3 Low > Vela 1 High (Desequilibrio comprador tras barrido de Asian Low).
        - Bearish FVG: Vela 3 High < Vela 1 Low (Desequilibrio vendedor tras barrido de Asian High).
        """
        try:
            n = len(df_1m)
            if n < 5:
                return None

            # Buscar en el bloque de velas recientes (últimas 12 velas tras el barrido)
            for i in range(n - 1, n - 12, -1):
                c1_high = float(df_1m['High'].iloc[i - 2])
                c1_low = float(df_1m['Low'].iloc[i - 2])
                c3_high = float(df_1m['High'].iloc[i])
                c3_low = float(df_1m['Low'].iloc[i])

                if direccion == 'LONG':
                    # Desequilibrio Alcista: Mínimo de la Vela 3 no solapa el Máximo de la Vela 1
                    if c3_low > c1_high:
                        zona = {
                            'top': c3_low,
                            'bottom': c1_high,
                            'tipo': 'BULLISH'
                        }
                        common.dlog(f"  {self.simbolo}: 1er FVG Alcista (1m) detectado => Zona [{zona['bottom']:.5f} - {zona['top']:.5f}]")
                        return zona

                elif direccion == 'SHORT':
                    # Desequilibrio Bajista: Máximo de la Vela 3 no solapa el Mínimo de la Vela 1
                    if c3_high < c1_low:
                        zona = {
                            'top': c1_low,
                            'bottom': c3_high,
                            'tipo': 'BEARISH'
                        }
                        common.dlog(f"  {self.simbolo}: 1er FVG Bajista (1m) detectado => Zona [{zona['bottom']:.5f} - {zona['top']:.5f}]")
                        return zona

            return None
        except Exception as e:
            common.dlog(f"⚠️ Error buscando FVG en {self.simbolo}: {e}")
            return None

    def analizar_senial(self):
        """
        Secuencia completa del video:
        1. Marca 7:00 PM Rango Asia.
        2. Barrido del máximo/mínimo.
        3. Creación del 1er FVG en 1m.
        4. Retorno/Retest del precio al FVG.
        """
        df_1m = descargar(self.simbolo, '1d', '1m')
        if df_1m.empty or len(df_1m) < 30:
            return None

        # Cálculo de volatilidad para buffers
        df_1m['ATR'] = calcular_atr(df_1m, 14)
        self.atr = float(df_1m['ATR'].iloc[-1])

        if not self.identificar_rango_asia(df_1m):
            return None

        p_actual = float(df_1m['Close'].iloc[-1])
        min_reciente = float(df_1m['Low'].iloc[-15:].min())
        max_reciente = float(df_1m['High'].iloc[-15:].max())

        # CASO 1: BARRIDO DE ASIAN LOW (Buscamos Compras / LONG)
        if min_reciente < self.asian_low:
            self.sweep_type = 'LOW_SWEEP'
            self.sweep_extreme = min_reciente
            fvg = self.detectar_primer_fvg(df_1m, 'LONG')

            if fvg:
                # Validar Retest: El precio actual ha regresado a la zona del FVG
                if p_actual <= fvg['top'] and p_actual >= (fvg['bottom'] * 0.9995):
                    self.fvg_zona = fvg
                    return 'LONG'

        # CASO 2: BARRIDO DE ASIAN HIGH (Buscamos Ventas / SHORT)
        elif max_reciente > self.asian_high:
            self.sweep_type = 'HIGH_SWEEP'
            self.sweep_extreme = max_reciente
            fvg = self.detectar_primer_fvg(df_1m, 'SHORT')

            if fvg:
                # Validar Retest: El precio actual ha regresado a la zona del FVG
                if p_actual >= fvg['bottom'] and p_actual <= (fvg['top'] * 1.0005):
                    self.fvg_zona = fvg
                    return 'SHORT'

        return None

def chequear_entradas():
    """
    Escanea la lista de activos en busca de setups de Barrido de Liquidez + Retest FVG.
    """
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

        bot = EstrategiaLiquiditySweep(activo)
        signal = bot.analizar_senial()

        if signal:
            df_1m = descargar(activo, '1d', '1m')
            if df_1m.empty:
                continue

            p_entrada = float(df_1m['Close'].iloc[-1])
            buffer_sl = (bot.atr or (p_entrada * 0.001)) * 0.3

            if signal == 'LONG':
                # SL por debajo del mínimo del barrido o FVG
                sl = min(bot.fvg_zona['bottom'] - buffer_sl, bot.sweep_extreme - buffer_sl)
                distancia_riesgo = p_entrada - sl
                # Target: Asian High u objetivo con R:R mínimo de 1:2
                tp = max(bot.asian_high, p_entrada + (distancia_riesgo * 2.0))
            else:
                # SL por encima del máximo del barrido o FVG
                sl = max(bot.fvg_zona['top'] + buffer_sl, bot.sweep_extreme + buffer_sl)
                distancia_riesgo = sl - p_entrada
                # Target: Asian Low u objetivo con R:R mínimo de 1:2
                tp = min(bot.asian_low, p_entrada - (distancia_riesgo * 2.0))

            common.dlog(f"  {activo}: SEÑAL ICT SWEEP RETEST {signal} | Entrada={p_entrada:.5f} SL={sl:.5f} TP={tp:.5f}")

            # Registrar apertura en la Base de Datos
            id_trade = common.registrar_apertura(
                estrategia=ESTRATEGIA,
                simbolo=activo,
                tipo=signal,
                precio=p_entrada,
                sl=sl,
                tp=tp,
                rango_alto=bot.asian_high,
                rango_bajo=bot.asian_low
            )

            nueva_op = {
                'id': id_trade,
                'simbolo': activo,
                'tipo': signal,
                'entrada': p_entrada,
                'sl': sl,
                'tp': tp,
                'hora': datetime.now()
            }
            operaciones_activas.append(nueva_op)

            # Notificar vía Telegram
            common.enviar_telegram(
                ESTRATEGIA,
                activo,
                f"🎯 *SEÑAL ICT SWEEP + 1m FVG RETEST ({activo})*\n"
                f"Estrategia Reel Instagram (Asia 7PM + FVG Retest)\n"
                f"Dirección: {signal}\n"
                f"Entrada (FVG Retest): {p_entrada:.5f}\n"
                f"TP (Asian Target): {tp:.5f}\n"
                f"SL (Sweep Low/High): {sl:.5f}\n"
                f"Rango Asia (7PM): {bot.asian_low:.5f} - {bot.asian_high:.5f}\n"
                f"ID Trade: {id_trade}",
                posicion={'entrada': p_entrada, 'sl': sl, 'tp': tp}
            )

def gestionar_operaciones():
    """
    Monitorea las posiciones abiertas evaluando TP y SL en velas de 1m.
    """
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
            else:  # SHORT
                if p_actual <= op['tp']:
                    cerrar, msg = True, "TP ✅"
                elif p_actual >= op['sl']:
                    cerrar, msg = True, "SL ❌"

            if cerrar:
                if common.registrar_cierre(op['id'], p_actual, msg):
                    icono = common.icono_cierre(msg)
                    common.enviar_telegram(
                        ESTRATEGIA,
                        op['simbolo'],
                        f"🏁 *CIERRE SWEEP FVG ({op['simbolo']})*\n"
                        f"Resultado: {msg} {icono}\n"
                        f"Precio Entrada: {op['entrada']:.5f}\n"
                        f"Precio Salida: {p_actual:.5f}\n"
                        f"ID Trade: {op['id']}"
                    )
                operaciones_activas.remove(op)

        except Exception as e:
            print(f"⚠️ Error gestionando posición en {op['simbolo']}: {e}")
            traceback.print_exc()

def ejecutar_bot():
    """
    Bucle principal de ejecución del Bot.
    """
    common.inicializar_db()
    print(f"🚀 {ESTRATEGIA} (ICT Asian Sweep 7PM + 1m FVG Retest) iniciado | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Limpiar trades pendientes antiguos
    cerrados = common.cerrar_trades_trabados(ESTRATEGIA, max_horas=24)
    if cerrados:
        print(f"🔄 {len(cerrados)} trade(s) huérfanos limpiados de BD")

    # Reanudar trades activos de la Base de Datos
    trades = common.obtener_trades_abiertos(ESTRATEGIA)
    for t in trades:
        operaciones_activas.append({
            'id': t['id'],
            'simbolo': t['simbolo'],
            'tipo': t['tipo'],
            'entrada': t['entrada'],
            'sl': t['sl'],
            'tp': t['tp']
        })

    common.enviar_telegram(
        ESTRATEGIA,
        None,
        "🤖 *Bot ICT Asian Sweep + 1m FVG Retest Activo*\n"
        "Reglas del Reel:\n"
        "1. Marca Rango Asiático (7:00 PM).\n"
        "2. Detecta Barrido de Liquidez.\n"
        "3. Localiza el 1er FVG en 1 min.\n"
        "4. Ejecuta al retornar al FVG."
    )

    mercado_anterior = common.horario_mercado()

    while True:
        mercado_actual = common.horario_mercado()
        if mercado_actual != mercado_anterior:
            mercado_anterior = mercado_actual

        chequear_entradas()
        gestionar_operaciones()

        print(f"💓 Heartbeat AsianSweepFVG {datetime.now().strftime('%H:%M:%S')} [Posiciones Activas: {len(operaciones_activas)}]")
        time.sleep(60)

if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()