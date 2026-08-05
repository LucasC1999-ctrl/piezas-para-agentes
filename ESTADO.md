# Estado — piezas para agentes

Última actualización: 2026-08-04, madrugada.

## Dónde estamos

Dos piezas funcionando de punta a punta, con 67 tests en verde.

| pieza | estado | qué falta |
|---|---|---|
| `vault-mcp` | **funciona** — 21 tests | API HTTP (opcional) |
| `wallet-mcp` | **funciona con sandbox** — 46 tests | drivers reales |

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

## Puente con Buzz — LISTO

El intento pendiente se publica como mensaje en un canal y se aprueba con una
reacción: ✅ ejecuta, ❌ rechaza. El comprobante vuelve como respuesta en el
mismo hilo. Verificado contra el relay real, no sólo con mocks.

**La seguridad está en `BUZZ_APROBADORES`, no en el canal.** Un canal privado no
alcanza: cualquiera adentro podría reaccionar. Sólo los pubkeys de esa lista
aprueban; el resto se ignora. Lista vacía = nadie aprueba (default seguro).
Si hay ✅ y ❌ a la vez, gana el ❌.

```bash
export BUZZ_CHANNEL=<uuid> BUZZ_PRIVATE_KEY=<nsec> \
       BUZZ_APROBADORES=<pubkey-hex>,<otra>
wallet-buzz              # bucle
wallet-buzz --una-vez    # una ronda
```

## REQUISITO NUEVO del vault (2026-08-05) — leer antes de tocar el modelo

El vault NO es un gestor de contraseñas personal: es **el reemplazo de las dos
planillas del estudio**. Hoy tienen un archivo con usuarios y claves fiscales
(impuestos) y otro con portales de bancos. Eso define la forma:

**Pestañas = secciones.** Se pueden agregar (Impuestos, Bancos, y las que
vengan). Cada pestaña **define sus propias columnas** y se pueden sumar en
cualquier momento: nada de un esquema fijo.

Columnas de arranque al crear una pestaña (editables, no obligatorias):

    cliente | portal | usuario | clave | otros | link

**Cada columna declara su tipo, y eso decide qué se cifra:**

| tipo      | se guarda | ¿la ve el agente sin permiso? | ¿aparece en el aviso de Buzz? |
|-----------|-----------|-------------------------------|-------------------------------|
| `texto`   | en claro  | sí                            | sí                            |
| `link`    | en claro  | sí                            | sí                            |
| `secreto` | cifrado   | **no**                        | **no** (sólo "cambió")        |

Que sea por columna y no por tabla permite dos secretos en la misma fila
(p.ej. clave fiscal + clave del token) y campos nuevos como "vencimiento" o
"responsable" sin migrar nada.

Implicancia para el modelo: el `Secret(name, value)` de hoy pasa a ser
`Seccion(columnas[]) -> Entrada(valores{})`. La maquinaria (cifrado, permisos
por agente, auditoría, MCP, puente Buzz) NO cambia — cambia la forma del dato.

**La funcionalidad estrella NO es guardar: es avisar.** Cuando alguien cambia
una clave o agrega un usuario, el aviso sale solo al canal de Buzz — "Lucas
actualizó la clave de ARCA de <cliente>". **Nunca el valor, sólo que cambió.**
Ese es el dolor real del estudio: alguien rota una clave y el resto se entera
el jueves cuando no puede entrar. Ningún gestor de contraseñas lo resuelve.

**Cómo se usa:** un acceso directo en el escritorio (`.desktop`) que abre la
web local. Nada que instalar, y se ve igual desde el teléfono.

**Dónde corre:** en UN solo lugar (el servidor del estudio, donde ya vive el
relay de Buzz). Todos entran por web a la misma instancia — así no hay copias
que sincronizar ni bases SQLite viajando por OneDrive, que se corrompen.

**Lo que NO hay que construir:**
- Autoguardado tipo Google / autocompletado de formularios. Eso necesita una
  extensión de navegador y es otro producto. Lucas sigue usando Chrome para
  entrar a las páginas; conviven sin pisarse.
- KeePassXC como backend. Se evaluó y se descartó: es para una persona con su
  base, no para un equipo que necesita enterarse de los cambios.

**Pendiente de definir:** ¿todos ven todas las credenciales, o hace falta
permiso por persona además de por agente? Sin eso resuelto, asumir que todos
ven todo (es lo que pasa hoy con las planillas).

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
3. Driver BIND, cuando haya contrato.
4. El puente hoy pollea cada 5s. Si el volumen crece, conviene suscribirse a
   los eventos del relay en vez de preguntar.

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
