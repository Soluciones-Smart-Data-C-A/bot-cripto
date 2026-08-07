"""
Bot Telegram Listener - Registra saldos diarios via comandos.
Comandos:
  /saldo 5000    - Registra saldo de $5000
  /saldo         - Muestra último saldo registrado
  /historial     - Muestra últimos 10 registros
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

import telebot

common.cargar_entorno()
common.inicializar_db()

TELEGRAM_TOKEN = common.TELEGRAM_TOKEN

if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_TOKEN no configurado.")
    sys.exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)


def es_usuario_autorizado(chat_id):
    return str(chat_id) in common.obtener_suscriptores()


@bot.message_handler(commands=['start', 'help'])
def cmd_help(message):
    if not es_usuario_autorizado(message.chat.id):
        return
    bot.reply_to(message,
        "📊 *Bot Cripto - Saldos*\n\n"
        "Comandos:\n"
        "/saldo 5000 - Registrar saldo\n"
        "/saldo - Ver último saldo\n"
        "/historial - Últimos 10 registros",
        parse_mode="Markdown")


@bot.message_handler(commands=['saldo'])
def cmd_saldo(message):
    if not es_usuario_autorizado(message.chat.id):
        return

    text = message.text.strip()
    parts = text.split()

    if len(parts) > 1:
        try:
            saldo = float(parts[1].replace(',', '').replace('$', ''))
        except ValueError:
            bot.reply_to(message, "❌ Formato inválido. Usa: /saldo 5000")
            return

        if saldo <= 0:
            bot.reply_to(message, "❌ El saldo debe ser mayor a 0.")
            return

        if common.registrar_saldo(message.chat.id, saldo):
            bot.reply_to(message, f"✅ Saldo registrado: *${saldo:,.2f}*", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Error guardando saldo. Intenta de nuevo.")
    else:
        saldos = common.obtener_saldos(chat_id=message.chat.id, limit=1)
        if saldos:
            s = saldos[0]
            bot.reply_to(message,
                f"💰 *Último saldo:* ${s['saldo']:,.2f}\n📅 {s['fecha']}",
                parse_mode="Markdown")
        else:
            bot.reply_to(message, "📭 No hay saldos registrados.\nUsa: /saldo 5000")


@bot.message_handler(commands=['historial'])
def cmd_historial(message):
    if not es_usuario_autorizado(message.chat.id):
        return

    saldos = common.obtener_saldos(chat_id=message.chat.id, limit=10)
    if not saldos:
        bot.reply_to(message, "📭 No hay saldos registrados.")
        return

    lines = ["📊 *Historial de Saldos:*\n"]
    for s in saldos:
        lines.append(f"• ${s['saldo']:,.2f} - {s['fecha']}")

    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")


if __name__ == '__main__':
    print("🤖 Telegram Listener de saldos iniciado")
    bot.infinity_polling()
