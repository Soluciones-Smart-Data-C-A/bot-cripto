"""
Dashboard Web - Historial de Operaciones y Estadísticas
Flask app para visualizar trades, métricas y gráficos de velas.
"""

import os
import sys
import time
import threading
import hashlib
import hmac
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, render_template, request, session, redirect, Response

# Agregar directorio actual al path para importar common
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'cambia-esta-clave-por-seguridad')

# Usuario del bot para el widget de "Login con Telegram" (sin @)
TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '').lstrip('@')

# Estrategias y activos disponibles (para configuración de notificaciones)
ESTRATEGIAS_DISPONIBLES = ['MORA_EMA_CROSS', 'CRT_V7', 'SMC_FVG_BOS', 'NY_OPEN']
ACTIVOS_DISPONIBLES = common.ACTIVOS

# Archivo de estado de notificaciones
NOTIFY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.notifications_off')

# ==========================================
# SSE BROADCASTER (notificaciones en tiempo real)
# ==========================================
import json
from queue import Queue

SSE_CLIENTS = []
SSE_LOCK = threading.Lock()
LAST_EVENT_ID = 0
LAST_EVENT_CIERRE = None

# Mapeo de símbolos del bot a Binance
SYMBOL_MAP = {
    'BTC-USD': 'BTCUSDT',
    'SOL-USD': 'SOLUSDT',
}

BINANCE_INTERVALS = {
    '1m': '1m',
    '5m': '5m',
    '15m': '15m',
    '1h': '1h',
}


# ==========================================
# HELPERS DE PRECIO Y CIERRE
# ==========================================
def obtener_precio_actual(simbolo):
    """Precio real del símbolo: Binance spot (si está mapeado) con fallback a yfinance."""
    precio = None
    try:
        simbolo_binance = SYMBOL_MAP.get(simbolo)
        if simbolo_binance:
            resp = requests.get('https://api.binance.com/api/v3/ticker/price',
                                params={'symbol': simbolo_binance}, timeout=10)
            if resp.status_code == 200:
                precio = float(resp.json()['price'])
    except Exception as e_binance:
        print(f"⚠️ Binance price falló para {simbolo}: {e_binance}")

    if precio is None:
        try:
            import yfinance as yf
            df = yf.download(simbolo, period='1d', interval='1m', progress=False, auto_adjust=True)
            if not df.empty:
                if hasattr(df.columns, 'get_level_values'):
                    df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()
                precio = float(df['Close'].iloc[-1])
        except Exception as e_yf:
            print(f"⚠️ yfinance price falló para {simbolo}: {e_yf}")

    return precio


def resultado_sl_tp(tipo, precio, sl, tp):
    """Determina si el precio tocó SL o TP. Devuelve 'TP', 'SL' o None."""
    sl = float(sl) if sl else None
    tp = float(tp) if tp else None
    if tipo == 'LONG':
        if sl and precio <= sl:
            return 'SL'
        if tp and precio >= tp:
            return 'TP'
    else:  # SHORT
        if sl and precio >= sl:
            return 'SL'
        if tp and precio <= tp:
            return 'TP'
    return None


# ==========================================
# RUTAS PRINCIPALES
# ==========================================
def usuario_actual():
    """Usuario autenticado vía Telegram (session['chat_id']) o None."""
    chat_id = session.get('chat_id')
    if not chat_id:
        return None
    return common.obtener_usuario(chat_id)

@app.route('/')
def index():
    return render_template('index.html', bot_username=TELEGRAM_BOT_USERNAME)


@app.route('/estrategias')
def estrategias():
    return render_template('estrategias.html')


@app.route('/reportes')
def reportes():
    return render_template('reportes.html')


# ==========================================
# LOGIN CON TELEGRAM (WIDGET OFICIAL)
# ==========================================
@app.route('/auth/tg')
def auth_tg():
    params = {}
    for k in ('id', 'username', 'first_name', 'last_name', 'photo_url', 'auth_date', 'hash'):
        if k in request.args:
            params[k] = request.args[k]

    received_hash = params.pop('hash', '')
    if not received_hash or 'id' not in params:
        return redirect('/')

    # Verificación de firma: HMAC-SHA256 con secret = SHA256(bot token)
    data_check_string = '\n'.join(f'{k}={params[k]}' for k in sorted(params))
    secret_key = hashlib.sha256(common.TELEGRAM_TOKEN.encode()).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return redirect('/')

    # Antirreplay: auth_date no puede ser viejo
    try:
        auth_date = int(params['auth_date'])
    except (TypeError, ValueError):
        auth_date = 0
    if abs(auth_date - time.time()) > 86400:
        return redirect('/')

    # Auto-registro del usuario como suscriptor
    common.registrar_usuario(params['id'], params.get('username'), params.get('first_name'), params.get('photo_url'))
    session['chat_id'] = str(params['id'])
    return redirect('/')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')


@app.route('/api/me')
def api_me():
    user = usuario_actual()
    if not user:
        return jsonify({'error': 'no autenticado'}), 401
    return jsonify(user)


@app.route('/api/perfil')
def api_perfil():
    user = usuario_actual()
    if not user:
        return jsonify({'error': 'no autenticado'}), 401

    meta = common.obtener_meta_diaria(user['chat_id'])
    return jsonify({
        'chat_id': user['chat_id'],
        'username': user['username'],
        'first_name': user['first_name'],
        'photo_url': user['photo_url'],
        'saldo': meta['balance'] if meta else None,
        'meta_diaria': meta['meta_diaria'] if meta else None,
        'perdida_trade': meta['perdida_trade'] if meta else None,
        'ganancia_trade': meta['ganancia_trade'] if meta else None,
        'fecha_saldo': None
    })


def registrar_cliente_sse():
    q = Queue()
    with SSE_LOCK:
        SSE_CLIENTS.append(q)
    return q


def desregistrar_cliente_sse(q):
    with SSE_LOCK:
        if q in SSE_CLIENTS:
            SSE_CLIENTS.remove(q)


def broadcast_senal(evento):
    with SSE_LOCK:
        for q in SSE_CLIENTS:
            try:
                q.put_nowait(evento)
            except Exception:
                pass


# ==========================================
# API: ESTADÍSTICAS
# ==========================================
@app.route('/api/stats')
def api_stats():
    estrategia = request.args.get('estrategia')
    simbolo = request.args.get('simbolo')
    desde = request.args.get('desde')
    hasta = request.args.get('hasta')

    conn = common.get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión a BD'}), 500

    try:
        cursor = conn.cursor()

        # Filtros dinámicos
        where_clauses = ["resultado != 'ABIERTA'"]
        params = []

        if estrategia:
            where_clauses.append("estrategia = %s")
            params.append(estrategia)
        if simbolo:
            where_clauses.append("simbolo = %s")
            params.append(simbolo)
        if desde:
            where_clauses.append("fecha_apertura >= %s")
            params.append(desde)
        if hasta:
            where_clauses.append("fecha_apertura <= %s")
            params.append(hasta + " 23:59:59")

        where_sql = " AND ".join(where_clauses)

        # Estadísticas generales (cerrados)
        cursor.execute(f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resultado LIKE '%%TP%%' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN resultado LIKE '%%SL%%' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN resultado = 'ABIERTA' THEN 1 ELSE 0 END) as abiertas
            FROM historial_operaciones
            WHERE {where_sql}
        """, params)
        row = cursor.fetchone()

        total = row[0] or 0
        wins = row[1] or 0
        losses = row[2] or 0
        abiertas = row[3] or 0

        exitosas_globales = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        # Exitosas del día (señales abiertas hoy, con mismos filtros)
        hoy = datetime.now().strftime('%Y-%m-%d')
        cursor.execute(f"""
            SELECT
                SUM(CASE WHEN resultado LIKE '%%TP%%' THEN 1 ELSE 0 END) as wins_hoy,
                SUM(CASE WHEN resultado LIKE '%%SL%%' THEN 1 ELSE 0 END) as losses_hoy
            FROM historial_operaciones
            WHERE {where_sql} AND fecha_apertura >= %s
        """, params + [hoy])
        row_dia = cursor.fetchone()
        wins_hoy = row_dia[0] or 0
        losses_hoy = row_dia[1] or 0
        exitosas_dia = (wins_hoy / (wins_hoy + losses_hoy) * 100) if (wins_hoy + losses_hoy) > 0 else 0

        # Mejor par + estrategia (por ganancia total, con mismos filtros)
        cursor.execute(f"""
            SELECT estrategia, simbolo,
                   SUM(CASE WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                       ELSE precio_entrada - precio_salida END) as ganancia_total
            FROM historial_operaciones
            WHERE {where_sql} AND resultado LIKE '%%TP%%'
            GROUP BY estrategia, simbolo
            ORDER BY ganancia_total DESC
            LIMIT 1
        """, params)
        mejor_row = cursor.fetchone()
        mejor_estrategia = mejor_row[0] if mejor_row else '-'
        mejor_activo = mejor_row[1] if mejor_row else '-'
        mejor_ganancia = round(float(mejor_row[2] or 0), 2) if mejor_row else 0

        # Estadísticas por estrategia (sin filtro de resultado para contar abiertas)
        where_clauses_estrat = []
        params_estrat = []
        if estrategia:
            where_clauses_estrat.append("estrategia = %s")
            params_estrat.append(estrategia)
        if simbolo:
            where_clauses_estrat.append("simbolo = %s")
            params_estrat.append(simbolo)
        if desde:
            where_clauses_estrat.append("fecha_apertura >= %s")
            params_estrat.append(desde)
        if hasta:
            where_clauses_estrat.append("fecha_apertura <= %s")
            params_estrat.append(hasta + " 23:59:59")
        where_sql_estrat = " AND ".join(where_clauses_estrat) if where_clauses_estrat else "1=1"

        cursor.execute(f"""
            SELECT
                estrategia,
                COUNT(*) as total,
                SUM(CASE WHEN resultado LIKE '%%TP%%' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                    ELSE precio_entrada - precio_salida END) as pnl,
                SUM(CASE WHEN resultado = 'ABIERTA' THEN 1 ELSE 0 END) as abiertas
            FROM historial_operaciones
            WHERE {where_sql_estrat}
            GROUP BY estrategia
            ORDER BY estrategia
        """, params_estrat)
        por_estrategia = []
        for r in cursor.fetchall():
            estrat_total = r[1] or 0
            estrat_wins = r[2] or 0
            por_estrategia.append({
                'estrategia': r[0],
                'total': estrat_total,
                'wins': estrat_wins,
                'win_rate': round((estrat_wins / estrat_total * 100) if estrat_total > 0 else 0, 1),
                'pnl': round(float(r[3] or 0), 2),
                'abiertas': int(r[4] or 0)
            })

        return jsonify({
            'total': total,
            'wins': wins,
            'losses': losses,
            'abiertas': abiertas,
            'exitosas_globales': round(exitosas_globales, 1),
            'exitosas_dia': round(exitosas_dia, 1),
            'mejor_estrategia': mejor_estrategia,
            'mejor_activo': mejor_activo,
            'mejor_ganancia': mejor_ganancia,
            'por_estrategia': por_estrategia
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ==========================================
# API: REPORTES DE FRECUENCIAS
# ==========================================
@app.route('/api/reportes')
def api_reportes():
    import reportes
    try:
        reporte = reportes.generar_reporte(
            estrategia=request.args.get('estrategia'),
            simbolo=request.args.get('simbolo'),
            mercado=request.args.get('mercado'),
            tipo=request.args.get('tipo'),
            desde=request.args.get('desde'),
            hasta=request.args.get('hasta'),
            zona=request.args.get('zona')
        )
        return jsonify(reporte)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==========================================
# API: HISTORIAL DE TRADES
# ==========================================
@app.route('/api/trades')
def api_trades():
    estrategia = request.args.get('estrategia')
    simbolo = request.args.get('simbolo')
    desde = request.args.get('desde')
    hasta = request.args.get('hasta')
    resultado = request.args.get('resultado')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    conn = common.get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión a BD'}), 500

    try:
        cursor = conn.cursor()
        where_clauses = []
        params = []

        if estrategia:
            where_clauses.append("estrategia = %s")
            params.append(estrategia)
        if simbolo:
            where_clauses.append("simbolo = %s")
            params.append(simbolo)
        if desde:
            where_clauses.append("fecha_apertura >= %s")
            params.append(desde)
        if hasta:
            where_clauses.append("fecha_apertura <= %s")
            params.append(hasta + " 23:59:59")
        if resultado:
            if resultado == 'TP':
                where_clauses.append("resultado LIKE '%%TP%%'")
            elif resultado == 'SL':
                where_clauses.append("resultado LIKE '%%SL%%'")
            elif resultado == 'ABIERTA':
                where_clauses.append("resultado = 'ABIERTA'")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        cursor.execute(f"SELECT COUNT(*) FROM historial_operaciones WHERE {where_sql}", params)
        total_count = cursor.fetchone()[0]

        offset = (page - 1) * per_page
        cursor.execute(f"""
            SELECT id, estrategia, simbolo, tipo, fecha_apertura, precio_entrada,
                   sl, tp, rango_alto, rango_bajo, fecha_cierre, precio_salida, resultado
            FROM historial_operaciones
            WHERE {where_sql}
            ORDER BY fecha_apertura DESC
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])

        trades = []
        for r in cursor.fetchall():
            trades.append({
                'id': r[0],
                'estrategia': r[1],
                'simbolo': r[2],
                'tipo': r[3],
                'fecha_apertura': r[4].strftime('%Y-%m-%d %H:%M') if r[4] else None,
                'precio_entrada': float(r[5]) if r[5] else None,
                'sl': float(r[6]) if r[6] else None,
                'tp': float(r[7]) if r[7] else None,
                'rango_alto': float(r[8]) if r[8] else None,
                'rango_bajo': float(r[9]) if r[9] else None,
                'fecha_cierre': r[10].strftime('%Y-%m-%d %H:%M') if r[10] else None,
                'precio_salida': float(r[11]) if r[11] else None,
                'resultado': r[12]
            })

        return jsonify({
            'trades': trades,
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'total_pages': (total_count + per_page - 1) // per_page
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/trades/count')
def api_trades_count():
    estrategia = request.args.get('estrategia')
    simbolo = request.args.get('simbolo')
    desde = request.args.get('desde')
    hasta = request.args.get('hasta')
    resultado = request.args.get('resultado')

    conn = common.get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión a BD'}), 500

    try:
        cursor = conn.cursor()
        where_clauses = []
        params = []

        if estrategia:
            where_clauses.append("estrategia = %s")
            params.append(estrategia)
        if simbolo:
            where_clauses.append("simbolo = %s")
            params.append(simbolo)
        if desde:
            where_clauses.append("fecha_apertura >= %s")
            params.append(desde)
        if hasta:
            where_clauses.append("fecha_apertura <= %s")
            params.append(hasta + " 23:59:59")
        if resultado:
            if resultado == 'TP':
                where_clauses.append("resultado LIKE '%%TP%%'")
            elif resultado == 'SL':
                where_clauses.append("resultado LIKE '%%SL%%'")
            elif resultado == 'ABIERTA':
                where_clauses.append("resultado = 'ABIERTA'")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        cursor.execute(f"SELECT COUNT(*) FROM historial_operaciones WHERE {where_sql}", params)
        count = cursor.fetchone()[0]
        return jsonify({'count': count})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ==========================================
# API: DETALLE DE TRADE + OHLC
# ==========================================
@app.route('/api/trade/<int:trade_id>')
def api_trade_detail(trade_id):
    conn = common.get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión a BD'}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, estrategia, simbolo, tipo, fecha_apertura, precio_entrada,
                   sl, tp, rango_alto, rango_bajo, fecha_cierre, precio_salida, resultado
            FROM historial_operaciones
            WHERE id = %s
        """, (trade_id,))
        r = cursor.fetchone()

        if not r:
            return jsonify({'error': 'Trade no encontrado'}), 404

        trade = {
            'id': r[0],
            'estrategia': r[1],
            'simbolo': r[2],
            'tipo': r[3],
            'fecha_apertura': r[4].strftime('%Y-%m-%d %H:%M') if r[4] else None,
            'precio_entrada': float(r[5]) if r[5] else None,
            'sl': float(r[6]) if r[6] else None,
            'tp': float(r[7]) if r[7] else None,
            'rango_alto': float(r[8]) if r[8] else None,
            'rango_bajo': float(r[9]) if r[9] else None,
            'fecha_cierre': r[10].strftime('%Y-%m-%d %H:%M') if r[10] else None,
            'precio_salida': float(r[11]) if r[11] else None,
            'resultado': r[12]
        }

        # Descargar OHLC desde Binance API (fallback: yfinance)
        ohlc = []
        if r[4] and r[2]:
            try:
                simbolo_bot = r[2]
                simbolo_binance = SYMBOL_MAP.get(simbolo_bot)
                fecha_apertura = r[4]
                fecha_cierre = r[10]

                if fecha_cierre:
                    duracion = (fecha_cierre - fecha_apertura).total_seconds() / 3600
                else:
                    duracion = min((datetime.now() - fecha_apertura).total_seconds() / 3600, 24)

                if duracion <= 6:
                    interval = '5m'
                elif duracion <= 48:
                    interval = '15m'
                else:
                    interval = '1h'

                # Intentar Binance primero
                if simbolo_binance:
                    try:
                        start_ms = int((fecha_apertura - timedelta(minutes=30)).timestamp() * 1000)
                        end_ms = int(((fecha_cierre + timedelta(minutes=30)) if fecha_cierre else datetime.now()).timestamp() * 1000)

                        resp = requests.get(
                            'https://api.binance.com/api/v3/klines',
                            params={
                                'symbol': simbolo_binance,
                                'interval': interval,
                                'startTime': start_ms,
                                'endTime': end_ms,
                                'limit': 1000
                            },
                            timeout=10
                        )

                        if resp.status_code == 200:
                            candles = resp.json()
                            for c in candles:
                                ohlc.append({
                                    'time': int(c[0] / 1000),
                                    'open': round(float(c[1]), 5),
                                    'high': round(float(c[2]), 5),
                                    'low': round(float(c[3]), 5),
                                    'close': round(float(c[4]), 5)
                                })
                            print(f"📊 OHLC Binance: {len(ohlc)} velas para {simbolo_binance}")
                        else:
                            print(f"⚠️ Binance respondió {resp.status_code}")
                    except Exception as e_binance:
                        print(f"⚠️ Binance falló: {e_binance}")

                # Fallback a yfinance si Binance no funcionó
                if not ohlc:
                    try:
                        import yfinance as yf
                        period = '5d' if duracion <= 24 else '30d'
                        df = yf.download(simbolo_bot, period=period, interval=interval, progress=False)
                        if not df.empty:
                            if hasattr(df.columns, 'get_level_values'):
                                df.columns = df.columns.get_level_values(0)
                            fecha_ini = fecha_apertura - timedelta(minutes=30)
                            fecha_fin = (fecha_cierre + timedelta(minutes=30)) if fecha_cierre else datetime.now()
                            for idx, row in df.iterrows():
                                vela_time = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
                                if hasattr(vela_time, 'tzinfo') and vela_time.tzinfo is not None:
                                    vela_time = vela_time.replace(tzinfo=None)
                                if fecha_ini <= vela_time <= fecha_fin:
                                    ohlc.append({
                                        'time': int(idx.timestamp()),
                                        'open': round(float(row['Open']), 5),
                                        'high': round(float(row['High']), 5),
                                        'low': round(float(row['Low']), 5),
                                        'close': round(float(row['Close']), 5)
                                    })
                            print(f"📊 OHLC yfinance (fallback): {len(ohlc)} velas")
                    except Exception as e_yf:
                        print(f"⚠️ yfinance fallback falló: {e_yf}")
            except Exception as e:
                print(f"⚠️ Error descargando OHLC para {r[2]}: {e}")

        trade['ohlc'] = ohlc
        return jsonify(trade)

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ==========================================
# API: CERRAR TRADE MANUALMENTE
# ==========================================
@app.route('/api/trade/<int:trade_id>/close', methods=['POST'])
def api_close_trade(trade_id):
    conn = common.get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión a BD'}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, estrategia, simbolo, tipo, precio_entrada, sl, tp, resultado
            FROM historial_operaciones
            WHERE id = %s
        """, (trade_id,))
        r = cursor.fetchone()

        if not r:
            return jsonify({'error': 'Trade no encontrado'}), 404

        trade_id, estrategia, simbolo, tipo, entrada, sl, tp, resultado = r
        if resultado != 'ABIERTA':
            return jsonify({'error': 'La señal ya está cerrada'}), 400

        precio_actual = obtener_precio_actual(simbolo)
        if precio_actual is None:
            precio_actual = float(entrada)

        nuevo_resultado = resultado_sl_tp(tipo, precio_actual, sl, tp) or 'CERRADO_MANUAL'

        common.registrar_cierre(trade_id, precio_actual, nuevo_resultado)

        return jsonify({
            'ok': True,
            'id': trade_id,
            'precio_salida': precio_actual,
            'resultado': nuevo_resultado
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ==========================================
# API: TRADES ABIERTOS (Carrusel)
# ==========================================
@app.route('/api/open-trades')
def api_open_trades():
    conn = common.get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión a BD'}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, estrategia, simbolo, tipo, precio_entrada, sl, tp, fecha_apertura
            FROM historial_operaciones
            WHERE resultado = 'ABIERTA'
            ORDER BY fecha_apertura DESC
        """)
        trades = []
        for r in cursor.fetchall():
            trade = {
                'id': r[0],
                'estrategia': r[1],
                'simbolo': r[2],
                'tipo': r[3],
                'precio_entrada': float(r[4]) if r[4] else 0,
                'sl': float(r[5]) if r[5] else 0,
                'tp': float(r[6]) if r[6] else 0,
                'fecha_apertura': r[7].strftime('%Y-%m-%d %H:%M') if r[7] else None,
            }

            # Obtener precio actual (Binance spot, fallback yfinance)
            precio_actual = obtener_precio_actual(trade['simbolo'])
            if precio_actual is None:
                precio_actual = trade['precio_entrada']

            trade['precio_actual'] = round(precio_actual, 5)

            # Calcular dirección (green/red)
            sl = trade['sl']
            tp = trade['tp']
            entrada = trade['precio_entrada']
            tipo = trade['tipo']

            if tipo == 'LONG':
                rango_total = tp - sl
                if rango_total > 0:
                    position = (precio_actual - sl) / rango_total
                else:
                    position = 0.5
            else:  # SHORT
                rango_total = sl - tp
                if rango_total > 0:
                    position = (sl - precio_actual) / rango_total
                else:
                    position = 0.5

            # PnL %
            if tipo == 'LONG' and entrada > 0:
                trade['pnl_pct'] = round((precio_actual - entrada) / entrada * 100, 2)
            elif tipo == 'SHORT' and entrada > 0:
                trade['pnl_pct'] = round((entrada - precio_actual) / entrada * 100, 2)
            else:
                trade['pnl_pct'] = 0

            # Dirección del borde: rojo si pierde, naranja entre 0.01 y 0.5, verde > 0.5
            if trade['pnl_pct'] < 0:
                trade['direccion'] = 'red'
            elif position <= 0.5:
                trade['direccion'] = 'orange'
            else:
                trade['direccion'] = 'green'

            trades.append(trade)

        return jsonify({'trades': trades, 'total': len(trades)})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ==========================================
# API: FILTROS
# ==========================================
@app.route('/api/filters')
def api_filters():
    conn = common.get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión a BD'}), 500

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT DISTINCT estrategia FROM historial_operaciones ORDER BY estrategia")
        estrategias = [r[0] for r in cursor.fetchall()]

        cursor.execute("SELECT DISTINCT simbolo FROM historial_operaciones ORDER BY simbolo")
        simbolos_db = [r[0] for r in cursor.fetchall()]

        simbolos = list(dict.fromkeys(simbolos_db + common.ACTIVOS))

        return jsonify({
            'estrategias': estrategias,
            'simbolos': simbolos
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ==========================================
# API: SALDOS
# ==========================================
@app.route('/api/saldos')
def api_saldos():
    user = usuario_actual()
    if not user:
        return jsonify({'error': 'no autenticado'}), 401
    limit = request.args.get('limit', 30, type=int)
    saldos = common.obtener_saldos(chat_id=user['chat_id'], limit=limit)
    return jsonify(saldos)


# ==========================================
# API: PREFERENCIAS DE NOTIFICACIÓN POR USUARIO
# ==========================================
@app.route('/api/preferencias')
def api_preferencias_get():
    user = usuario_actual()
    if not user:
        return jsonify({'error': 'no autenticado'}), 401
    prefs = common.obtener_preferencias(user['chat_id'])
    prefs['meta_pct'] = common.obtener_meta_pct(user['chat_id'])
    return jsonify(prefs)

@app.route('/api/preferencias', methods=['POST'])
def api_preferencias_post():
    user = usuario_actual()
    if not user:
        return jsonify({'error': 'no autenticado'}), 401
    data = request.get_json(silent=True) or {}
    ok = common.guardar_preferencias(user['chat_id'], data.get('estrategias'), data.get('simbolos'))
    if not ok:
        return jsonify({'error': 'Error guardando preferencias'}), 500

    if 'meta_pct' in data:
        try:
            pct = float(data['meta_pct'])
        except (TypeError, ValueError):
            pct = None
        if pct is None or not (0.5 <= pct <= 20):
            return jsonify({'error': 'meta_pct debe estar entre 0.5 y 20'}), 400
        if common.guardar_meta_pct(user['chat_id'], pct):
            common.recalcular_ultimo_saldo(user['chat_id'])

    prefs = common.obtener_preferencias(user['chat_id'])
    prefs['meta_pct'] = common.obtener_meta_pct(user['chat_id'])
    return jsonify(prefs)


@app.route('/api/opciones')
def api_opciones():
    return jsonify({
        'estrategias': ESTRATEGIAS_DISPONIBLES,
        'activos': ACTIVOS_DISPONIBLES
    })


# ==========================================
# API: NOTIFICACIONES
# ==========================================
@app.route('/api/notifications/status')
def api_notifications_status():
    enabled = not os.path.exists(NOTIFY_FILE)
    return jsonify({'enabled': enabled})

@app.route('/api/notifications/toggle', methods=['POST'])
def api_notifications_toggle():
    if os.path.exists(NOTIFY_FILE):
        os.remove(NOTIFY_FILE)
        enabled = True
    else:
        with open(NOTIFY_FILE, 'w') as f:
            f.write('off')
        enabled = False
    return jsonify({'enabled': enabled})


# ==========================================
# SSE: NOTIFICACIONES EN TIEMPO REAL
# ==========================================
BROADCAST_INTERVAL = 3  # segundos


def iniciar_broadcast():
    global LAST_EVENT_ID, LAST_EVENT_CIERRE
    def loop():
        global LAST_EVENT_ID, LAST_EVENT_CIERRE
        time.sleep(5)
        conn = common.get_db_connection()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT IFNULL(MAX(id),0) FROM historial_operaciones")
            LAST_EVENT_ID = cursor.fetchone()[0]
            cursor.execute("SELECT IFNULL(MAX(fecha_cierre),'1970-01-01') FROM historial_operaciones WHERE fecha_cierre IS NOT NULL")
            LAST_EVENT_CIERRE = cursor.fetchone()[0]
        except Exception as e:
            print(f"⚠️ [broadcast] Error inicializando watermarks: {e}")
        finally:
            conn.close()

        while True:
            time.sleep(BROADCAST_INTERVAL)
            conn = common.get_db_connection()
            if not conn:
                continue
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, estrategia, simbolo, tipo, resultado, precio_entrada, precio_salida, sl, tp
                    FROM historial_operaciones
                    WHERE id > %s
                    ORDER BY id
                """, (LAST_EVENT_ID,))
                rows = cursor.fetchall()

                if rows:
                    max_id = rows[-1][0]
                    for r in rows:
                        tid, estrategia, simbolo, tipo_op, resultado, entrada, salida, sl, tp = r
                        if resultado == 'ABIERTA':
                            tipo = 'apertura'
                        else:
                            tipo = 'cierre'
                        evento = {
                            'tipo': tipo,
                            'id': tid,
                            'estrategia': estrategia,
                            'simbolo': simbolo,
                            'tipo_op': tipo_op,
                            'resultado': resultado,
                            'precio_entrada': float(entrada) if entrada else None,
                            'precio_salida': float(salida) if salida else None,
                            'sl': float(sl) if sl else None,
                            'tp': float(tp) if tp else None
                        }
                        broadcast_senal(evento)
                    LAST_EVENT_ID = max_id

                cursor.execute("""
                    SELECT id, estrategia, simbolo, tipo, resultado, precio_entrada, precio_salida, sl, tp
                    FROM historial_operaciones
                    WHERE fecha_cierre > %s AND id <= %s
                    ORDER BY fecha_cierre
                """, (LAST_EVENT_CIERRE, LAST_EVENT_ID))
                rows_cierre = cursor.fetchall()
                if rows_cierre:
                    cursor.execute("SELECT MAX(fecha_cierre) FROM historial_operaciones WHERE fecha_cierre > %s", (LAST_EVENT_CIERRE,))
                    new_cierre = cursor.fetchone()[0]
                    if new_cierre:
                        LAST_EVENT_CIERRE = new_cierre
                    for r in rows_cierre:
                        tid, estrategia, simbolo, tipo_op, resultado, entrada, salida, sl, tp = r
                        evento = {
                            'tipo': 'cierre',
                            'id': tid,
                            'estrategia': estrategia,
                            'simbolo': simbolo,
                            'tipo_op': tipo_op,
                            'resultado': resultado,
                            'precio_entrada': float(entrada) if entrada else None,
                            'precio_salida': float(salida) if salida else None,
                            'sl': float(sl) if sl else None,
                            'tp': float(tp) if tp else None
                        }
                        broadcast_senal(evento)
            except Exception as e:
                print(f"⚠️ [broadcast] Error en ciclo: {e}")
            finally:
                conn.close()

    t = threading.Thread(target=loop, daemon=True, name='broadcast-sse')
    t.start()


@app.route('/api/events')
def api_events():
    user = usuario_actual()
    if not user:
        return jsonify({'error': 'no autenticado'}), 401

    def generate():
        q = registrar_cliente_sse()
        try:
            while True:
                try:
                    evento = q.get(timeout=15)
                    yield f"event: senal\ndata: {json.dumps(evento)}\n\n"
                except Exception:
                    yield ": ping\n\n"
        finally:
            desregistrar_cliente_sse(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no',
                             'Connection': 'keep-alive'})


# ==========================================
# WATCHDOG: CIERRE AUTOMÁTICO DE SEÑALES
# ==========================================
WATCHDOG_INTERVAL = 30  # segundos

def vigilar_cierres():
    """Cierra automáticamente trades abiertos que hayan tocado SL/TP."""
    conn = common.get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, estrategia, simbolo, tipo, precio_entrada, sl, tp
            FROM historial_operaciones
            WHERE resultado = 'ABIERTA'
        """)
        trades = cursor.fetchall()

        for r in trades:
            trade_id, estrategia, simbolo, tipo, entrada, sl, tp = r
            try:
                # Para acciones/ETF: no cerrar si el mercado está cerrado
                if common.es_accion_o_etf(simbolo) and not common.horario_mercado():
                    continue

                precio_actual = obtener_precio_actual(simbolo)
                if precio_actual is None:
                    continue

                resultado = resultado_sl_tp(tipo, precio_actual, sl, tp)
                if not resultado:
                    continue

                if common.registrar_cierre(trade_id, precio_actual, resultado):
                    print(f"🏁 [watchdog] Cierre automático {simbolo} #{trade_id}: {resultado} @ {precio_actual:.5f}")
                    common.enviar_telegram(estrategia, simbolo,
                        f"🏁 *CIERRE AUTOMÁTICO ({estrategia})*\n"
                        f"Par: {simbolo}\nMotivo: {resultado} {common.icono_cierre(resultado)}\n"
                        f"Precio: {precio_actual:.5f}\nID: {trade_id}")
            except Exception as e:
                print(f"⚠️ [watchdog] Error cerrando {simbolo} #{trade_id}: {e}")
    except Exception as e:
        print(f"⚠️ [watchdog] Error general: {e}")
    finally:
        conn.close()


def iniciar_watchdog():
    def loop():
        time.sleep(10)  # espera inicial
        while True:
            try:
                vigilar_cierres()
            except Exception as e:
                print(f"⚠️ [watchdog] Fallo en ciclo: {e}")
            time.sleep(WATCHDOG_INTERVAL)

    t = threading.Thread(target=loop, daemon=True, name='watchdog-cierres')
    t.start()


# ==========================================
# MAIN
# ==========================================
if __name__ == '__main__':
    print("📊 Dashboard Bot Cripto - http://localhost:5000")
    iniciar_watchdog()
    iniciar_broadcast()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
