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
        "/saldo 73 - Registrar saldo\n"
        "/saldo - Ver último saldo y meta\n"
        "/size entrada sl tp - Calcular posición\n"
        "/historial - Últimos 10 registros\n\n"
        "La meta diaria se calcula al 5% del saldo.\n"
        "Win rate asumido: 30% (3W / 7L en 10 trades).",
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
            meta = common.calcular_meta_diaria(saldo)
            bot.reply_to(message,
                f"✅ Saldo registrado: *${saldo:,.2f}*\n\n"
                f"📊 *Meta diaria (5%):* ${meta['meta_diaria']:.2f}\n"
                f"Per trade: ganar *${meta['ganancia_trade']:.2f}* / perder *${meta['perdida_trade']:.2f}*\n"
                f"10 trades (30% WR): +${meta['ganancia_trade']*3:.2f} - ${meta['perdida_trade']*7:.2f} = *${meta['meta_diaria']:.2f}*",
                parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Error guardando saldo. Intenta de nuevo.")
    else:
        meta = common.obtener_meta_diaria(message.chat.id)
        if meta:
            bot.reply_to(message,
                f"💰 *Último saldo:* ${meta['balance']:,.2f}\n\n"
                f"📊 *Meta diaria (5%):* ${meta['meta_diaria']:.2f}\n"
                f"Per trade: ganar *${meta['ganancia_trade']:.2f}* / perder *${meta['perdida_trade']:.2f}*\n"
                f"10 trades (30% WR): +${meta['ganancia_trade']*3:.2f} - ${meta['perdida_trade']*7:.2f} = *${meta['meta_diaria']:.2f}*",
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
        lines.append(f"• ${s['saldo']:,.2f} | Meta: ${s['meta_diaria']:.2f} | {s['fecha']}")

    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=['size'])
def cmd_size(message):
    if not es_usuario_autorizado(message.chat.id):
        return

    parts = message.text.strip().split()
    if len(parts) != 4:
        bot.reply_to(message,
            "📐 *Calcular posición*\n\n"
            "Uso: /size entrada sl tp\n"
            "Ejemplo: /size 64562.71 64433.58 64950.08",
            parse_mode="Markdown")
        return

    try:
        entrada = float(parts[1])
        sl = float(parts[2])
        tp = float(parts[3])
    except ValueError:
        bot.reply_to(message, "❌ Valores inválidos. Usa números.")
        return

    pos = common.calcular_posicion(entrada, sl, tp)
    if not pos:
        bot.reply_to(message, "❌ No hay saldo registrado o error en cálculo.\nUsa /saldo primero.")
        return

    bot.reply_to(message,
        f"📐 *Posición calculada*\n\n"
        f"Entrada: {entrada:.5f}\n"
        f"SL: {sl:.5f} ({pos['sl_dist_pct']}%)\n"
        f"TP: {tp:.5f} ({pos['tp_dist_pct']}%)\n\n"
        f"💰 *Invertir: ${pos['usd_invertir']:.0f}*\n"
        f"Red: -${pos['perdida']:.2f} | Win: +${pos['ganancia']:.2f}",
        parse_mode="Markdown")


if __name__ == '__main__':
    print("🤖 Telegram Listener de saldos iniciado")
    bot.infinity_polling()
