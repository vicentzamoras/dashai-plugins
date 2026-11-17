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
5. Dos componentes de tu plugin no tienen el mismo nombre de clase. Pueden llamarse como uno de
   dashAI o de otro plugin: dashAI registra cada uno como `<id>:Clase` (por ejemplo
   `mi-plugin:Accuracy`).
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
en `index.json`. Las versiones nuevas no necesitan otro PR: el bot las recoge en ≤ 6 horas.

## Si una release nueva no aparece

**Build index** revisa cada release nueva con las mismas reglas del PR. Una release que no pasa
queda fuera del índice; las anteriores siguen publicadas y dashAI sigue ofreciendo la última que
pasó. El bot abre un issue en este repo que te menciona (uno por plugin; comenta solo si el
motivo cambia y se cierra solo cuando una release nueva pasa). El motivo va una línea por release, por ejemplo:

```text
v1.2.0: version 1.1.0 does not match the tag v1.2.0
```

Corrige el motivo y publica una release nueva con una versión mayor. No reemplaces el archivo
de la release que falló: un tag publicado no debería cambiar.

## Retirar tu plugin

Abre un PR que borre `plugins/<id>.json` y agregue a `removed.json` una entrada con
`"type": "withdrawn"` y el motivo. El `id` queda reservado.
