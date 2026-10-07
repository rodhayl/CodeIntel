# CodeIntel · laboratorio offline de contexto de código

[English](README.md) · [Recorrido](docs/QUICKSTART.md) · [Diseño](docs/portfolio/DESIGN.md) · [Validación](docs/portfolio/VALIDATION.md)

CodeIntel indexa código local y devuelve fragmentos literales con rutas, rangos, hashes y un presupuesto del JSON completo. La verificación rechaza cambios en los archivos seleccionados. La interfaz mantenida es `codeintel lab {index,query,verify,demo,evaluate}`. Usa Python, tree-sitter y SQLite/FTS5; no necesita modelo, API key ni GPU.

## Instalar y probar

Empieza en una copia limpia de [este repositorio sin historial heredado](https://github.com/rodhayl/CodeIntel) o en su archivo de fuentes revisado. [Identidad y acceso a las fuentes](docs/portfolio/SOURCE_ACCESS.md#get-the-identified-source) explica cómo verificar la versión elegida. Para instalar estos archivos no necesitas acceso al repositorio privado de desarrollo original. Los recibos antiguos no certifican cambios posteriores.

El entorno validado es **CPython 3.12 en Linux x86_64**. Conserva los dos archivos de versiones fijadas para reproducirlo; no contienen hashes de paquetes ni garantizan portabilidad a otras plataformas. Actualizar dependencias exige revisar compatibilidad y repetir la aceptación. Setuptools está fijado para construir sin aislamiento. La preparación usa PyPI y el CLI instalado funciona offline.

```bash
CHECKOUT="$(pwd -P)"
WORK="$(mktemp -d /tmp/codeintel-review.XXXXXX)"
python3.12 -m venv "$WORK/venv"
PYTHON="$WORK/venv/bin/python"
CODEINTEL="$WORK/venv/bin/codeintel"
"$PYTHON" -m pip install --index-url https://pypi.org/simple -r "$CHECKOUT/requirements-lab.lock"
"$PYTHON" -m pip install --no-index --no-deps --no-build-isolation "$CHECKOUT"
cd "$WORK"
"$PYTHON" -I -c 'import codeintel; print(codeintel.__file__)'
"$CODEINTEL" lab demo --workspace "$WORK/demo" --format text
"$CODEINTEL" lab evaluate --format text
```

La demo crea cuatro archivos sintéticos, consulta `reserve_seats`, aplica un cambio programado, rechaza el paquete obsoleto y consulta de nuevo. No descubre ni corrige el fallo por sí sola. La evaluación tiene cuatro casos escritos a mano, dos alternativas y tres repeticiones, además de diez comprobaciones de contrato. El control literal usa los mismos fragmentos indexados; no es ripgrep ni una evaluación independiente de agentes.

[Salida de la demo](docs/portfolio/DEMO.txt) · [Evaluación](docs/portfolio/EVALUATION.txt) · [Tu repositorio](docs/QUICKSTART.md) · [Código de CodeIntel](docs/portfolio/REAL_SOURCE_CURRENT.md)

## Límites importantes

- Los paquetes contienen código exacto sin ocultar secretos automáticamente. Mantén su privacidad y guarda estado y recibos fuera del repositorio inspeccionado.
- El JSON con salto de línea cabe en 1.024–8.192 bytes; por defecto, 4.096. Los alias conservan ubicaciones verificables del texto repetido. La expansión de símbolos aplica una reserva conservadora de 64 bytes y puede dejar cobertura parcial aunque otra serialización completa cupiera.
- Verificar comprueba los archivos seleccionados en ese momento. No autentica al productor ni garantiza relevancia, dependencias completas o que un `EMPTY` antiguo siga siendo completo. Lee el rango registrado y los archivos que definen las dependencias.
- Python, JavaScript y TypeScript tienen análisis estructural. Otros textos UTF-8 admitidos usan fragmentación alternativa, sin semántica equivalente. Los cambios reconstruyen la instantánea completa.
- No se distribuyen integración con agentes, proveedores, búsqueda densa, ejecutor SCIP ni GUI. No se ha demostrado ahorro actual de tokens, coste o tiempo, ni mejores respuestas. El [resumen histórico](docs/portfolio/HISTORY.md) conserva resultados favorables y adversos de sistemas retirados.

## Desarrollo y distribución

Instala `requirements-lab-dev.lock` en un venv y después el proyecto sin resolver dependencias ni aislar la construcción. `python -m pytest tests` ejecuta la suite retenida; consulta el [procedimiento completo](docs/portfolio/VALIDATION.md) para comparar fuente limpia y wheel instalado.

El wheel contiene runtime y cuatro fixtures. El sdist es fuente de construcción, sin pruebas ni herramientas de revisión; usa su [receta](README.package.md). El archivo curado añade documentación técnica, pruebas y herramientas seleccionadas. Los artículos, material personal y recibos históricos se conservan por separado.

La fuente actual tiene licencia [MIT](LICENSE) y [avisos de terceros](THIRD_PARTY_NOTICES.md). El repositorio original de desarrollo `rodhayl/CodeIntelPriv` y sus objetos Git históricos permanecen privados: no se incluyen aquí ni se relicencian retroactivamente. Un candidato limpio no implica publicación ni preparación para producción. [Alcance de licencia](docs/portfolio/LICENSING.md).
