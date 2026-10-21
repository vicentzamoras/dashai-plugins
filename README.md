# dashai-plugins (índice de prueba)

Índice de la tienda de plugins de dashAI. **No guarda plugins:** cada archivo de `plugins/`
apunta al repositorio de GitHub de un plugin. `index.json` es lo que dashAI descarga.

`index.json` lo genera el workflow **Build index** (`scripts/validate.py`) al hacer merge,
cada 6 horas y a mano (*Actions → Build index → Run workflow*; con *full* revisa todo de
nuevo, para cuando cambian las reglas). Lee el estado y las releases de todos los repos con
GraphQL, 100 repos por consulta, y revisa solo lo nuevo (reusa una release cuyo archivo o
commit no cambió). Solo commitea si cambia lo que el índice lista; `built_at` es cuándo
cambió por última vez. El archivo va compacto (sin sangría). Cada PR pasa por el
workflow **Check**. Los dos revisan lo que se puede sin dashAI: la entrada, el repo (público,
no archivado, licencia), las releases (archivo, tamaño, atestación, `README.md` en la raíz del
commit) y el manifiesto (campos,
`id`, versión igual al tag, tipos, que cada clase exista). Los dos revisan además que los
nombres de los componentes de una release nueva no sean los de otro plugin de la tienda ni los
de dashAI (lee su `get_initial_components` de las ramas `production` y `develop`); una release
ya publicada no se vuelve a revisar contra nombres que dashAI agregue después. Lo que necesita dashAI
corriendo (dependencias, cargar el código) lo vuelve a revisar dashAI al instalar.

Si una release nueva (o el plugin entero) queda fuera, **Build index** se lo avisa a su autor en
un issue de este repo que lo menciona.

- `plugins/<id>.json`: una entrada por plugin (`id` y `repo`).
- `removed.json`: plugins retirados, con el motivo.
- `index.json`: lo genera **Build index**; no se edita a mano.
- `schema/`: el formato de cada archivo (JSON Schema).
- `scripts/validate.py` y `.github/workflows/`: el bot (su test: `python -m unittest discover scripts`).

Para publicar un plugin, ver [CONTRIBUTING.md](CONTRIBUTING.md).
