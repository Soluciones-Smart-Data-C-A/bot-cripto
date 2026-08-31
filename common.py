"""
Módulo común: conexión a BD, registro de operaciones y mensajería Telegram.
Usado por todas las estrategias del bot (Mora EMA Cross, CRT, NY Open).
"""

import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import requests
import mysql.connector
from mysql.connector import Error

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(path):
        return None

# ==========================================
# GESTIÓN DE ARGUMENTOS Y VARIABLES DE ENTORNO
# ==========================================
def cargar_entorno():
    argumento = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if argumento == 'local':
        load_dotenv('.env_local')
    elif argumento == 'produccion':
        load_dotenv('.env')
    else:
        if os.path.exists('.env_local'):
            load_dotenv('.env_local')
        else:
            load_dotenv('.env')

cargar_entorno()

# ==========================================
# CONFIGURACIÓN
# ==========================================
DB_HOST = os.getenv('DB_HOST', '45.22.208.171')
DB_USER = os.getenv('DB_USER', 'root')
DB_NAME = os.getenv('DB_NAME', 'trades')
DB_PORT = int(os.getenv('DB_PORT', 3306))
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')

# Debug verbose (activar con BOT_DEBUG=1 en Coolify → Env Vars)
DEBUG = os.getenv('BOT_DEBUG', '').strip().lower() in ('1', 'true', 'yes')

def dlog(*args, **kwargs):
    """Imprime solo si BOT_DEBUG=1."""
    if DEBUG:
        print(*args, **kwargs, flush=True)

# Activos monitoreados por todas las estrategias
ACTIVOS = ['BTC-USD', 'SOL-USD', 'HYPE32196-USD', 'IWM', 'SMH', 'VT', 'VALE', 'PYPL', 'INTC',
           'NKE', 'GOOGL', 'CRWV', 'CCJ', 'COST', 'AAPL', 'ITX', 'MRVL', 'COIN', 'META', 'RTX', 'CVX']

# ==========================================
# PRODUCCIÓN vs PRUEBA
# ==========================================
# Estrategias en modo prueba: escriben en historial_pruebas, no en producción.
# Se mantienen corriendo durante un periodo de prueba; luego se decide si paran o pasan a producción.
TEST_ESTRATEGIAS = ('SMC_FVG_BOS', 'INSTA_SWEEP_V1')

TABLA_PRODUCCION = 'historial_operaciones'
TABLA_PRUEBA = 'historial_pruebas'

def es_estrategia_prueba(estrategia):
    return estrategia in TEST_ESTRATEGIAS

def tabla_estrategia(estrategia):
    """Devuelve la tabla donde vive una estrategia según su modo (producción/prueba)."""
    return TABLA_PRUEBA if es_estrategia_prueba(estrategia) else TABLA_PRODUCCION

# Acciones y ETFs (ticker yfinance puro, sin -USD)
ACCIONES_ETF = {'IWM', 'SMH', 'VT', 'VALE', 'PYPL', 'INTC',
                'NKE', 'GOOGL', 'CRWV', 'CCJ', 'COST', 'AAPL', 'ITX', 'MRVL', 'COIN', 'META', 'RTX', 'CVX'}

def es_accion_o_etf(simbolo):
    return simbolo in ACCIONES_ETF

def horario_mercado():
    """Retorna True si el mercado de acciones US está abierto (9:30 AM - 4:00 PM ET)."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    ny_offset = timedelta(hours=-4)  # EDT
    ny_time = now + ny_offset
    weekday = ny_time.weekday()
    if weekday >= 5:  # Sábado o Domingo
        return False
    market_open = ny_time.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = ny_time.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= ny_time <= market_close

def verificar_config():
    """Valida que las credenciales requeridas estén configuradas en el entorno."""
    ok = True
    if not DB_PASSWORD:
        print("❌ ERROR: DB_PASSWORD no configurado en el entorno.")
        ok = False
    if not TELEGRAM_TOKEN:
        print("❌ ERROR: TELEGRAM_TOKEN no configurado en el entorno.")
        ok = False
    return ok

# ==========================================
# BASE DE DATOS
# ==========================================
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            connect_timeout=5
        )
        return conn
    except Error as e:
        print(f"❌ Error DB: {e}")
        return None

def inicializar_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()

        cursor.execute("DROP TABLE IF EXISTS historial_mora_cross")
        cursor.execute("DROP TABLE IF EXISTS historial_mora_ny")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_operaciones (
                id INT AUTO_INCREMENT PRIMARY KEY,
                estrategia VARCHAR(30) NOT NULL,
                simbolo VARCHAR(20) NOT NULL,
                tipo VARCHAR(10),
                fecha_apertura DATETIME,
                precio_entrada FLOAT,
                sl FLOAT,
                tp FLOAT,
                rango_alto FLOAT,
                rango_bajo FLOAT,
                ema_9 FLOAT,
                ema_21 FLOAT,
                fecha_cierre DATETIME NULL,
                precio_salida FLOAT NULL,
                resultado VARCHAR(50) DEFAULT 'ABIERTA'
            )
        """)

        # Tabla de estrategias en modo prueba (mismo esquema que producción)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLA_PRUEBA} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                estrategia VARCHAR(30) NOT NULL,
                simbolo VARCHAR(20) NOT NULL,
                tipo VARCHAR(10),
                fecha_apertura DATETIME,
                precio_entrada FLOAT,
                sl FLOAT,
                tp FLOAT,
                rango_alto FLOAT,
                rango_bajo FLOAT,
                ema_9 FLOAT,
                ema_21 FLOAT,
                fecha_cierre DATETIME NULL,
                precio_salida FLOAT NULL,
                resultado VARCHAR(50) DEFAULT 'ABIERTA',
                UNIQUE KEY uk_prueba (estrategia, simbolo, fecha_apertura, precio_entrada, tipo)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telegram_mensajes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                chat_id VARCHAR(50),
                message_id BIGINT,
                estrategia VARCHAR(30),
                simbolo VARCHAR(20),
                contenido TEXT,
                fecha_envio DATETIME,
                estado VARCHAR(20)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saldos_diarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                chat_id VARCHAR(50) NOT NULL,
                saldo FLOAT NOT NULL,
                meta_diaria FLOAT,
                perdida_trade FLOAT,
                ganancia_trade FLOAT,
                fecha DATETIME DEFAULT NOW()
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                chat_id VARCHAR(50) PRIMARY KEY,
                username VARCHAR(100),
                first_name VARCHAR(100),
                photo_url VARCHAR(500),
                meta_pct FLOAT DEFAULT 5.0,
                fecha_alta DATETIME DEFAULT NOW()
            )
        """)

        try:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN photo_url VARCHAR(500) AFTER first_name")
        except Error:
            pass  # Ya existe

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS preferencias_notificaciones (
                id INT AUTO_INCREMENT PRIMARY KEY,
                chat_id VARCHAR(50) NOT NULL,
                estrategia VARCHAR(30),
                simbolo VARCHAR(20),
                KEY idx_pref (chat_id)
            )
        """)

        # Agregar columnas nuevas si la tabla ya existía sin ellas
        for col, tipo in [('meta_diaria', 'FLOAT'), ('perdida_trade', 'FLOAT'), ('ganancia_trade', 'FLOAT')]:
            try:
                cursor.execute(f"ALTER TABLE saldos_diarios ADD COLUMN {col} {tipo}")
            except Error:
                pass  # La columna ya existe

        try:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN meta_pct FLOAT DEFAULT 5.0")
        except Error:
            pass  # La columna ya existe

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rangos_descartados (
                id INT AUTO_INCREMENT PRIMARY KEY,
                estrategia VARCHAR(30) NOT NULL,
                simbolo VARCHAR(20) NOT NULL,
                rango_alto FLOAT,
                rango_bajo FLOAT,
                fecha DATETIME DEFAULT NOW(),
                UNIQUE KEY idx_rango (estrategia, simbolo)
            )
        """)

        conn.commit()
        migrar_estrategias_a_prueba(conn)
        conn.commit()
    except Error as e:
        print(f"❌ Error inicializando tablas: {e}")
    finally:
        conn.close()

def migrar_estrategias_a_prueba(conn=None):
    """Mueve los registros existentes de las estrategias en prueba desde
    historial_operaciones hacia historial_pruebas. Idempotente (INSERT IGNORE).
    Deja de contar en producción."""
    cerrar_conn = conn is None
    if conn is None:
        conn = get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        placeholders = ", ".join(["%s"] * len(TEST_ESTRATEGIAS))
        # Copiar a la tabla de prueba (IGNORE por la unique key natural)
        cursor.execute(f"""
            INSERT IGNORE INTO {TABLA_PRUEBA}
                (estrategia, simbolo, tipo, fecha_apertura, precio_entrada, sl, tp,
                 rango_alto, rango_bajo, ema_9, ema_21, fecha_cierre, precio_salida, resultado)
            SELECT estrategia, simbolo, tipo, fecha_apertura, precio_entrada, sl, tp,
                   rango_alto, rango_bajo, ema_9, ema_21, fecha_cierre, precio_salida, resultado
            FROM {TABLA_PRODUCCION}
            WHERE estrategia IN ({placeholders})
        """, TEST_ESTRATEGIAS)
        movidos = cursor.rowcount
        # Borrar los ya migrados de producción
        cursor.execute(f"""
            DELETE FROM {TABLA_PRODUCCION}
            WHERE estrategia IN ({placeholders})
        """, TEST_ESTRATEGIAS)
        eliminados = cursor.rowcount
        if movidos or eliminados:
            print(f"🔁 Migración adaptación a prueba: {movidos} copiado(s), {eliminados} removido(s) de producción")
        if cerrar_conn:
            conn.commit()
    except Error as e:
        print(f"❌ Error migrando estrategias a prueba: {e}")
    finally:
        if cerrar_conn:
            conn.close()

def obtener_suscriptores():
    conn = get_db_connection()
    ids = []
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM usuarios")
            ids = [str(row[0]) for row in cursor.fetchall()]
        except Error as e:
            print(f"❌ Error obteniendo suscriptores: {e}")
        finally:
            conn.close()
    return ids

def registrar_usuario(chat_id, username=None, first_name=None, photo_url=None):
    """Registra o actualiza un usuario de Telegram (auto-registro desde el login web)."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO usuarios (chat_id, username, first_name, photo_url)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE username = VALUES(username), first_name = VALUES(first_name), photo_url = VALUES(photo_url)
        """, (str(chat_id), username, first_name, photo_url))
        conn.commit()
        return True
    except Error as e:
        print(f"❌ Error registrando usuario: {e}")
        return None
    finally:
        conn.close()

def obtener_usuario(chat_id):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, username, first_name, photo_url FROM usuarios WHERE chat_id = %s", (str(chat_id),))
        r = cursor.fetchone()
        if r:
            return {'chat_id': str(r[0]), 'username': r[1], 'first_name': r[2], 'photo_url': r[3]}
        return None
    except Error as e:
        print(f"❌ Error obteniendo usuario: {e}")
        return None
    finally:
        conn.close()

def obtener_preferencias(chat_id):
    """Preferencias de notificación de un usuario: listas de estrategias y símbolos.
    Sin preferencias configuradas, el usuario recibe todas las notificaciones."""
    conn = get_db_connection()
    prefs = {'estrategias': [], 'simbolos': []}
    if not conn:
        return prefs
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT estrategia, simbolo FROM preferencias_notificaciones WHERE chat_id = %s
        """, (str(chat_id),))
        for r in cursor.fetchall():
            if r[0]:
                prefs['estrategias'].append(r[0])
            if r[1]:
                prefs['simbolos'].append(r[1])
    except Error as e:
        print(f"❌ Error obteniendo preferencias: {e}")
    finally:
        conn.close()
    return prefs

def guardar_preferencias(chat_id, estrategias=None, simbolos=None):
    """Reemplaza las preferencias de notificación de un usuario."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM preferencias_notificaciones WHERE chat_id = %s", (str(chat_id),))
        for e in (estrategias or []):
            cursor.execute("""
                INSERT INTO preferencias_notificaciones (chat_id, estrategia) VALUES (%s, %s)
            """, (str(chat_id), e))
        for s in (simbolos or []):
            cursor.execute("""
                INSERT INTO preferencias_notificaciones (chat_id, simbolo) VALUES (%s, %s)
            """, (str(chat_id), s))
        conn.commit()
        return True
    except Error as e:
        print(f"❌ Error guardando preferencias: {e}")
        return False
    finally:
        conn.close()

def usuario_quiere_notificacion(chat_id, estrategia, simbolo):
    """Sin preferencias => recibe todo. Con preferencias => recibe si coincide
    la estrategia Y el símbolo (intersección). Si solo tiene uno, filtra solo por ese."""
    prefs = obtener_preferencias(chat_id)
    sin_estrategias = not prefs['estrategias']
    sin_simbolos = not prefs['simbolos']
    if sin_estrategias and sin_simbolos:
        return True
    coincide_estrategia = sin_estrategias or (estrategia and estrategia in prefs['estrategias'])
    coincide_simbolo = sin_simbolos or (simbolo and simbolo in prefs['simbolos'])
    return coincide_estrategia and coincide_simbolo

def registrar_apertura(estrategia, simbolo, tipo, precio, sl=None, tp=None,
                       rango_alto=None, rango_bajo=None, ema_9=None, ema_21=None):
    conn = get_db_connection()
    if not conn:
        return None
    tabla = tabla_estrategia(estrategia)
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            INSERT INTO {tabla}
                (estrategia, simbolo, tipo, fecha_apertura, precio_entrada,
                 sl, tp, rango_alto, rango_bajo, ema_9, ema_21)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (estrategia, simbolo, tipo, datetime.now(), precio,
              sl, tp, rango_alto, rango_bajo, ema_9, ema_21))
        conn.commit()
        return cursor.lastrowid
    except Error as e:
        print(f"❌ Error registrando apertura: {e}")
        return None
    finally:
        conn.close()

def registrar_cierre(id_operacion, precio_salida, resultado, estrategia=None):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        if estrategia:
            tablas = [tabla_estrategia(estrategia)]
        else:
            tablas = [TABLA_PRODUCCION, TABLA_PRUEBA]
        actualizados = False
        for tabla in tablas:
            cursor.execute(f"""
                UPDATE {tabla}
                SET fecha_cierre = %s, precio_salida = %s, resultado = %s
                WHERE id = %s AND resultado = 'ABIERTA'
            """, (datetime.now(), precio_salida, resultado, id_operacion))
            if cursor.rowcount > 0:
                actualizados = True
                break
        conn.commit()
        return actualizados
    except Error as e:
        print(f"❌ Error registrando cierre: {e}")
        return False
    finally:
        conn.close()

def obtener_trades_abiertos(estrategia, simbolo=None):
    """Obtiene trades abiertos de una estrategia desde la BD."""
    conn = get_db_connection()
    trades = []
    if not conn:
        return trades
    tabla = tabla_estrategia(estrategia)
    try:
        cursor = conn.cursor()
        if simbolo:
            cursor.execute(f"""
                SELECT id, simbolo, tipo, precio_entrada, sl, tp, fecha_apertura,
                       rango_alto, rango_bajo
                FROM {tabla}
                WHERE estrategia = %s AND simbolo = %s AND resultado = 'ABIERTA'
            """, (estrategia, simbolo))
        else:
            cursor.execute(f"""
                SELECT id, simbolo, tipo, precio_entrada, sl, tp, fecha_apertura,
                       rango_alto, rango_bajo
                FROM {tabla}
                WHERE estrategia = %s AND resultado = 'ABIERTA'
            """, (estrategia,))
        trades = [{'id': row[0], 'simbolo': row[1], 'tipo': row[2],
                   'entrada': row[3], 'sl': row[4], 'tp': row[5],
                   'fecha_apertura': row[6],
                   'rango_alto': float(row[7]) if row[7] is not None else None,
                   'rango_bajo': float(row[8]) if row[8] is not None else None}
                  for row in cursor.fetchall()]
    except Error as e:
        print(f"❌ Error obteniendo trades abiertos: {e}")
    finally:
        conn.close()
    return trades

def cerrar_trades_trabados(estrategia, max_horas=24):
    """Cierra automáticamente trades abiertos por más de X horas.
    Para acciones/ETF, solo cierra si el mercado está abierto."""
    import yfinance as yf

    conn = get_db_connection()
    if not conn:
        return []

    cerrados = []
    tabla = tabla_estrategia(estrategia)
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT id, simbolo, tipo, precio_entrada, fecha_apertura
            FROM {tabla}
            WHERE estrategia = %s AND resultado = 'ABIERTA'
            AND fecha_apertura < NOW() - INTERVAL %s HOUR
        """, (estrategia, max_horas))

        trades = cursor.fetchall()

        for t in trades:
            trade_id, simbolo, tipo, entrada, fecha = t

            # Para acciones/ETF: no cerrar si el mercado está cerrado
            if es_accion_o_etf(simbolo) and not horario_mercado():
                continue

            # Obtener precio actual
            try:
                df = yf.download(simbolo, period='1d', interval='1m', progress=False, auto_adjust=True)
                if df.empty:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

                precio = float(df['Close'].iloc[-1])

                cursor.execute(f"""
                    UPDATE {tabla}
                    SET fecha_cierre = NOW(), precio_salida = %s,
                        resultado = 'CERRADO_AUTOMÁTICO'
                    WHERE id = %s AND resultado = 'ABIERTA'
                """, (precio, trade_id))

                cerrados.append({
                    'id': trade_id,
                    'simbolo': simbolo,
                    'tipo': tipo,
                    'entrada': float(entrada),
                    'salida': precio
                })
            except Exception as e:
                print(f"⚠️ Error cerrando trade {trade_id}: {e}")

        conn.commit()

        # Notificar por Telegram si se cerraron trades
        if cerrados:
            msg = f"🔄 *TRADES CERRADOS AUTOMÁTICAMENTE ({estrategia})*\n"
            msg += f"Se cerraron {len(cerrados)} trade(s) con más de {max_horas}h:\n"
            for c in cerrados:
                emoji = '📈' if c['tipo'] == 'LONG' else '📉'
                msg += f"{emoji} {c['simbolo']} {c['tipo']} @ {c['entrada']:.5f} → {c['salida']:.5f}\n"
            enviar_telegram(estrategia, None, msg)

    except Error as e:
        print(f"❌ Error cerrando trades trabados: {e}")
    finally:
        conn.close()

    return cerrados

# ==========================================
# RANGOS DESCARTADOS ( tras SL )
# ==========================================
def guardar_rango_descartado(estrategia, simbolo, rango_alto, rango_bajo):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO rangos_descartados (estrategia, simbolo, rango_alto, rango_bajo, fecha)
            VALUES (%s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE rango_alto = VALUES(rango_alto), rango_bajo = VALUES(rango_bajo), fecha = NOW()
        """, (estrategia, simbolo, rango_alto, rango_bajo))
        conn.commit()
        return True
    except Error as e:
        print(f"❌ Error guardando rango descartado: {e}")
        return False
    finally:
        conn.close()

def cargar_rangos_descartados(estrategia):
    conn = get_db_connection()
    resultado = {}
    if not conn:
        return resultado
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT simbolo, rango_alto, rango_bajo FROM rangos_descartados
            WHERE estrategia = %s
        """, (estrategia,))
        for r in cursor.fetchall():
            resultado[str(r[0])] = {'alto': float(r[1]) if r[1] else None, 'bajo': float(r[2]) if r[2] else None}
    except Error as e:
        print(f"❌ Error cargando rangos descartados: {e}")
    finally:
        conn.close()
    return resultado

def limpiar_rango_descartado(estrategia, simbolo):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM rangos_descartados WHERE estrategia = %s AND simbolo = %s
        """, (estrategia, simbolo))
        conn.commit()
        return True
    except Error as e:
        print(f"❌ Error limpiando rango descartado: {e}")
        return False
    finally:
        conn.close()

# ==========================================
# SALDOS
# ==========================================
def obtener_meta_pct(chat_id):
    """Obtiene el % de meta diaria configurado por el usuario (default 5.0)."""
    conn = get_db_connection()
    if not conn:
        return 5.0
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT meta_pct FROM usuarios WHERE chat_id = %s", (str(chat_id),))
        r = cursor.fetchone()
        return float(r[0]) if r and r[0] else 5.0
    except Error as e:
        print(f"❌ Error obteniendo meta_pct: {e}")
        return 5.0
    finally:
        conn.close()

def guardar_meta_pct(chat_id, pct):
    """Guarda el % de meta diaria del usuario. Crea el registro si no existe."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO usuarios (chat_id, meta_pct)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE meta_pct = VALUES(meta_pct)
        """, (str(chat_id), float(pct)))
        conn.commit()
        return True
    except Error as e:
        print(f"❌ Error guardando meta_pct: {e}")
        return False
    finally:
        conn.close()

def calcular_meta_diaria(balance, pct=5.0):
    """Calcula meta diaria (% configurable) y montos por trade (30% WR, ratio 1:3).
    Todo se escala proporcionalmente al %: si subes el %, también sube el
    riesgo/recompensa por trade; se mantiene la estructura 3*ganancia - 7*perdida = meta."""
    pct = float(pct or 5.0)
    factor = pct / 5.0
    meta = balance * pct / 100.0
    perdida = balance * 0.007534 * factor
    ganancia = balance * 0.03424 * factor
    return {
        'balance': balance,
        'meta_diaria': round(meta, 2),
        'perdida_trade': round(perdida, 2),
        'ganancia_trade': round(ganancia, 2)
    }

def registrar_saldo(chat_id, saldo):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        pct = obtener_meta_pct(chat_id)
        meta = calcular_meta_diaria(saldo, pct)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO saldos_diarios
                    (chat_id, saldo, meta_diaria, perdida_trade, ganancia_trade, fecha)
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (str(chat_id), saldo, meta['meta_diaria'],
                  meta['perdida_trade'], meta['ganancia_trade']))
        except Error:
            # Fallback si las columnas nuevas no existen aún
            cursor.execute("""
                INSERT INTO saldos_diarios (chat_id, saldo, fecha)
                VALUES (%s, %s, NOW())
            """, (str(chat_id), saldo))
        conn.commit()
        return meta
    except Error as e:
        print(f"❌ Error registrando saldo: {e}")
        return None
    finally:
        conn.close()

def obtener_meta_diaria(chat_id):
    """Obtiene la meta diaria calculada del último saldo registrado."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT saldo, meta_diaria, perdida_trade, ganancia_trade
            FROM saldos_diarios
            WHERE chat_id = %s
            ORDER BY fecha DESC
            LIMIT 1
        """, (str(chat_id),))
        r = cursor.fetchone()
        if r:
            return {
                'balance': float(r[0]),
                'meta_diaria': float(r[1]),
                'perdida_trade': float(r[2]),
                'ganancia_trade': float(r[3])
            }
        return None
    except Error as e:
        print(f"❌ Error obteniendo meta diaria: {e}")
        return None
    finally:
        conn.close()

def recalcular_ultimo_saldo(chat_id):
    """Recalcula meta_diaria/perdida_trade/ganancia_trade del último saldo
    con el % actual del usuario (tras cambiar la meta diaria)."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, saldo FROM saldos_diarios
            WHERE chat_id = %s
            ORDER BY fecha DESC
            LIMIT 1
        """, (str(chat_id),))
        r = cursor.fetchone()
        if not r:
            return None
        meta_id, balance = r[0], float(r[1])
        pct = obtener_meta_pct(chat_id)
        meta = calcular_meta_diaria(balance, pct)
        cursor.execute("""
            UPDATE saldos_diarios
            SET meta_diaria = %s, perdida_trade = %s, ganancia_trade = %s
            WHERE id = %s
        """, (meta['meta_diaria'], meta['perdida_trade'], meta['ganancia_trade'], meta_id))
        conn.commit()
        return meta
    except Error as e:
        print(f"❌ Error recalculando saldo: {e}")
        return None
    finally:
        conn.close()

def calcular_posicion(precio_entrada, sl, tp, chat_id=None):
    """Calcula tamaño de posición basado en ganancia_trade del último saldo.
    USD invertir = ganancia_trade / TP_distancia_%"""
    meta = obtener_meta_diaria(chat_id)
    if not meta:
        return None
    tp_dist = abs(tp - precio_entrada) / precio_entrada
    sl_dist = abs(precio_entrada - sl) / precio_entrada
    if tp_dist == 0:
        return None
    usd = meta['ganancia_trade'] / tp_dist
    return {
        'usd_invertir': round(usd, 0),
        'sl_dist_pct': round(sl_dist * 100, 3),
        'tp_dist_pct': round(tp_dist * 100, 3),
        'perdida': round(usd * sl_dist, 2),
        'ganancia': round(usd * tp_dist, 2)
    }

def obtener_saldos(chat_id=None, limit=30):
    conn = get_db_connection()
    saldos = []
    if conn:
        try:
            cursor = conn.cursor()
            if chat_id:
                cursor.execute("""
                    SELECT id, chat_id, saldo, meta_diaria, perdida_trade, ganancia_trade, fecha
                    FROM saldos_diarios
                    WHERE chat_id = %s
                    ORDER BY fecha DESC
                    LIMIT %s
                """, (str(chat_id), limit))
            else:
                cursor.execute("""
                    SELECT id, chat_id, saldo, meta_diaria, perdida_trade, ganancia_trade, fecha
                    FROM saldos_diarios
                    ORDER BY fecha DESC
                    LIMIT %s
                """, (limit,))
            saldos = [{'id': r[0], 'chat_id': r[1], 'saldo': float(r[2]),
                       'meta_diaria': float(r[3]) if r[3] else None,
                       'perdida_trade': float(r[4]) if r[4] else None,
                       'ganancia_trade': float(r[5]) if r[5] else None,
                       'fecha': r[6].strftime('%Y-%m-%d %H:%M') if r[6] else None}
                      for r in cursor.fetchall()]
        except Error as e:
            print(f"❌ Error obteniendo saldos: {e}")
        finally:
            conn.close()
    return saldos

# ==========================================
# TELEGRAM
# ==========================================
def icono_cierre(resultado):
    """Icono de éxito/fallo según el motivo del cierre: TP => ✅, SL => ❌, otros => ''."""
    r = (resultado or '').upper()
    if 'TP' in r:
        return '✅'
    if 'SL' in r:
        return '❌'
    return ''

def enviar_telegram(estrategia, simbolo, mensaje, posicion=None):
    # Verificar si las notificaciones están desactivadas
    notify_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.notifications_off')
    if os.path.exists(notify_file):
        print(f"🔕 [{estrategia}] Notificación desactivada: {mensaje[:50]}...")
        return

    ids = obtener_suscriptores()
    if not ids or not TELEGRAM_TOKEN:
        print(f"📢 [{estrategia}] {mensaje}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    conn = get_db_connection()
    for chat_id in ids:
        if not usuario_quiere_notificacion(chat_id, estrategia, simbolo):
            continue
        texto = mensaje
        if posicion:
            pos = calcular_posicion(posicion['entrada'], posicion['sl'], posicion['tp'], chat_id)
            if pos:
                texto = f"{mensaje}\n📐 Invertir: ${pos['usd_invertir']:.0f} | Riesgo: -${pos['perdida']:.2f} | Ganancia: +${pos['ganancia']:.2f}"
        message_id = None
        estado = 'ENVIADO'
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"},
                timeout=5
            )
            if resp.status_code == 200:
                try:
                    message_id = resp.json().get('result', {}).get('message_id')
                except ValueError:
                    pass
            else:
                estado = 'ERROR'
        except Exception as e:
            estado = 'ERROR'
            print(f"⚠️ Error enviando Telegram: {e}")

        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO telegram_mensajes
                        (chat_id, message_id, estrategia, simbolo, contenido, fecha_envio, estado)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (chat_id, message_id, estrategia, simbolo, texto, datetime.now(), estado))
                conn.commit()
            except Error as e:
                print(f"⚠️ Error guardando mensaje Telegram: {e}")

    if conn:
        conn.close()
