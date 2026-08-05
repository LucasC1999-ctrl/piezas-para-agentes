# wallet-mcp

Wallets para agentes de IA. El agente propone; una persona firma.

## Qué hace

Le da a un agente la capacidad de consultar saldos, ver movimientos, generar
resúmenes y **proponer** transferencias. Ejecutarlas requiere aprobación humana.

```
wallet_capacidades()             qué puede el proveedor configurado
wallet_listar()                  las wallets disponibles
wallet_crear(...)                alta con titular y documento
wallet_saldo(id)                 saldo consultado al proveedor
wallet_movimientos(id)           extracto
wallet_resumen(id, formato)      texto | json | csv | html
wallet_proponer_transferencia()  -> requiere_aprobacion + intent_id
wallet_estado_intento(id)        ¿ya lo aprobaron?
wallet_pendientes()              qué está esperando firma
```

No existe `wallet_aprobar`. Aprobar se hace con `wallet-admin`, que es otro
programa. Si el agente pudiera aprobar sus propias propuestas, la aprobación
sería un trámite y no una compuerta.

## Probarlo sin cuenta en ningún lado

```bash
uv venv && uv pip install -e ".[dev]"
export WALLET_HOME=/tmp/wallet-demo

.venv/bin/wallet-admin crear --nombre "Juan Perez" --documento 30123456
.venv/bin/wallet-admin pendientes
.venv/bin/wallet-admin aprobar <intent_id> --como tu-nombre
```

## Resúmenes

```bash
wallet-admin resumen <wallet_id> --formato html \
    --entidad "Tu Estudio" --color "#0a5c3a" --salida resumen.html
```

Cuatro formatos: `texto` para pegar en un chat, `json` para que un frontend lo
dibuje, `csv` para planilla, y `html` autocontenido e imprimible (Ctrl+P lo
exporta a PDF sin dependencias binarias).

El documento del titular sale enmascarado (`•••••456`) en todos.

## Configuración

| variable | para qué | default |
|---|---|---|
| `WALLET_HOME` | dónde vive la base | `~/.local/share/wallet-mcp` |
| `WALLET_DRIVER` | proveedor | `sandbox` |
| `WALLET_LIMITE_SIN_APROBACION` | monto que pasa sin firma | `0` |
| `WALLET_AGENT_ID` | quién propuso, para el registro | `agente` |
| `WALLET_MARCA_*` | marca de los resúmenes | — |

`WALLET_LIMITE_SIN_APROBACION` arranca en **cero**: nada se ejecuta solo hasta
que vos decidas lo contrario.

## Escribir un driver

Implementá el protocolo de `drivers/base.py` y declará tus capacidades. Si tu
proveedor no puede transferir, decilo — el agente se entera antes de prometer:

```python
class MiDriver:
    nombre = "mi-proveedor"
    def capacidades(self):
        return Capacidades(
            consultar_saldo=True, transferir=False,
            motivos={"transferir": "esta API sólo cobra"},
        )
```

No hace falta heredar de nada: es un `Protocol`.

## Licencia

Apache-2.0
