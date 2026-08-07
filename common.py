"""
Módulo común: conexión a BD, registro de operaciones y mensajería Telegram.
Usado por todas las estrategias del bot (Mora EMA Cross, CRT, NY Open).
"""

import os
import sys
from datetime import datetime

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

# Activos monitoreados por todas las estrategias
ACTIVOS = ['BTC-USD', 'SOL-USD', 'HYPE32196-USD']

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

        conn.commit()
    except Error as e:
        print(f"❌ Error inicializando tablas: {e}")
    finally:
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

def registrar_apertura(estrategia, simbolo, tipo, precio, sl=None, tp=None,
                       rango_alto=None, rango_bajo=None, ema_9=None, ema_21=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO historial_operaciones
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

def registrar_cierre(id_operacion, precio_salida, resultado):
    conn = get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE historial_operaciones
            SET fecha_cierre = %s, precio_salida = %s, resultado = %s
            WHERE id = %s
        """, (datetime.now(), precio_salida, resultado, id_operacion))
        conn.commit()
    except Error as e:
        print(f"❌ Error registrando cierre: {e}")
    finally:
        conn.close()

def obtener_trades_abiertos(estrategia, simbolo=None):
    """Obtiene trades abiertos de una estrategia desde la BD."""
    conn = get_db_connection()
    trades = []
    if conn:
        try:
            cursor = conn.cursor()
            if simbolo:
                cursor.execute("""
                    SELECT id, simbolo, tipo, precio_entrada, sl, tp, fecha_apertura
                    FROM historial_operaciones
                    WHERE estrategia = %s AND simbolo = %s AND resultado = 'ABIERTA'
                """, (estrategia, simbolo))
            else:
                cursor.execute("""
                    SELECT id, simbolo, tipo, precio_entrada, sl, tp, fecha_apertura
                    FROM historial_operaciones
                    WHERE estrategia = %s AND resultado = 'ABIERTA'
                """, (estrategia,))
            trades = [{'id': row[0], 'simbolo': row[1], 'tipo': row[2],
                       'entrada': row[3], 'sl': row[4], 'tp': row[5],
                       'fecha_apertura': row[6]}
                      for row in cursor.fetchall()]
        except Error as e:
            print(f"❌ Error obteniendo trades abiertos: {e}")
        finally:
            conn.close()
    return trades

def cerrar_trades_trabados(estrategia, max_horas=24):
    """Cierra automáticamente trades abiertos por más de X horas."""
    import yfinance as yf

    conn = get_db_connection()
    if not conn:
        return []

    cerrados = []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, simbolo, tipo, precio_entrada, fecha_apertura
            FROM historial_operaciones
            WHERE estrategia = %s AND resultado = 'ABIERTA'
            AND fecha_apertura < NOW() - INTERVAL %s HOUR
        """, (estrategia, max_horas))

        trades = cursor.fetchall()

        for t in trades:
            trade_id, simbolo, tipo, entrada, fecha = t

            # Obtener precio actual
            try:
                df = yf.download(simbolo, period='1d', interval='1m', progress=False, auto_adjust=True)
                if df.empty:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df = df.xs(simbolo, axis=1, level=1, drop_level=True).copy()

                precio = float(df['Close'].iloc[-1])

                cursor.execute("""
                    UPDATE historial_operaciones
                    SET fecha_cierre = NOW(), precio_salida = %s,
                        resultado = 'CERRADO_AUTOMÁTICO'
                    WHERE id = %s
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
# TELEGRAM
# ==========================================
def enviar_telegram(estrategia, simbolo, mensaje):
    ids = obtener_suscriptores()
    if not ids or not TELEGRAM_TOKEN:
        print(f"📢 [{estrategia}] {mensaje}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    conn = get_db_connection()
    for chat_id in ids:
        message_id = None
        estado = 'ENVIADO'
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
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
                """, (chat_id, message_id, estrategia, simbolo, mensaje, datetime.now(), estado))
                conn.commit()
            except Error as e:
                print(f"⚠️ Error guardando mensaje Telegram: {e}")

    if conn:
        conn.close()
