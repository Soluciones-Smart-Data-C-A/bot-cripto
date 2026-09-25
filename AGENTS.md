# AGENTS.md

Bot de trading cripto/acciones. Sin framework: scripts Python sueltos que corren en paralelo dentro de un contenedor, comunicados solo por MySQL, más un dashboard Flask. **No hay tests, linters ni CI.** Solo rama `main`; push directo → redeploy manual en Coolify (`bot-cripto.gscloud.us`).

## Verificación (no hay tests/lint)
- Python: `python3 -m py_compile <archivo>.py`
- JS (templates, `<script>` inline, sin linter): extraer el/los bloque(s) y validar

  ```bash
  python3 -c "import re;html=open('templates/index.html').read();open('/tmp/x.js','w').write(chr(10).join(re.findall(r'<script>(.*?)</script>',html,re.S)))" \
    && node --check /tmp/x.js
  ```
- Los bots requieren `import common` vía CWD: ejecutarlos desde la raíz del repo. Dev usa Python 3.14; el contenedor, 3.10-slim (`Dockerfile:2`) — no usar sintaxis >3.10.
- Entorno: `sys.argv[1]` = `'local'` (→ `.env_local`) o `'produccion'` (→ `.env`); el contenedor usa vars directas de Coolify. `.env*` están gitignored y contienen secretos vivos: **nunca commitearlos**.

## Arquitectura (no obvio desde los filenames)
- Sin dispatcher. Cada `estrategia_*.py`, `bot_mora_trader.py`, `bot_telegram_listener.py` y `dashboard.py` es un proceso independiente lanzado por el `CMD` del Dockerfile. Comunicación solo por MySQL; dashboard y estrategias NO se importan entre sí. Import falla si la estrategia NO está en el Dockerfile.
- Filename ≠ estrategia: `estrategia_ema_cross.py` = `NY_OPEN`, `bot_mora_trader.py` = `MORA_EMA_CROSS`, `estrategia_crt_v2.py` = `CRT_V7`. La fuente de verdad es la constante `ESTRATEGIA = '...'` de cada archivo.
- `common.py`: librería compartida (DB, Telegram, activos, tabla→estrategia). `reportes.py`: solo vía `/api/reportes` (import lazy en dashboard).
- La tabla de trades se deriva del nombre: las de `TEST_ESTRATEGIAS` van a `historial_pruebas`; el resto a `historial_operaciones` (`common.tabla_estrategia`). Producción/prueba = automático por nombre; mover una estrategia = sacarla de `TEST_ESTRATEGIAS` (la migración `migrar_estrategias_a_prueba` corre en cada boot).

## Registrar una estrategia (triple bookkeeping — error silencioso)
Debe ir en 3 lugares o se rompe en silencio (filtros, reportes, SSE):
1. `common.TEST_ESTRATEGIAS` (o fuera, si es producción) — `common.py:66`
2. `dashboard.py:28-29` — `ESTRATEGIAS_PRODUCCION` / `ESTRATEGIAS_PRUEBA`
3. `templates/estrategias.html:80` — dict `ESTRATEGIAS`
Nuevo archivo: agregarlo al `COPY` y al `CMD` del `Dockerfile`, con el guard final
`if __name__ == "__main__": if not common.verificar_config(): sys.exit(1); ejecutar_bot()`.

## Boilerplate de una estrategia
- Constante `ESTRATEGIA`; dict en memoria de operaciones; `common.inicializar_db()` al inicio de `ejecutar_bot()`.
- Abrir/cerrar con `common.registrar_apertura` / `common.registrar_cierre`.
- **Una operación por símbolo**: chequea `common.obtener_trades_abiertos(estrategia, simbolo)` antes de abrir.
- Saltar acciones/ETF con mercado cerrado: `es_accion_o_etf(...)` + `horario_mercado()`.
- Notificar con `common.enviar_telegram(...)`; el cuerpo SIEMPRE sale de `common.mensaje_senal()` (apertura) / `common.mensaje_cierre()` (cierre) + `extra=[...]` para líneas propias — no escribir f-strings de mensajes (estilo unificado 2026-09).

## Gotchas
- **`INSTA_SWEEP_V1` está DETENIDA** (2026-09-25, 13% acierto): `estrategia_insta_sweep.py` tiene `COPY` pero NO está en el `CMD` del Dockerfile. Para reactivar: agregar `python estrategia_insta_sweep.py &` al CMD. Sus trades abiertos los cierra el watchdog del dashboard.
- `horario_mercado()` hardcodea EDT −4h (`common.py:110`): se desfasa 1h cuando US NO está en DST (nov–mar).
- `inicializar_db()` hace `DROP TABLE IF EXISTS historial_mora_cross / historial_mora_ny` en cada boot (`common.py:156-157`): no reintroducir esos nombres.
- Resultados de cierre se cuentan por substring (`LIKE '%TP%'`/`'%SL%'`, `dashboard.py:290`, `reportes.py:133`): mantener las subcadenas `TP`/`SL` en cualquier resultado nuevo (p.ej. `TP_MANUAL`, `SL_MANUAL`, `TP: ALTO DEL RANGO ✅`).
- Estrategias de prueba NO emiten SSE/push web (`dashboard.py:224-232`), pero SÍ envían Telegram.
- Login del dashboard `/auth/tg` es el único registro de usuarios; destinatarios de Telegram salen de la tabla `usuarios` (no hay chat-id hardcodeado).
- Pausa global de notificaciones = archivo sidecar `.notifications_off` (no BD). Horario 8–17 en `usuarios.notif_horario`.
- `dashboard.py:1240`: `app.run(threaded=True)` puerto 5000, sin gunicorn. Varias APIs (stats, trades, open-trades, filters, reportes, opciones) no exigen login.