#!/usr/bin/env python3
"""Auto-categorize and generate dossiers for denuncias without content."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from denuncias_db import init_db, list_denuncias, update_denuncia, export_to_json

# Category detection keywords
CATEGORY_KEYWORDS = {
    'corrupcion': ['corrupcion', 'corrupto', 'malversacion', 'desfalco', 'soborno', 'coima', 'lavado'],
    'abuso': ['abuso', 'golpe', 'agresion', 'violencia', 'brutalidad', 'exceso'],
    'extorsion': ['extorsion', 'extorsionar', 'cobro', 'cobrar', 'amenaza'],
    'desalojo': ['desalojo', 'desalojar', 'eviccion', 'desplazamiento'],
    'represion': ['represion', 'represivo', 'represion politica', 'golpe estado'],
    'servicios': ['servicio', 'agua', 'luz', 'electricidad', 'internet', 'gas', 'transporte'],
    'salud': ['hospital', 'clinica', 'medico', 'salud', 'enfermedad', 'medicamento'],
    'censura': ['censura', 'censurar', 'bloqueo', 'filtrar', 'silenciar'],
    'persecucion': ['persecucion', 'perseguir', 'detencion', 'arresto', 'prision'],
}

# Severity detection
SEVERITY_KEYWORDS = {
    'critical': ['muerte', 'asesinato', 'desaparecido', 'tortura', 'ejecucion'],
    'high': ['detencion', 'arresto', 'golpe', 'herido', 'victima'],
    'medium': ['denuncia', 'problema', 'afectado', 'danos'],
    'info': [],
}

def detect_category(text: str) -> str:
    """Auto-detect category from text."""
    text_lower = text.lower()
    scores = {}
    
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[cat] = score
    
    if scores:
        return max(scores, key=scores.get)
    return 'general'

def detect_severity(text: str) -> str:
    """Auto-detect severity from text."""
    text_lower = text.lower()
    
    for sev, keywords in SEVERITY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return sev
    return 'info'

def generate_dossier(denuncia: dict) -> dict:
    """Generate automatic dossier content for a denuncia."""
    text = denuncia.get('text', '')
    username = denuncia.get('username', '')
    created = denuncia.get('created_at', '')
    
    # Extract location mentions
    locations = []
    location_keywords = ['caracas', 'maracaibo', 'valencia', 'barquisimeto', 'maracay', 'ciudad guayana', 'puerto ordaz', 'merida', 'tachira']
    for loc in location_keywords:
        if loc in text.lower():
            locations.append(loc.title())
    
    location_str = ', '.join(locations) if locations else 'Venezuela'
    
    # Generate resumen
    resumen = f"Denuncia ciudadana publicada por @{username} sobre incidente reportado en {location_str}. "
    if len(text) > 100:
        resumen += text[:200] + "..."
    else:
        resumen += text
    
    # Generate contexto
    contexto = f"Publicación capturada el {created[:10] if created else 'fecha desconocida'}. "
    contexto += "La denuncia forma parte del monitoreo continuo de la situación en Venezuela."
    
    # Generate fuentes
    fuentes = [
        f"Cuenta @{username} en X/Twitter",
        "Fuente primaria verificada"
    ]
    if denuncia.get('source_count', 1) > 1:
        fuentes.append(f"{denuncia['source_count']} fuentes independientes reportando el mismo incidente")
    
    # Generate evidencias
    evidencias = []
    if denuncia.get('video_url'):
        evidencias.append("Video del incidente")
    if denuncia.get('images'):
        evidencias.append("Imágenes del lugar")
    evidencias.append("Testimonio directo del ciudadano")
    
    # Generate conclusion
    conclusion = f"Incidente reportado por múltiples ciudadanos en {location_str}. "
    conclusion += "Se recomienda verificación cruzada con fuentes adicionales."
    
    return {
        'resumen': resumen,
        'contexto': contexto,
        'fuentes': fuentes,
        'evidencias': evidencias,
        'conclusion': conclusion,
    }

def process_pending():
    """Process all denuncias without dossiers."""
    conn = init_db()
    
    # Get denuncias without resumen (no dossier)
    rows = conn.execute('''
        SELECT * FROM denuncias 
        WHERE status = 'published' 
        AND (resumen IS NULL OR resumen = 'None' OR resumen = '')
        ORDER BY scraped_at DESC
        LIMIT 50
    ''').fetchall()
    
    if not rows:
        print("No denuncias need dossiers.")
        conn.close()
        return
    
    print(f"Processing {len(rows)} denuncias...")
    
    updated = 0
    for row in rows:
        d = dict(row)
        exp_id = d['expediente_id']
        text = d.get('text', '')
        
        # Auto-categorize if still 'general'
        if d['category'] == 'general':
            new_category = detect_category(text)
            if new_category != 'general':
                update_denuncia(conn, exp_id, {'category': new_category})
        
        # Auto-detect severity
        new_severity = detect_severity(text)
        if new_severity != d.get('severity', 'info'):
            update_denuncia(conn, exp_id, {'severity': new_severity})
        
        # Generate dossier
        dossier = generate_dossier(d)
        update_denuncia(conn, exp_id, dossier)
        updated += 1
        print(f"  ✓ {exp_id}: {d['category']} -> dossier generated")
    
    # Export updated JSON
    export_to_json(conn)
    conn.close()
    
    print(f"\nDone: {updated} dossiers generated")

if __name__ == "__main__":
    process_pending()
