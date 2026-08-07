# Agregar una segunda comunidad al mismo relay

Prueba para responder una pregunta de negocio: **¿se puede tener una comunidad
por cliente sin un servidor por cliente?**

La idea: un contenedor de relay por comunidad, todos contra la misma Postgres,
Redis y MinIO. Cada relay declara su propio `RELAY_URL`, y como el relay resuelve
la comunidad por el `Host` del pedido, cada uno atiende la suya.

**Esto no está probado.** Se hace sobre un relay en producción; los backups de
Hetzner ya están activos, así que el peor caso es restaurar.

## Paso 1 — DNS

Ya hecho: `ecya.ovdianlabs.com` → `178.105.175.106`, propagado.

## Paso 2 — El override de Compose

En el VPS, en `~/buzz-src/deploy/compose/`:

```bash
cat > compose.ecya.yml <<'EOF'
# Segunda comunidad: ECYA.
#
# Es una copia del servicio `relay` con dos diferencias:
#   - RELAY_URL apunta a ecya.ovdianlabs.com, que es lo que define qué
#     comunidad crea y atiende este contenedor.
#   - No publica puertos al host: sólo lo alcanza Caddy por la red interna.
#
# Comparte Postgres, Redis y MinIO con el relay principal. El aislamiento entre
# comunidades es lógico (community_id en las tablas), no físico.
services:
  relay-ecya:
    image: ${BUZZ_IMAGE:-ghcr.io/block/buzz:main}
    env_file:
      - .env
    environment:
      # Lo único que cambia respecto del relay principal.
      RELAY_URL: wss://ecya.ovdianlabs.com
      BUZZ_MEDIA_BASE_URL: https://ecya.ovdianlabs.com
      BUZZ_MEDIA_SERVER_DOMAIN: ecya.ovdianlabs.com
      BUZZ_CORS_ORIGINS: https://ecya.ovdianlabs.com

      BUZZ_BIND_ADDR: 0.0.0.0:3000
      BUZZ_HEALTH_PORT: "8080"
      BUZZ_METRICS_PORT: "9102"
      DATABASE_URL: postgres://${POSTGRES_USER:-buzz}:${POSTGRES_PASSWORD:?}@postgres:5432/${POSTGRES_DB:-buzz}
      REDIS_URL: redis://:${REDIS_PASSWORD:?}@redis:6379
      BUZZ_S3_ENDPOINT: http://minio:9000
      BUZZ_S3_ACCESS_KEY: ${BUZZ_S3_ACCESS_KEY:?}
      BUZZ_S3_SECRET_KEY: ${BUZZ_S3_SECRET_KEY:?}
      BUZZ_S3_BUCKET: ${BUZZ_S3_BUCKET:-buzz-media}
      BUZZ_GIT_REPO_PATH: /data/git
      # En false: el relay principal ya corrió las migraciones. Dos procesos
      # migrando la misma base a la vez es pedir problemas.
      BUZZ_AUTO_MIGRATE: "false"
      BUZZ_GIT_CONFORMANCE_PROBE: "false"
    volumes:
      - buzz-git-data-ecya:/data/git
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "bash -ec 'exec 3<>/dev/tcp/127.0.0.1/8080; printf \"GET /_readiness HTTP/1.1\\r\\nHost: 127.0.0.1\\r\\nConnection: close\\r\\n\\r\\n\" >&3; grep -q \"200 OK\" <&3'",
        ]
      interval: 10s
      timeout: 3s
      retries: 12
      start_period: 30s
    restart: unless-stopped
    networks:
      - buzz-net

volumes:
  buzz-git-data-ecya:
EOF
```

## Paso 3 — Caddy

El `Caddyfile` actual sólo conoce un sitio. Se le agrega el segundo:

```bash
cp Caddyfile Caddyfile.bak
cat >> Caddyfile <<'EOF'

ecya.ovdianlabs.com {
  encode zstd gzip
  reverse_proxy relay-ecya:3000
}
EOF
cat Caddyfile
```

## Paso 4 — Levantar

Los tres `-f` van juntos en cada comando, si no Compose no ve el servicio nuevo:

```bash
docker compose -f compose.yml -f compose.caddy.yml -f compose.ecya.yml up -d
docker compose -f compose.yml -f compose.caddy.yml -f compose.ecya.yml ps
```

## Paso 5 — Verificar

Lo que hay que mirar, en orden:

```bash
# 1. ¿Creó SU comunidad, con su host?
docker compose -f compose.yml -f compose.caddy.yml -f compose.ecya.yml \
  logs relay-ecya | grep -i "community ensured"
```

Tiene que decir `host: ecya.ovdianlabs.com`. Si dice otra cosa, `RELAY_URL` no
llegó bien.

```bash
# 2. ¿Responde por HTTPS con su propio certificado?
curl -sI https://ecya.ovdianlabs.com | head -3

# 3. ¿El WebSocket da 101? (--http1.1 obligatorio, ver el prompt principal)
curl -s -o /dev/null -w "%{http_code}\n" --http1.1 \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  https://ecya.ovdianlabs.com/
```

```bash
# 4. LO MÁS IMPORTANTE: ¿la comunidad original sigue sana?
bash ~/verificar-buzz.sh
```

Si ese script sigue dando TODO BIEN, las dos conviven.

```bash
# 5. ¿Son dos comunidades distintas en la base, o se pisaron?
docker compose -f compose.yml exec -T postgres \
  psql -U buzz -d buzz -c "SELECT id, host FROM communities ORDER BY host;"
```

Tienen que aparecer **dos filas** con hosts distintos. Si aparece una sola, el
segundo relay se adueñó de la comunidad del primero y hay que revertir.

## Si algo sale mal

```bash
docker compose -f compose.yml -f compose.caddy.yml -f compose.ecya.yml \
  down relay-ecya
cp Caddyfile.bak Caddyfile
docker compose -f compose.yml -f compose.caddy.yml up -d --force-recreate caddy
bash ~/verificar-buzz.sh
```

El relay principal no se toca en ningún momento, así que revertir es sacar lo
que se agregó.

## Qué significa cada resultado

**Si funciona:** una comunidad por cliente cuesta un contenedor liviano —unos
cientos de MB— en vez de un servidor entero. El modelo de negocio cierra con un
solo VPS para varios clientes.

**Si no funciona:** cada cliente necesita su propio stack, y con 3,7 GB entran
dos o tres. Ahí el precio por cliente tiene que cubrir un servidor propio.
