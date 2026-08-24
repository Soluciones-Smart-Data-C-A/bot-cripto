"""
Script de Trading Automático - Estrategia Liquidity Sweep & Vela Envolvente (SanchezZFX)
Basado en: https://www.youtube.com/watch?v=gVgYo5YMovg
Conceptos Clave:

El DÓNDE: Identificación de liquidez en niveles clave (Máximos/Mínimos H1/H4 o de Sesión).

El BARRIDO (Liquidity Sweep): Captura de Stops superando o rompiendo levemente el nivel clave.

El CUÁNDO (Confirmación): Formación de VELA ENVOLVENTE (Engulfing) en 5m con cuerpo sólido.

Entrada y Gestión: Entrada al cierre de la envolvente, SL en el extremo del barrido y TP 1:1 (R:R).
Persistencia unificada vía common.py.
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

ESTRATEGIA = 'SANCHEZZFX_SWEEP'
operaciones_activas = []
rangos_descartados = {}  # {simbolo: {'alto': float, 'bajo': float}}

RATIO_RISK_REWARD = 1.0  # Ratio 1:1 según la especificación del vídeo


def descargar(simbolo, periodo, intervalo):
    """Descarga datos desde yfinance limpiando encabezados multi-índice."""
    try:
        df = yf.download(simbolo, period=periodo, interval=intervalo, progress=False)
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        common.dlog(f"Error descargando {simbolo}: {e}")
        return pd.DataFrame()


# ==========================================
# CLASE DE LA ESTRATEGIA (SANCHEZZFX)
# ==========================================
class EstrategiaSanchezZFX:
    def __init__(self, simbolo):
        self.simbolo = simbolo
        self.nivel_alto = None  # Alto clave H1/H4/Sesión
        self.nivel_bajo = None  # Bajo clave H1/H4/Sesión

    def identificar_niveles_liquidez(self):
        """
        Paso 1: Identificar los niveles de liquidez donde se acumulan Stop Losses (El DÓNDE).
        Se toman los máximos y mínimos de las temporalidades H1/H4 recientes.
        """
        try:
            df_h1 = descargar(self.simbolo, '5d', '1h')
            if df_h1.empty or len(df_h1) < 10:
                common.dlog(f"⚠️ {self.simbolo}: Insuficientes velas 1h para niveles de liquidez")
                return False

            # Tomamos el bloque reciente de consolidación/sesión (últimas 12 velas de 1h)
            velas_referencia = df_h1.iloc[-13:-1]
            self.nivel_alto = float(velas_referencia['High'].max())
            self.nivel_bajo = float(velas_referencia['Low'].min())

            common.dlog(f"  {self.simbolo} [Niveles H1]: Bajo={self.nivel_bajo:.5f} | Alto={self.nivel_alto:.5f}")
            return True
        except Exception as e:
            print(f"⚠️ Error {self.simbolo} estableciendo niveles: {e}")
            traceback.print_exc()
            return False

    def es_vela_envolvente(self, df_5m, direccion):
        """
        Paso 3: Confirmación por Vela Envolvente (El CUÁNDO).
        - Para LONG: Vela 5m alcista cuyo cuerpo o rango envuelve a la vela previa y cierra con cuerpo sólido.
        - Para SHORT: Vela 5m bajista cuyo cuerpo o rango envuelve a la vela previa y cierra con cuerpo sólido.
        """
        if len(df_5m) < 3:
            return False

        vela_actual = df_5m.iloc[-1]
        vela_previa = df_5m.iloc[-2]

        open_curr, close_curr = float(vela_actual['Open']), float(vela_actual['Close'])
        high_curr, low_curr = float(vela_actual['High']), float(vela_actual['Low'])

        open_prev, close_prev = float(vela_previa['Open']), float(vela_previa['Close'])

        cuerpo_actual = abs(close_curr - open_curr)
        rango_total = high_curr - low_curr

        if rango_total == 0:
            return False

        # Rechazo/Mechas: La vela debe ser un cuerpo con convicción (> 50% de su rango total)
        porcentaje_cuerpo = cuerpo_actual / rango_total
        if porcentaje_cuerpo < 0.45:
            common.dlog(f"  {self.simbolo}: Vela rechazada por tener demasiado mecha (Cuerpo: {porcentaje_cuerpo:.2%})")
            return False

        if direccion == 'LONG':
            # Vela alcista
            es_alcista = close_curr > open_curr
            # Envuelve el cierre/apertura o el cuerpo anterior
            envuelve = (close_curr > max(open_prev, close_prev)) and (open_curr <= min(open_prev, close_prev) or close_curr > float(vela_previa['High']))
            return es_alcista and envuelve

        elif direccion == 'SHORT':
            # Vela bajista
            es_bajista = close_curr < open_curr
            # Envuelve el cierre/apertura o el cuerpo anterior
            envuelve = (close_curr < min(open_prev, close_prev)) and (open_curr >= max(open_prev, close_prev) or close_curr < float(vela_previa['Low']))
            return es_bajista and envuelve

        return False

    def analizar_patron_sweep(self):
        """
        Paso 2 y 3: Evalúa el Liquidity Sweep + Confirmación por Envolvente en 5M.
        """
        df_m5 = descargar(self.simbolo, '2d', '5m')
        if df_m5.empty or len(df_m5) < 15:
            common.dlog(f"⚠️ {self.simbolo}: Sin suficientes datos en 5m")
            return None

        # Evaluamos las últimas velas en 5m
        ultimas_velas = df_m5.iloc[-10:]
        low_reciente = float(ultimas_velas['Low'].min())
        high_reciente = float(ultimas_velas['High'].max())
        precio_actual = float(df_m5['Close'].iloc[-1])

        # ---------------------------------------------------------------------
        # ESCENARIO 1: SWEEP DE MÍNIMOS (Barrido de liquidez vendedora) -> LONG
        # ---------------------------------------------------------------------
        # El precio toca o rompe por debajo del bajo clave (toma de stops) y reacciona
        hizo_sweep_bajo = low_reciente < self.nivel_bajo and precio_actual > (self.nivel_bajo * 0.995)
        if hizo_sweep_bajo:
            common.dlog(f"  {self.simbolo}: ⚡ Sweep de Mínimo detectado ({low_reciente:.5f} < {self.nivel_bajo:.5f})")
            if self.es_vela_envolvente(df_m5, 'LONG'):
                sl = low_reciente * 0.9995  # SL justo bajo el mínimo absoluto del barrido
                distancia_riesgo = precio_actual - sl
                tp = precio_actual + (distancia_riesgo * RATIO_RISK_REWARD)
                return {
                    'tipo': 'LONG',
                    'entrada': precio_actual,
                    'sl': sl,
                    'tp': tp,
                    'sweep_extreme': low_reciente
                }

        # ---------------------------------------------------------------------
        # ESCENARIO 2: SWEEP DE MÁXIMOS (Barrido de liquidez compradora) -> SHORT
        # ---------------------------------------------------------------------
        # El precio toca o rompe por encima del alto clave (toma de stops) y reacciona
        hizo_sweep_alto = high_reciente > self.nivel_alto and precio_actual < (self.nivel_alto * 1.005)
        if hizo_sweep_alto:
            common.dlog(f"  {self.simbolo}: ⚡ Sweep de Máximo detectado ({high_reciente:.5f} > {self.nivel_alto:.5f})")
            if self.es_vela_envolvente(df_m5, 'SHORT'):
                sl = high_reciente * 1.0005  # SL justo sobre el máximo absoluto del barrido
                distancia_riesgo = sl - precio_actual
                tp = precio_actual - (distancia_riesgo * RATIO_RISK_REWARD)
                return {
                    'tipo': 'SHORT',
                    'entrada': precio_actual,
                    'sl': sl,
                    'tp': tp,
                    'sweep_extreme': high_reciente
                }

        return None


# ==========================================
# LÓGICA DE ENTRADAS Y GESTIÓN DE TRADES
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

        # Verificar si el rango o activo fue descartado temporalmente por un SL previo
        if activo in rangos_descartados:
            rango = rangos_descartados[activo]
            if rango['bajo'] is not None and rango['alto'] is not None:
                try:
                    df_precio = descargar(activo, '1d', '1m')
                    if not df_precio.empty:
                        p_actual = float(df_precio['Close'].iloc[-1])
                        if rango['bajo'] <= p_actual <= rango['alto']:
                            common.dlog(f"  {activo}: rango descartado [{rango['bajo']:.2f}-{rango['alto']:.2f}] tras SL, saltando")
                            continue
                        else:
                            common.dlog(f"  {activo}: el precio salió del rango descartado, habilitando nueva señal")
                            del rangos_descartados[activo]
                            common.limpiar_rango_descartado(ESTRATEGIA, activo)
                except Exception:
                    pass

        bot = EstrategiaSanchezZFX(activo)
        if bot.identificar_niveles_liquidez():
            senal = bot.analizar_patron_sweep()
            if senal:
                p_entrada = senal['entrada']
                sl = senal['sl']
                tp = senal['tp']
                tipo = senal['tipo']

                common.dlog(f"  {activo}: 🎯 SEÑAL SANCHEZZFX ({tipo}) | entrada={p_entrada:.5f} sl={sl:.5f} tp={tp:.5f}")

                nueva_op = {
                    'simbolo': activo,
                    'tipo': tipo,
                    'entrada': p_entrada,
                    'sl': sl,
                    'tp': tp,
                    'hora': datetime.now(),
                    'rango_alto': bot.nivel_alto,
                    'rango_bajo': bot.nivel_bajo
                }

                nueva_op['id'] = common.registrar_apertura(
                    ESTRATEGIA, activo, tipo, p_entrada,
                    sl=sl, tp=tp,
                    rango_alto=bot.nivel_alto, rango_bajo=bot.nivel_bajo
                )
                operaciones_activas.append(nueva_op)

                common.enviar_telegram(
                    ESTRATEGIA, activo,
                    f"⚡ *SEÑAL SWEEP + ENVOLVENTE ({activo})*\n"
                    f"Estrategia: SanchezZFX\n"
                    f"Dirección: *{tipo}*\n"
                    f"Entrada: `{p_entrada:.5f}`\n"
                    f"SL (Extremo Barrido): `{sl:.5f}`\n"
                    f"TP (Ratio 1:{RATIO_RISK_REWARD:.1f}): `{tp:.5f}`\n"
                    f"ID Trade: `{nueva_op['id']}`",
                    posicion={'entrada': p_entrada, 'sl': sl, 'tp': tp}
                )


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
            else:  # SHORT
                if p_actual <= op['tp']:
                    cerrar, msg = True, "TP ✅"
                elif p_actual >= op['sl']:
                    cerrar, msg = True, "SL ❌"

            if cerrar:
                if common.registrar_cierre(op['id'], p_actual, msg):
                    common.enviar_telegram(
                        ESTRATEGIA, op['simbolo'],
                        f"🏁 *CIERRE SWEEP ({op['simbolo']})*\n"
                        f"Motivo: {msg}\n"
                        f"Precio Salida: `{p_actual:.5f}`\n"
                        f"ID: `{op['id']}`"
                    )

                if 'SL' in msg:
                    rango_a = op.get('rango_alto')
                    rango_b = op.get('rango_bajo')
                    if rango_a is not None and rango_b is not None:
                        rangos_descartados[op['simbolo']] = {'alto': rango_a, 'bajo': rango_b}
                        common.guardar_rango_descartado(ESTRATEGIA, op['simbolo'], rango_a, rango_b)
                elif 'TP' in msg:
                    if op['simbolo'] in rangos_descartados:
                        del rangos_descartados[op['simbolo']]
                        common.limpiar_rango_descartado(ESTRATEGIA, op['simbolo'])

                operaciones_activas.remove(op)
        except Exception as e:
            print(f"⚠️ Error gestionando {op['simbolo']}: {e}")
            traceback.print_exc()


def ejecutar_bot():
    common.inicializar_db()
    print(f"🚀 {ESTRATEGIA} iniciado | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | ACTIVOS: {len(common.ACTIVOS)} | Mercado Abierto: {common.horario_mercado()}")

    cerrados = common.cerrar_trades_trabados(ESTRATEGIA, max_horas=24)
    if cerrados:
        print(f"🔄 {len(cerrados)} trade(s) cerrados automáticamente al iniciar")

    rangos_descartados.update(common.cargar_rangos_descartados(ESTRATEGIA))
    if rangos_descartados:
        print(f"🔒 {len(rangos_descartados)} rango(s) descartado(s) cargado(s) de BD")

    trades = common.obtener_trades_abiertos(ESTRATEGIA)
    for t in trades:
        operaciones_activas.append({
            'simbolo': t['simbolo'], 'tipo': t['tipo'], 'entrada': t['entrada'],
            'sl': t['sl'], 'tp': t['tp'], 'id': t['id']
        })
    if trades:
        print(f"🔄 {len(trades)} trade(s) abierto(s) recuperado(s) de BD")
        common.enviar_telegram(ESTRATEGIA, None, f"🔄 *TRADES REANUDADOS ({ESTRATEGIA})*\n{len(trades)} operación(es) recuperada(s)")

    common.enviar_telegram(
        ESTRATEGIA, None,
        "🤖 *Bot SanchezZFX Sweep v1.0 Activo*\nEstrategia: Liquidity Sweep + Confirmación Vela Envolvente (Ratio 1:1)."
    )

    mercado_anterior = common.horario_mercado()
    while True:
        mercado_actual = common.horario_mercado()
        if mercado_actual != mercado_anterior:
            if mercado_actual:
                print("🔔 Mercado ABIERTO — escaneando acciones/ETF")
            else:
                print("⏰ Mercado CERRADO — pausando acciones/ETF")
            mercado_anterior = mercado_actual

        gestionar_operaciones()
        chequear_entradas()

        print(f"💓 Heartbeat {ESTRATEGIA} {datetime.now().strftime('%H:%M:%S')} [Operaciones activas: {len(operaciones_activas)}]")
        time.sleep(60)


if __name__ == "__main__":
    if not common.verificar_config():
        sys.exit(1)
    ejecutar_bot()
