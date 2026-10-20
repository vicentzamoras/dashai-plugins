# Publicar un plugin en la tienda

## Antes de abrir el PR

1. Tu plugin cumple la guía de dashAI ("Migrating a PyPI plugin", pasos 1 a 4): un
   `manifest.json` válido y una release de GitHub con un solo `.zip`, idealmente construido
   y atestado por un workflow.
2. La `version` del manifiesto es igual al tag de la release (`v0.1.0` → `0.1.0`).
3. El repositorio es público, tiene una licencia de esta lista (MIT, Apache 2.0, BSD 2/3,
   GPLv3, LGPLv3, Unlicense) y un `README.md` en la raíz, con ese nombre exacto, en el commit
   de cada release: dashAI lo muestra en la tienda desde ahí.
4. El `id` no contiene `dashai`.
5. Los nombres de clase de tus componentes no son los de un componente de dashAI ni los de otro
   plugin de la tienda: dashAI identifica cada componente por el nombre de su clase, y dos no
   pueden llamarse igual. Usa un prefijo propio (por ejemplo `KitAccuracy`, no `Accuracy`).
6. Lo probaste instalándolo en dashAI desde la URL de tu repositorio.

## El PR

- Un plugin por PR.
- Agrega `plugins/<id>.json`:

  ```json
  { "id": "mi-plugin", "repo": "tu-usuario/mi-plugin" }
  ```

- Tienes que ser el dueño del repositorio (o, si es de una organización, un miembro público).

El check **Check** revisa tu entrada y tus releases; si falla, su resumen dice por qué.
Después una persona del equipo revisa el PR y hace merge, y **Build index** publica tu plugin
en `index.json`. Las versiones nuevas no necesitan otro PR: se recogen cada día.

## Retirar tu plugin

Abre un PR que borre `plugins/<id>.json` y agregue a `removed.json` una entrada con
`"type": "withdrawn"` y el motivo. El `id` queda reservado.
