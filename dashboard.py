"""
Dashboard Web - Historial de Operaciones y Estadísticas
Flask app para visualizar trades, métricas y gráficos de velas.
"""

import os
import sys
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, render_template, request

# Agregar directorio actual al path para importar common
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

app = Flask(__name__)

# Archivo de estado de notificaciones
NOTIFY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.notifications_off')

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
# RUTAS PRINCIPALES
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')


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

        # Estadísticas generales
        cursor.execute(f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resultado LIKE '%%TP%%' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN resultado LIKE '%%SL%%' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN resultado LIKE '%%TP%%'
                    THEN CASE
                        WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                        ELSE precio_entrada - precio_salida
                    END
                    ELSE 0
                END) as ganancia_total,
                SUM(CASE WHEN resultado LIKE '%%SL%%'
                    THEN CASE
                        WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                        ELSE precio_entrada - precio_salida
                    END
                    ELSE 0
                END) as perdida_total,
                MAX(CASE
                    WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                    ELSE precio_entrada - precio_salida
                END) as mejor_trade,
                MIN(CASE
                    WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                    ELSE precio_entrada - precio_salida
                END) as peor_trade
            FROM historial_operaciones
            WHERE {where_sql}
        """, params)
        row = cursor.fetchone()

        total = row[0] or 0
        wins = row[1] or 0
        losses = row[2] or 0
        ganancia_total = float(row[3] or 0)
        perdida_total = float(row[4] or 0)
        mejor_trade = float(row[5] or 0)
        peor_trade = float(row[6] or 0)

        win_rate = (wins / total * 100) if total > 0 else 0
        profit_factor = (ganancia_total / abs(perdida_total)) if perdida_total != 0 else 0

        # Estadísticas por estrategia
        cursor.execute(f"""
            SELECT
                estrategia,
                COUNT(*) as total,
                SUM(CASE WHEN resultado LIKE '%%TP%%' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN tipo = 'LONG' THEN precio_salida - precio_entrada
                    ELSE precio_entrada - precio_salida END) as pnl
            FROM historial_operaciones
            WHERE {where_sql}
            GROUP BY estrategia
            ORDER BY estrategia
        """, params)
        por_estrategia = []
        for r in cursor.fetchall():
            estrat_total = r[1] or 0
            estrat_wins = r[2] or 0
            por_estrategia.append({
                'estrategia': r[0],
                'total': estrat_total,
                'wins': estrat_wins,
                'win_rate': round((estrat_wins / estrat_total * 100) if estrat_total > 0 else 0, 1),
                'pnl': round(float(r[3] or 0), 2)
            })

        return jsonify({
            'total': total,
            'wins': wins,
            'losses': losses,
            'win_rate': round(win_rate, 1),
            'ganancia_total': round(ganancia_total, 2),
            'perdida_total': round(perdida_total, 2),
            'profit_factor': round(profit_factor, 2),
            'mejor_trade': round(mejor_trade, 2),
            'peor_trade': round(peor_trade, 2),
            'por_estrategia': por_estrategia
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


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

        cursor.execute(f"""
            SELECT id, estrategia, simbolo, tipo, fecha_apertura, precio_entrada,
                   sl, tp, rango_alto, rango_bajo, fecha_cierre, precio_salida, resultado
            FROM historial_operaciones
            WHERE {where_sql}
            ORDER BY fecha_apertura DESC
            LIMIT 200
        """, params)

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

        return jsonify(trades)

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
        simbolos = [r[0] for r in cursor.fetchall()]

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
    limit = request.args.get('limit', 30, type=int)
    saldos = common.obtener_saldos(limit=limit)
    return jsonify(saldos)


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
# MAIN
# ==========================================
if __name__ == '__main__':
    print("📊 Dashboard Bot Cripto - http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
