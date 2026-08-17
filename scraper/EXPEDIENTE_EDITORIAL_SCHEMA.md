# Contrato editorial de Expediente Venezuela

El frontend trabaja con **expedientes** y cada expediente puede contener **una o muchas evidencias**.

## Expediente

```json
{
  "expediente_id": "EV-2026-0001",
  "titulo": "Título descriptivo del hecho",
  "resumen": "Síntesis neutral del caso",
  "category": "abuso-autoridad",
  "status": "en-investigacion",
  "severity": "high",
  "quien": ["persona, institución o actor"],
  "cuando": "2026-08-16",
  "donde": "lugar o jurisdicción",
  "que": "qué se denuncia o documenta",
  "por_que": "por qué el hecho es relevante para el expediente",
  "como": "cómo habría ocurrido, si está documentado",
  "tags": ["tema", "institución"],
  "relacionados": ["EV-2026-0002"],
  "evidencias": []
}
```

## Evidencia

```json
{
  "evidence_id": "EV-2026-0001-E01",
  "type": "video",
  "url": "https://x.com/usuario/status/123",
  "tweet_id": "123",
  "author": "usuario",
  "text": "Texto o descripción capturada",
  "created_at": "2026-08-16T20:10:00Z",
  "captured_at": "2026-08-17T01:00:00Z",
  "thumbnail_url": "https://.../thumbnail.jpg",
  "video_url": "https://.../video.mp4",
  "source_status": "captured"
}
```

### Reglas

- Un link nuevo **no necesita crear un expediente nuevo**. Si documenta el mismo hecho, debe añadirse a `evidencias` del expediente existente.
- `thumbnail_url` debe capturarse cuando exista una imagen o poster del video. Si existe un archivo de video preservado, `video_url` debe apuntar a ese archivo.
- `relacionados` expresa vínculos editoriales explícitos entre expedientes; no debe inferirse solo porque compartan categoría.
- `quien`, `cuando`, `donde`, `que`, `por_que` y `como` pueden quedar vacíos mientras se investigan; no deben inventarse.
- La presencia de una evidencia no equivale a verificación del hecho. El estado editorial del expediente debe indicarlo.
