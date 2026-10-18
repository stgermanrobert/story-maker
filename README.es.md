# Story Maker

**Convertí videos horizontales en Stories / Reels verticales de Instagram con fondo desenfocado, con vista previa en vivo, reproducción y una línea de tiempo para cortar al milisegundo.**

[Read in English](README.md)

Story Maker es una app de escritorio para Windows hecha en Python (Tkinter) sobre FFmpeg. Cargás un video (o pegás un link de YouTube), ajustás cómo se ven el fondo y el video principal, elegís el fragmento exacto y exportás un MP4 de 1080×1920 listo para subir.

## Funciones

**Fuentes**
- Descarga desde YouTube hasta 1080p (con [yt-dlp](https://github.com/yt-dlp/yt-dlp)) directo desde la app.
- Carga de cualquier video local. Si no tiene audio (por ejemplo, una descarga de yt-dlp sin unir), detecta solo el archivo de audio que le corresponde.
- Formatos: Story / Reel 9:16 (1080×1920), Feed 4:5 (1080×1350), Cuadrado 1:1, Story 720p.

**Fondo**
- Zoom, posición horizontal / vertical, espejo.
- Desenfoque, brillo, contraste, saturación, viñeta.
- Opacidad sobre un color a elección.

**Video principal**
- Clic sobre la preview para seleccionarlo; después se arrastra para moverlo, esquinas o rueda del mouse para escalar, doble clic lo centra (con guías magnéticas). Clic afuera o `Esc` oculta el recuadro para ver la preview limpia.
- Recorte propio (izquierda / derecha / arriba / abajo).
- Opacidad, esquinas redondeadas, borde con color, sombra.
- Recorte de barras negras incrustadas en la fuente.

**Reproducción y corte**
- Play / pausa con audio, mostrando la composición final.
- Línea de tiempo con forma de onda, zoom y desplazamiento.
- Marcadores de entrada y salida: se arrastran, se marcan en el cursor (`I` / `O`) o se escriben con milisegundos (`1:21.200`).
- Arrastrando la barra violeta sobre la onda (o `Ctrl`+arrastrar dentro de la selección) se mueve el fragmento entero sin cambiar su duración.
- Imán a duraciones de 15, 30 y 60 s y al cursor (`Alt` para arrastrar libre, o se apaga el imán).
- Transporte: inicio del video, inicio del bloque, stop (dos veces = vuelve al inicio del video), play, fin del bloque, fin del video.
- Botones de fragmento de 15 / 30 / 60 s, loop y avance cuadro por cuadro.

**Exportación**
- MP4 H.264 + AAC con `faststart`, CRF / preset / bitrate de audio ajustables.
- Opción de partir en partes de N segundos para subir como varias Stories.
- "Copiar comando" da el comando de FFmpeg equivalente, listo para PowerShell.
- Recuerda los ajustes entre sesiones.

## Atajos de teclado

| Tecla | Acción |
|---|---|
| `Espacio` | Play / pausa |
| `I` / `O` | Marcar entrada / salida en el cursor |
| `←` / `→` | Frame anterior / siguiente |
| `Shift` + `←` / `→` | −1 s / +1 s |
| `Esc` | Stop (dos veces = inicio del video) |
| `Inicio` / `Fin` | Ir al inicio / fin del bloque |
| `Ctrl` + `Inicio` / `Fin` | Ir al inicio / fin del video |
| Rueda sobre la línea de tiempo | Zoom (`Shift` + rueda = desplazar, doble clic = ver todo) |
| Arrastrar la barra violeta / `Ctrl` + arrastrar en la selección | Mover el fragmento entero |
| `Alt` al arrastrar | Sin imán |

## Instalación

### Opción A — Versión portable (recomendada)
Descargá el último `StoryMaker-vX.Y.Z-win64.zip` desde [Releases](../../releases), descomprimilo donde quieras y abrí `StoryMaker.exe`. Trae FFmpeg y yt-dlp incluidos; no hay que instalar nada más.

Para verificar la descarga, compará su hash con `SHA256SUMS.txt` de la misma release:

```powershell
Get-FileHash .\StoryMaker-vX.Y.Z-win64.zip -Algorithm SHA256
```

> Windows SmartScreen puede avisar que la app no está firmada: **Más información → Ejecutar de todos modos**.

### Opción B — Desde el código
Requisitos: Windows 10/11, [Python 3.10+](https://www.python.org/downloads/) (el instalador oficial ya trae Tkinter) y FFmpeg en el `PATH`:

```powershell
winget install --id Gyan.FFmpeg -e
python -m pip install -U yt-dlp      # opcional, para bajar de YouTube
```

Después doble clic en `story_maker.pyw`, o:

```powershell
python story_maker.pyw
```

## Cómo funciona

Todo lo renderiza FFmpeg con un único `filter_complex`: la fuente se divide en dos; una rama se escala hasta cubrir el lienzo, se desenfoca y se ajusta de color (el fondo), y la otra se recorta, se escala y opcionalmente lleva esquinas redondeadas, borde y sombra (el video principal); después se superponen. La preview y la reproducción usan el mismo filtro a menor resolución, así que lo que ves es lo que exportás.

## Licencia

Story Maker se publica bajo la [licencia MIT](LICENSE).
La versión portable incluye software de terceros con sus propias licencias (FFmpeg es GPLv3): ver [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Descargá y publicá solo contenido que tengas derecho a usar.
