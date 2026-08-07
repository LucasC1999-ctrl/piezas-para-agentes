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

## Paso 2 — Generar la identidad del relay (ANTES de levantar nada)

Este paso va primero por un deadlock real: con `BUZZ_REQUIRE_RELAY_MEMBERSHIP=true`
el relay valida `RELAY_OWNER_PUBKEY` **antes de arrancar** y aborta si falta. Pero
si generaras la clave con `docker exec` necesitarías el relay ya corriendo. Cada
paso esperaría al otro.

Se rompe generando la clave sin el relay:

```bash
git clone https://github.com/block/buzz.git ~/buzz-src
cd ~/buzz-src/deploy/compose

# --entrypoint es obligatorio: el ENTRYPOINT de la imagen es buzz-relay, así que
# sin sobrescribirlo los argumentos se los come el relay e intenta arrancar.
umask 077
docker run --rm --entrypoint buzz-admin ghcr.io/block/buzz:main generate-key \
  > ~/buzz-admin-identidad.txt
chmod 600 ~/buzz-admin-identidad.txt
```

Ese archivo tiene la clave pública y la privada.

- **NO pegues la privada en el chat.** Ya está en el archivo con permisos 600.
- Decime sólo la **pública**, que no es secreta.

Hace falta además una clave para el relay mismo (distinta de la del dueño):

```bash
docker run --rm --entrypoint buzz-admin ghcr.io/block/buzz:main generate-key \
  > ~/buzz-relay-identidad.txt
chmod 600 ~/buzz-relay-identidad.txt
```

## Paso 3 — El `.env`

**NO hagas `cp .env.example .env`.** Escribí el archivo de cero.

Motivo: el ejemplo trae valores `CHANGE_ME_...`, y el `${VAR:?}` de Compose sólo
falla si la variable está vacía o no existe — con `CHANGE_ME` **no se dispara**.
Resultado: el stack levanta feliz con `CHANGE_ME_RANDOM_PASSWORD` como contraseña
de Postgres. Falla en silencio, que es el modo peligroso.

```bash
cat > .env <<'EOF'
# Pineá el digest en vez de :main, así lo que corre es lo que auditaste.
BUZZ_IMAGE=ghcr.io/block/buzz:main

# ── LA VARIABLE MÁS IMPORTANTE ──────────────────────────────────────────────
# Se llama RELAY_URL, NO BUZZ_RELAY_URL.
#   crates/buzz-relay/src/config.rs:535 → std::env::var("RELAY_URL")
# `BUZZ_RELAY_URL` existe en el repo pero la leen buzz-dev-mcp y los test
# clients, nunca el relay. Peor: el mensaje de error del propio relay
# (main.rs:265) nombra BUZZ_RELAY_URL, que no es la que lee.
#
# Y como es un unwrap_or_else, escribirla mal NO hace ruido: cae al default
# ws://localhost:3000, la comunidad se crea con ese host, el HTTP responde 200
# normal, y sólo el WebSocket devuelve 404. Es la falla más difícil de ver de
# toda esta instalación.
RELAY_URL=wss://buzz.ovdianlabs.com
BUZZ_DOMAIN=buzz.ovdianlabs.com

BUZZ_MEDIA_BASE_URL=https://buzz.ovdianlabs.com
BUZZ_MEDIA_SERVER_DOMAIN=buzz.ovdianlabs.com
BUZZ_CORS_ORIGINS=https://buzz.ovdianlabs.com

BUZZ_REQUIRE_AUTH_TOKEN=true
BUZZ_REQUIRE_RELAY_MEMBERSHIP=true
BUZZ_ALLOW_NIP_OA_AUTH=true
BUZZ_AUTO_MIGRATE=true
BUZZ_GIT_CONFORMANCE_PROBE=true
RUST_LOG=info

# Del Paso 2. La pública del dueño, y la PRIVADA del relay.
# La privada del relay tiene que ser estable: con una efímera, cada reinicio
# invalida las firmas NIP-43 anteriores.
RELAY_OWNER_PUBKEY=<pubkey del archivo buzz-admin-identidad.txt>
BUZZ_RELAY_PRIVATE_KEY=<privada del archivo buzz-relay-identidad.txt>
BUZZ_GIT_HOOK_HMAC_SECRET=<openssl rand -hex 32>

POSTGRES_DB=buzz
POSTGRES_USER=buzz
POSTGRES_PASSWORD=<openssl rand -hex 32>
REDIS_PASSWORD=<openssl rand -hex 32>

BUZZ_S3_ACCESS_KEY=<openssl rand -hex 32>
BUZZ_S3_SECRET_KEY=<openssl rand -hex 32>
BUZZ_S3_BUCKET=buzz-media
BUZZ_S3_ADDRESSING_STYLE=path

BUZZ_HTTP_PORT=3000
CADDY_HTTP_PORT=80
CADDY_HTTPS_PORT=443
EOF
chmod 600 .env
```

Verificá que no haya quedado ningún placeholder antes de seguir:

```bash
grep -nE "CHANGE_ME|<.*>" .env && echo "FALTAN VALORES" || echo "env completo"
```

Las cinco que el prompt viejo omitía y son obligatorias: `POSTGRES_PASSWORD`,
`REDIS_PASSWORD` (Compose no renderiza sin ellas), `BUZZ_DOMAIN` (sin esto Caddy
no tiene nombre de sitio y no hay TLS), y `RELAY_OWNER_PUBKEY` +
`BUZZ_RELAY_PRIVATE_KEY` (el relay aborta al arrancar).

## Paso 3b — Levantar

```bash
docker compose -f compose.yml -f compose.caddy.yml up -d
docker compose ps
docker compose logs relay | grep -i "community ensured"
```

Esa última línea es la que confirma todo. Tiene que decir
`host: buzz.ovdianlabs.com`. **Si dice `localhost:3000`, escribiste
`BUZZ_RELAY_URL` en vez de `RELAY_URL`** — corregilo, `docker compose down` y
volvé a levantar.

Verificá el TLS y el WebSocket desde afuera:

```bash
curl -sI https://buzz.ovdianlabs.com | head -3

# --http1.1 NO es opcional: sin eso curl negocia HTTP/2, donde el upgrade con
# "Connection: Upgrade" no existe, y devuelve 200 — un falso OK que hace creer
# que el WebSocket anda cuando no se probó nada.
curl -s -o /dev/null -w "%{http_code}\n" --http1.1 \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  https://buzz.ovdianlabs.com/
```

El segundo tiene que dar **101**. Si da 404, es el problema de `RELAY_URL`. Si da
502, el relay no levantó.

Nota sobre el dueño: **no corras `buzz-admin add-member --role admin`**. El dueño
se define por `RELAY_OWNER_PUBKEY` en el `.env`, que es más fuerte que admin —
agregarlo como admin sería degradarlo. `buzz-admin` de hecho rechaza el rol owner
a propósito.

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
