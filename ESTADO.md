# Estado — piezas para agentes

Última actualización: 2026-08-04, madrugada.

## Dónde estamos

Dos piezas funcionando de punta a punta, con 53 tests en verde.

| pieza | estado | qué falta |
|---|---|---|
| `vault-mcp` | **funciona** — 21 tests | API HTTP (opcional) |
| `wallet-mcp` | **funciona con sandbox** — 32 tests | drivers reales |

## Decisiones tomadas (no volver a discutirlas sin motivo)

1. **Piezas sueltas, no un monolito.** Cada una es su propio MCP, se instala
   sola. La wallet *puede* usar el vault pero no lo necesita.
2. **OSS, Apache-2.0.** El objetivo es que cualquiera le dé una wallet a su
   agente, no vender un SaaS.
3. **El agente propone, la persona firma.** Las herramientas que aprueban viven
   en programas distintos (`vault-admin`, `wallet-admin`), no detrás de un flag.
4. **Sin custodia de fondos.** El CVU se emite a nombre del cliente final, con
   su KYC. No hay tabla de saldos: el saldo lo tiene el proveedor. Esto es lo
   que mantiene el proyecto fuera del registro PSP del BCRA.
5. **Permisos del vault: booleano.** Agente X tiene el secreto Y, o no. Sin
   roles ni niveles.
6. **Sandbox como driver de referencia**, para que el repo se pruebe sin cuenta
   en ningún lado.

## Cómo correrlo

```bash
cd packages/wallet-mcp && uv venv && uv pip install -e ".[dev]"
export WALLET_HOME=/tmp/wallet-demo
.venv/bin/wallet-admin crear --nombre "Juan Perez" --documento 30123456
.venv/bin/wallet-admin pendientes
.venv/bin/wallet-admin aprobar <id> --como lucas
.venv/bin/wallet-admin resumen <wallet_id> --formato html --salida r.html

# tests
.venv/bin/python -m pytest tests/ -q      # 32
cd ../vault-mcp && .venv/bin/python -m pytest tests/ -q   # 21

# API para frontends
WALLET_API_TOKEN=xxx .venv/bin/wallet-api   # http://127.0.0.1:8479/docs

# conectar a Buzz
./scripts/conectar-a-buzz.sh Claude
```

## SIGUIENTE PASO

**Driver de Mercado Pago, parcial y honesto.**

Ya está verificado que la API pública de MP **no hace transferencias salientes**
a terceros: su doc para desarrolladores sólo tiene cobros (Checkout Pro, Bricks,
API, suscripciones, QR, Point). Así que el driver de MP tiene que declarar:

```python
Capacidades(
    consultar_saldo=True, listar_movimientos=True, cobrar=True,
    transferir=False,
    motivos={"transferir": "la API pública de Mercado Pago no permite money-out a terceros"},
)
```

Eso ya está soportado por el diseño — es literalmente el caso de uso para el
que existe `Capacidades`. Falta implementar las llamadas HTTP.

### Después

1. `wallet_cobrar` (generar link de pago / QR) — es lo que MP **sí** permite y
   lo que haría útil el driver desde el día uno.
2. API HTTP del vault, para administrarlo desde una pantalla.
3. Notificación de pendientes hacia Buzz: hoy el agente propone y hay que
   correr `wallet-admin pendientes` a mano. Lo natural es que el intento
   aparezca como mensaje en un canal y se apruebe desde ahí.
4. Driver BIND, cuando haya contrato.

## Aprendido (no repetir)

- **El sandbox necesita persistir.** Arrancó en memoria y los unit tests
  pasaban, pero cada comando de la CLI es un proceso nuevo: la wallet existía y
  el saldo nacía en cero. Los unit tests no lo agarraron porque usan una sola
  instancia. Se arregla con `estado_en=`.
- **SQLite y threads.** Una conexión no se puede compartir entre threads y
  FastAPI atiende cada request en uno distinto. Va `threading.local`.
- **No emitir CVU.** No es una limitación técnica que se pueda saltear con
  código: es el registro PSP del BCRA (Com. "A" 8432/2026).
- El default de `limite_sin_aprobacion` es **cero** y tiene que seguir siéndolo.
