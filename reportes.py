"""
Módulo de Reportes - Análisis de frecuencias de ganancias.
Detecta mejores horas, días, mercados, estrategias, pares y tipos
según el historial de operaciones cerradas.

Usado por el dashboard (ruta /api/reportes).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import os

import common

DIAS_SEMANA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
DIAS_DB = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
DIAS_DB_TO_IDX = {nombre: i for i, nombre in enumerate(DIAS_DB)}

MIN_TRADES_TOP = 3

# Zona horaria en la que el bot GUARDA los datetimes: coincide con el TZ del contenedor
# (las estrategias guardan datetime.now() local). Si Coolify fuerza TZ=UTC, esto es UTC.
ZONA_DATOS = os.environ.get('TZ', 'UTC') or 'UTC'
# Zona horaria por defecto para mostrar los reportes (por usuario)
ZONA_POR_DEFECTO = 'America/Caracas'


def _convertir(fecha_naive, zona=None):
    """Interpreta un datetime naive como ZONA_DATOS y lo convierte a la zona pedida."""
    if fecha_naive is None:
        return None
    if not zona:
        zona = ZONA_DATOS
    try:
        aware = fecha_naive.replace(tzinfo=ZoneInfo(ZONA_DATOS))
        return aware.astimezone(ZoneInfo(zona)).replace(tzinfo=None)
    except Exception as e:
        print(f"⚠️ Error convirtiendo zona ({zona}): {e}")
        return fecha_naive


def _fetch_cerrados(filtros):
    """Lee trades cerrados (apertura, resultado, tipo, precios) para agrupar en Python."""
    conn = common.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT fecha_apertura, resultado, tipo, precio_entrada, precio_salida
            FROM historial_operaciones
            WHERE {filtros[0]}
        """, filtros[1])
        return cursor.fetchall()
    except Exception as e:
        print(f"❌ Error leyendo trades para reporte: {e}")
        return []
    finally:
        conn.close()


def _mercado_sql():
    """Devuelve la expresión SQL que clasifica cada símbolo en CRIPTO o ACCIONES/ETF."""
    acciones = "', '".join(sorted(common.ACCIONES_ETF))
    return f"""CASE WHEN simbolo IN ('{acciones}') THEN 'ACCIONES/ETF' ELSE 'CRIPTO' END"""


def _construir_filtros(estrategia=None, simbolo=None, mercado=None, tipo=None,
                       desde=None, hasta=None):
    """Construye WHERE (trades cerrados) + params a partir de los filtros recibidos."""
    where = ["resultado != 'ABIERTA'"]
    params = []

    if estrategia:
        where.append("estrategia = %s")
        params.append(estrategia)
    if simbolo:
        where.append("simbolo = %s")
        params.append(simbolo)
    if mercado:
        mercado = mercado.upper()
        if mercado == 'CRIPTO':
            where.append("simbolo NOT IN ({})".format(
                ",".join(["%s"] * len(common.ACCIONES_ETF))))
            params.extend(sorted(common.ACCIONES_ETF))
        elif mercado == 'ACCIONES' or mercado == 'ACCIONES/ETF':
            where.append("simbolo IN ({})".format(
                ",".join(["%s"] * len(common.ACCIONES_ETF))))
            params.extend(sorted(common.ACCIONES_ETF))
    if tipo:
        where.append("tipo = %s")
        params.append(tipo.upper())
    if desde:
        where.append("fecha_apertura >= %s")
        params.append(desde)
    if hasta:
        where.append("fecha_apertura <= %s")
        params.append(hasta + " 23:59:59")

    return " AND ".join(where), params


def _procesar_filas(rows):
    """Convierte filas (clave, total, wins, losses, pnl) en dicts con win_rate."""
    items = []
    for clave, total, wins, losses, pnl in rows:
        items.append({
            'clave': clave,
            'total': int(total or 0),
            'wins': int(wins or 0),
            'losses': int(losses or 0),
            'win_rate': round((wins / (wins + losses) * 100) if (wins + losses) > 0 else 0, 1),
            'pnl': round(float(pnl or 0), 2)
        })
    return items


def _consulta_dimension(select_sql, filtros, extra_params=()):
    """Ejecuta una consulta GROUP BY de una dimensión y devuelve los items."""
    conn = common.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT {select_sql},
                   COUNT(*) as total,
                   SUM(CASE WHEN resultado LIKE '%TP%' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultado LIKE '%SL%' THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                       ELSE precio_entrada - precio_salida END) as pnl
            FROM historial_operaciones
            WHERE {filtros[0]}
            GROUP BY 1
        """, list(filtros[1]) + list(extra_params))
        return _procesar_filas(cursor.fetchall())
    except Exception as e:
        print(f"❌ Error consultando reporte: {e}")
        return []
    finally:
        conn.close()


def analizar_por_hora(filtros, zona=None):
    """Frecuencias por hora de apertura (0-23), en la zona horaria del usuario."""
    filas = _fetch_cerrados(filtros)
    total = [0] * 24
    wins = [0] * 24
    losses = [0] * 24
    pnl = [0.0] * 24

    for fecha_apertura, resultado, tipo, pe, ps in filas:
        if fecha_apertura is None:
            continue
        h = _convertir(fecha_apertura, zona).hour
        total[h] += 1
        if resultado and 'TP' in resultado:
            wins[h] += 1
        if resultado and 'SL' in resultado:
            losses[h] += 1
        if ps is not None and pe is not None:
            pnl[h] += (ps - pe) if tipo == 'LONG' else (pe - ps)

    horas = []
    for h in range(24):
        wr = round((wins[h] / (wins[h] + losses[h]) * 100), 1) if (wins[h] + losses[h]) > 0 else 0.0
        horas.append({
            'clave': f"{h:02d}:00",
            'total': total[h],
            'wins': wins[h],
            'losses': losses[h],
            'win_rate': wr,
            'pnl': round(pnl[h], 2)
        })
    return horas


def analizar_por_dia(filtros, zona=None):
    """Frecuencias por día de la semana (Lun-Dom), en la zona horaria del usuario."""
    filas = _fetch_cerrados(filtros)
    items = []
    for idx, nombre in enumerate(DIAS_SEMANA):
        items.append({'clave': nombre, 'total': 0, 'wins': 0,
                      'losses': 0, 'win_rate': 0.0, 'pnl': 0.0})

    for fecha_apertura, resultado, tipo, pe, ps in filas:
        if fecha_apertura is None:
            continue
        idx = _convertir(fecha_apertura, zona).weekday()
        item = items[idx]
        item['total'] += 1
        if resultado and 'TP' in resultado:
            item['wins'] += 1
        if resultado and 'SL' in resultado:
            item['losses'] += 1
        if ps is not None and pe is not None:
            item['pnl'] += (ps - pe) if tipo == 'LONG' else (pe - ps)

    for item in items:
        if (item['wins'] + item['losses']) > 0:
            item['win_rate'] = round(item['wins'] / (item['wins'] + item['losses']) * 100, 1)
        item['pnl'] = round(item['pnl'], 2)
    return items


def analizar_por_estrategia(filtros):
    return _consulta_dimension("estrategia", filtros)


def analizar_por_par(filtros):
    return _consulta_dimension("simbolo", filtros)


def analizar_por_mercado(filtros):
    return _consulta_dimension(_mercado_sql(), filtros)


def analizar_por_tipo(filtros):
    items = _consulta_dimension("tipo", filtros)
    return [i for i in items if i['clave'] in ('LONG', 'SHORT')]


def _top(items, minimo=MIN_TRADES_TOP):
    """Mejor elemento por win_rate (con mínimo de trades, desempate por pnl)."""
    candidatos = [i for i in items if i['total'] >= minimo]
    if not candidatos:
        candidatos = [i for i in items if i['total'] > 0]
    if not candidatos:
        return None
    return max(candidatos, key=lambda i: (i['win_rate'], i['pnl']))


def analizar_rachas(filtros):
    """Mejor racha y racha actual de TP consecutivos (sobre trades cerrados)."""
    conn = common.get_db_connection()
    if not conn:
        return {'mejor': 0, 'actual': 0}
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT resultado FROM historial_operaciones
            WHERE {filtros[0]}
            ORDER BY fecha_cierre ASC, id ASC
        """, filtros[1])
        resultados = [str(r[0]) for r in cursor.fetchall()]

        mejor = 0
        racha = 0
        for res in resultados:
            if 'TP' in res:
                racha += 1
                mejor = max(mejor, racha)
            else:
                racha = 0
        return {'mejor': mejor, 'actual': racha}
    except Exception as e:
        print(f"❌ Error consultando rachas: {e}")
        return {'mejor': 0, 'actual': 0}
    finally:
        conn.close()


def analizar_resumen(filtros, zona=None):
    """Resumen global: win rate, pnl, mejores por dimensión y rachas."""
    por_hora = analizar_por_hora(filtros, zona)
    por_dia = analizar_por_dia(filtros, zona)
    por_estrategia = analizar_por_estrategia(filtros)
    por_par = analizar_por_par(filtros)
    por_mercado = analizar_por_mercado(filtros)
    por_tipo = analizar_por_tipo(filtros)

    conn = common.get_db_connection()
    if not conn:
        return {}
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN resultado LIKE '%TP%' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN resultado LIKE '%SL%' THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                       ELSE precio_entrada - precio_salida END) as pnl
            FROM historial_operaciones
            WHERE {filtros[0]}
        """, filtros[1])
        total, wins, losses, pnl = cursor.fetchone()
        total = int(total or 0)
        wins = int(wins or 0)
        losses = int(losses or 0)
    except Exception as e:
        print(f"❌ Error consultando resumen: {e}")
        total = wins = losses = 0
        pnl = 0
    finally:
        conn.close()

    rachas = analizar_rachas(filtros)

    def _mejor(items, campo):
        top = _top(items)
        return top[campo] if top else '-'

    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'win_rate': round((wins / (wins + losses) * 100) if (wins + losses) > 0 else 0, 1),
        'pnl': round(float(pnl or 0), 2),
        'mejor_hora': _mejor(por_hora, 'clave'),
        'mejor_dia': _mejor(por_dia, 'clave'),
        'mejor_estrategia': _mejor(por_estrategia, 'clave'),
        'mejor_par': _mejor(por_par, 'clave'),
        'mejor_mercado': _mejor(por_mercado, 'clave'),
        'mejor_tipo': _mejor(por_tipo, 'clave'),
        'racha_mejor': rachas['mejor'],
        'racha_actual': rachas['actual']
    }


def generar_reporte(estrategia=None, simbolo=None, mercado=None, tipo=None,
                    desde=None, hasta=None, zona=None):
    """Genera el reporte completo de frecuencias de ganancias."""
    filtros = _construir_filtros(estrategia, simbolo, mercado, tipo, desde, hasta)
    return {
        'por_hora': analizar_por_hora(filtros, zona),
        'por_dia': analizar_por_dia(filtros, zona),
        'por_estrategia': analizar_por_estrategia(filtros),
        'por_par': analizar_por_par(filtros),
        'por_mercado': analizar_por_mercado(filtros),
        'por_tipo': analizar_por_tipo(filtros),
        'resumen': analizar_resumen(filtros, zona),
        'zona': zona or ZONA_POR_DEFECTO
    }
