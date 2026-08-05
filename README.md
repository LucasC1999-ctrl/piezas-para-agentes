# Piezas para agentes

Componentes sueltos que le dan capacidades reales a un agente de IA. Cada uno es
un servidor MCP independiente: se instalan por separado, se usan por separado, y
se combinan si querés.

| pieza | qué hace | estado |
|---|---|---|
| [`vault-mcp`](packages/vault-mcp) | secretos con permiso explícito por agente | funciona |
| [`wallet-mcp`](packages/wallet-mcp) | wallets y pagos, con aprobación humana | funciona con driver sandbox |

## La idea

Un agente que puede hacer cosas de verdad necesita dos cosas que nadie le quiere
dar: credenciales y permiso para mover plata. Estas piezas resuelven cada una
por separado, con el mismo criterio:

**El agente propone, la persona dispone.** El agente puede consultar todo lo que
le habilitaste y proponer cualquier operación, pero lo irreversible —leer un
secreto que no le diste, ejecutar una transferencia— requiere una acción humana.
No es una opción de configuración: las herramientas que aprueban viven en
programas distintos de los que usa el agente.

## Probarlo en dos minutos

Sin cuenta en ningún lado, sin credenciales, sin trámites:

```bash
git clone <este-repo> && cd piezas

# la wallet, con proveedor simulado
cd packages/wallet-mcp && uv venv && uv pip install -e ".[dev]"
export WALLET_HOME=/tmp/wallet-demo

.venv/bin/wallet-admin crear --nombre "Juan Perez" --documento 30123456
.venv/bin/wallet-admin wallets
```

El agente propone una transferencia y queda esperando:

```
wallet_proponer_transferencia(wallet_id, monto="15000.50", cvu="000...")
  -> requiere_aprobacion, intent_id: int_39ff171e...
```

Y vos la firmás:

```bash
.venv/bin/wallet-admin pendientes
.venv/bin/wallet-admin aprobar int_39ff171e... --como lucas
```

## Conectarlas a Buzz

```bash
./scripts/conectar-a-buzz.sh Claude
```

Agrega los MCP al agente que le digas, en todas las comunidades donde exista.
Hace backup antes y es idempotente: correrlo dos veces no duplica nada.

## Cómo está armado

Cada pieza tiene la misma forma:

```
core/     lógica y reglas — sin saber nada de MCP ni de HTTP
drivers/  integraciones con proveedores (sólo wallet)
mcp/      el servidor que ve el agente, y la CLI que usás vos
api/      API HTTP para montarle el frontend que quieras
```

El `core` no importa nada de `mcp`. Eso es lo que permite que la misma lógica
sirva para un agente en Buzz, para una web a medida y para la línea de comandos
sin escribir la regla tres veces.

### Sobre los drivers de wallet

Los proveedores **no son intercambiables**, así que cada driver declara qué
sabe hacer:

```python
Capacidades(
    consultar_saldo=True,
    transferir=False,
    motivos={"transferir": "la API pública de este proveedor no permite money-out"},
)
```

El agente consulta `wallet_capacidades` y se entera de lo que no puede hacer
*antes* de prometérselo a alguien, en vez de fallar con un error opaco del
proveedor a mitad de camino.

Drivers disponibles: `sandbox`. Los reales van como paquetes aparte.

## Lo que estas piezas no hacen

- **No emiten CVU.** Eso lo hace un proveedor de servicios de pago registrado.
  En Argentina la figura es "PSPCP como Servicio" (Com. BCRA "A" 8432/2026): el
  CVU se emite a nombre de tu cliente, con su KYC, y vos operás con su permiso.
- **No custodian fondos.** No hay tabla de saldos: el saldo lo tiene el
  proveedor. Lo que se guarda acá es quién propuso qué y quién lo autorizó.
- **No generan PDF.** El resumen sale en HTML imprimible; el navegador lo
  exporta a PDF con Ctrl+P sin arrastrar una dependencia binaria al proyecto.

## Licencia

Apache-2.0
