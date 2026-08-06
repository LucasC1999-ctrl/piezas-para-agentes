# planilla-watch

Vigila la planilla de credenciales del estudio y avisa al canal de Buzz cuando
algo cambia. **No reemplaza el Excel: lo mira.**

## Para qué

Alguien rota la clave de ARCA un martes y el resto del estudio se entera el
jueves cuando no puede entrar. Eso es lo que esto resuelve, sin que nadie
cambie cómo trabaja.

```
**Cambios en la planilla de credenciales** — Marina

⚠️ 1 credencial cambió — si la usabas, pedila de nuevo.

- Nueva entrada en **Impuestos**: Nuevo Cliente SA · ARCA
- **Impuestos** · Pérez SA · ARCA: cambió clave; se editó otros
```

## Uso

```bash
# archivo local o carpeta sincronizada
planilla-watch --archivo ~/OneDrive/credenciales.xlsx --canal <uuid-del-canal>

# directamente de OneDrive por rclone
planilla-watch --rclone ECYA:Credenciales/cred.xlsx --canal <uuid> --seguir

# ver qué avisaría, sin mandar nada
planilla-watch --archivo cred.xlsx --simular
```

La primera corrida guarda la línea de base y **no avisa nada**: avisar de las
200 filas que ya existían es la forma más rápida de que alguien silencie el
canal el primer día.

## Qué NO publica

**El valor de una clave no sale nunca.** Ni al canal, ni al log, ni al archivo
de estado — de los secretos sólo se guarda un hash. Si el estado se filtra, no
entrega ni una credencial. El aviso dice *que* cambió y *de quién*, nunca
*cuál* es la nueva.

Las columnas se detectan solas: cualquiera que se llame `clave`,
`contraseña`, `password`, `pass`, `pin` o `token` se trata como secreta, sin
importar mayúsculas ni acentos.

## Aguanta la planilla real

- **Reordenar no avisa nada.** Las filas se identifican por su contenido
  (cliente + portal), no por su posición: ordenar alfabéticamente no genera un
  solo mensaje.
- **Cada hoja es una pestaña**, y avisa cuando aparece una nueva.
- **Columnas de más no rompen nada** — agregá `vencimiento` o `responsable`
  cuando quieras.
- Filas vacías, celdas nulas y fórmulas se manejan sin quejarse.
- Dos claves en la misma fila (fiscal y token) se cifran las dos.

## Licencia

Apache-2.0
