# dashai-plugins (índice de prueba)

Índice de la tienda de plugins de dashAI. **No guarda plugins:** cada archivo de `plugins/`
apunta al repositorio de GitHub de un plugin. `index.json` es lo que dashAI descarga.

`index.json` lo genera el workflow **Build index** (`scripts/validate.py`) al hacer merge,
cada día y a mano (*Actions → Build index → Run workflow*). Cada día revisa solo lo nuevo
(reusa una release cuyo archivo o commit no cambió) y los lunes revisa todo de nuevo;
`built_at` es la hora de la última revisión. Cada PR pasa por el
workflow **Check**. Los dos revisan lo que se puede sin dashAI: la entrada, el repo (público,
no archivado, licencia), las releases (archivo, tamaño, atestación, `README.md` en la raíz del
commit) y el manifiesto (campos,
`id`, versión igual al tag, tipos, que cada clase exista). **Check** revisa además que los
nombres de los componentes no sean los de otro plugin de la tienda ni los de dashAI (lee su
`get_initial_components` de las ramas `production` y `develop`). Lo que necesita dashAI
corriendo (dependencias, cargar el código) lo vuelve a revisar dashAI al instalar.

- `plugins/<id>.json`: una entrada por plugin (`id` y `repo`).
- `removed.json`: plugins retirados, con el motivo.
- `index.json`: lo genera **Build index**; no se edita a mano.
- `schema/`: el formato de cada archivo (JSON Schema).
- `scripts/validate.py` y `.github/workflows/`: el bot.

Para publicar un plugin, ver [CONTRIBUTING.md](CONTRIBUTING.md).
