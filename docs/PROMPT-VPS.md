# Prompt maestro — montar el stack en el VPS

Copiá todo lo que está entre las líneas de guiones y pegáselo al Claude Code
del VPS. Antes de pegarlo, completá los cuatro datos de la primera sección.

---

Vas a montar el stack de comunicación con agentes del estudio en este VPS.
Trabajá con cuidado: esto va a ser el servidor de producción de un estudio
contable. Verificá cada paso antes de pasar al siguiente y no des nada por
hecho sin comprobarlo.

## Datos de esta instalación

- Dominio: `buzz.ovdianlabs.com`
- Email para Let's Encrypt: `administracion@ovdianlabs.com`
- Identidad de admin: **la vas a generar vos en el Paso 3** (no reutilizamos la de mi
  notebook: el relay del estudio tiene que tener una identidad propia)
- Repo de las piezas: `https://github.com/LucasC1999-ctrl/piezas-para-agentes`

El DNS ya está creado y propagado (verificado el 2026-08-06): `buzz.ovdianlabs.com`
resuelve a `178.105.175.106` en los resolvedores públicos.

## Qué tiene que quedar andando

1. **El relay de Buzz** — servidor de chat con agentes, con TLS.
2. **Las tres piezas** (`vault-mcp`, `wallet-mcp`, `planilla-watch`), instaladas
   y con sus tests en verde.

## Paso 0 — Confirmar que el DNS ya apunta acá

Nada de lo que sigue funciona sin esto: Caddy le pide el certificado a Let's
Encrypt, que valida resolviendo el dominio. Si el DNS no está, falla y el
relay no levanta con TLS.

```bash
dig +short A buzz.ovdianlabs.com
curl -s ifconfig.me; echo
```

Las dos salidas tienen que ser **178.105.175.106**. Si `dig` no devuelve nada
o devuelve otra IP, **frená y avisame**: el registro DNS se crea en Squarespace
(Settings → Domains → ovdianlabs.com → DNS Settings) con tipo `A`, host `buzz`,
apuntando a esa IP. Tarda unos minutos en propagar.

## Paso 1 — Reconocer el terreno

Antes de instalar nada, decime qué encontrás:

```bash
cat /etc/os-release | head -2
docker --version; docker compose version
free -h; df -h /; nproc
sudo ss -tlnp | grep -E ':(80|443|3000)\s'
```

Si Docker no está, instalalo con el script oficial de Docker (`get.docker.com`),
no con el paquete de la distro — el de Debian suele venir sin `compose` v2.

**Si los puertos 80 o 443 están ocupados, pará y avisame.** Probablemente haya
otro servicio y necesitamos decidir qué hacer, no pisarlo.

## Paso 2 — El relay de Buzz

```bash
git clone https://github.com/block/buzz.git ~/buzz-src
cd ~/buzz-src/deploy/compose
cp .env.example .env   # si no existe, mirá qué variables pide compose.yml
```

Editá `.env` con esto, y prestá atención a la primera variable porque es la
que más problemas da:

```
# CRÍTICO: el relay deriva de acá el "host" de su comunidad, y después SÓLO
# responde a pedidos que lleguen con ese Host exacto, puerto incluido. Si esto
# no coincide con el dominio por el que se entra, el WebSocket devuelve
# "404 no community is configured for this host" y ningún agente se conecta,
# aunque el HTTP normal responda 200 y parezca que todo anda.
# Va el dominio público con wss://, sin puerto (443 es implícito).
BUZZ_RELAY_URL=wss://buzz.ovdianlabs.com

# Generá cada uno con: openssl rand -hex 32
BUZZ_S3_ACCESS_KEY=<generar>
BUZZ_S3_SECRET_KEY=<generar>
BUZZ_S3_BUCKET=buzz-media

BUZZ_AUTO_MIGRATE=true
BUZZ_REQUIRE_RELAY_MEMBERSHIP=true
```

Levantalo **con** el compose de Caddy, que resuelve el TLS solo:

```bash
docker compose -f compose.yml -f compose.caddy.yml up -d
docker compose ps
docker compose logs relay | grep -i "community ensured"
```

Esa última línea es la que confirma que quedó bien. Tiene que decir
`host: buzz.ovdianlabs.com`. **Si dice `localhost:3000` u otra cosa, `BUZZ_RELAY_URL`
está mal: corregilo, `docker compose down` y volvé a levantar.** No sigas con
un host equivocado, porque todo lo demás va a fallar de formas confusas.

Verificá el TLS y el WebSocket desde afuera:

```bash
curl -sI https://buzz.ovdianlabs.com | head -3
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  https://buzz.ovdianlabs.com/
```

El segundo tiene que dar **101** (Switching Protocols). Si da 404, es el
problema del host que expliqué arriba. Si da 502, el relay no levantó.

## Paso 3 — La identidad de administración del estudio

En Buzz no hay cuentas con mail ni login con Google: la identidad es un par de
claves Nostr. Este relay necesita una identidad de admin **propia del estudio**,
no la de una notebook personal — si el admin fuera la identidad de una máquina,
el día que esa máquina se rompe se pierde el control del relay.

Generala en el VPS:

```bash
docker exec buzz-prod-relay-1 buzz-admin generate-key
```

Eso imprime una clave pública y una privada.

**Con la privada, cuidado:**

- **NO la pegues en este chat.** Guardala en `~/buzz-admin-identidad.txt` con
  permisos `600` (`umask 077` antes de escribir el archivo).
- Decime sólo la **pública**, que no es secreta.

Después dale el rol de admin en el relay:

```bash
docker exec buzz-prod-relay-1 buzz-admin add-member \
  --pubkey <LA_PUBLICA_QUE_GENERASTE> --role admin
docker exec buzz-prod-relay-1 buzz-admin list-members
```

Cuando termines, avisame que la clave privada quedó en ese archivo: la voy a
buscar por SSH para importarla en mi Buzz de escritorio y guardarla donde
corresponde. **Esa clave es el control del relay: si se pierde, se pierde la
administración; si se filtra, cualquiera es admin.**

## Paso 4 — Las piezas

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # si no está uv
git clone https://github.com/LucasC1999-ctrl/piezas-para-agentes ~/piezas
cd ~/piezas

for p in vault-mcp wallet-mcp planilla-watch; do
  (cd packages/$p && uv venv && uv pip install -e ".[dev]" && \
   .venv/bin/python -m pytest tests/ -q)
done
```

Tienen que dar **21**, **46** y **17** tests en verde. Si alguno falla, pará y
mostrame la salida completa — no lo arregles por tu cuenta.

Después leé `ESTADO.md` del repo: tiene el diseño, las decisiones tomadas y las
trampas conocidas. No cambies decisiones que estén ahí sin consultarme.

## Paso 5 — Que sobreviva a un reinicio

```bash
sudo systemctl enable docker
docker compose -f compose.yml -f compose.caddy.yml config | grep -c "restart: unless-stopped"
```

Después reiniciá el VPS y comprobá que el relay vuelve solo. Un servidor que
hay que levantar a mano después de cada reinicio no sirve para un estudio.

## Reglas para todo el trabajo

- **Sin secretos en el chat.** Si generás claves, dejalas en `.env` con
  permisos `600` y decime sólo que las generaste. Nunca las pegues acá.
- **`.env` nunca va a git.** Verificá que esté en `.gitignore` antes de
  commitear nada.
- **Verificá con un comando, no con optimismo.** "Debería andar" no vale:
  mostrame la salida que lo comprueba.
- **Si algo falla dos veces igual, pará y explicame** qué probaste y qué
  devolvió, en vez de seguir intentando variantes.
- **No abras puertos al mundo** más allá de 80 y 443. El 3000 queda sólo
  interno, detrás de Caddy.

## Cuando termines

Escribime un resumen con:
- versión de Buzz que quedó corriendo (`docker compose images`)
- el resultado de las tres verificaciones (TLS, WebSocket 101, tests)
- qué quedó pendiente o dudoso

---

## Lo que este prompt NO hace, y hay que decidir después

- **El bot de Hermes** (`estudio-suite`) — es un repo aparte y su compose todavía
  pasa variables `MM_*` de Mattermost en vez de las `BUZZ_*` que el código
  realmente lee. Hay que arreglarlo antes de desplegarlo.
- **Backups** del volumen de Postgres. Sin esto, un VPS que se pierde se lleva
  los canales del estudio.
- **El vigilante de la planilla** — necesita acceso al OneDrive del estudio
  (rclone), que hoy está desconectado.
