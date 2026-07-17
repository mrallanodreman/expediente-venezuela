#!/usr/bin/env python3
"""Expediente Venezuela — Investigador MCP
Investiga eventos de cronologia.json usando web search, curioso OSINT y herramientas de reconnaissance.
Genera hallazgos enriquecidos para cada evento.
"""
import json, sys, os, subprocess, re, time, hashlib
from datetime import datetime
from pathlib import Path

# Paths
CHRONOLOGY_FILE = "/home/pctorre/expediente-venezuela/scraper/data/cronologia.json"
FINDINGS_DIR = "/home/pctorre/expediente-venezuela/investigacion"
CURIOSO_BASE = "/mnt/sdb1/FkSociety/CuRioso"
CURIOSO_FINDINGS = os.path.join(CURIOSO_BASE, "findings", "scan-results.json")
CURIOSO_DB = os.path.join(CURIOSO_BASE, "findings", "curioso_db.json")
TOOLS_DIR = os.path.join(CURIOSO_BASE, "tools")
TORIFY = os.path.join(TOOLS_DIR, "torify.py")

os.makedirs(FINDINGS_DIR, exist_ok=True)

# Category context for investigation
CATEGORY_CONTEXT = {
    "corrupcion": {
        "search_terms": ["corrupción Venezuela", "desvío fondos públicos", "malversación", "peculado", "cohecho"],
        "entities": ["ministerio", "gobernación", "alcaldía", "pdvsa", "corpoelec", "banco de venezuela"],
        "legal": ["MP", "fiscalía", "contraloría", "TSJ"]
    },
    "abuso": {
        "search_terms": ["abuso de poder Venezuela", "exceso de fuerza", "arbitrariedad", "violación derechos"],
        "entities": ["PNB", "GNB", "SEBIN", "DGCIM", "militar", "policial"],
        "legal": ["DDHH", "CIDH", "ONU", "fiscalía"]
    },
    "servicios": {
        "search_terms": ["desabastecimiento Venezuela", "corte de luz", "falla agua", "servicios públicos colapso"],
        "entities": ["corpoelec", "hidrocapital", "bandes", "cantv", "corpoguayana"],
        "legal": ["ministerio de energía", "ministerio de salud"]
    },
    "salud": {
        "search_terms": ["crisis hospitalaria Venezuela", "falta medicinas", "mortalidad materna", "epidemia Venezuela"],
        "entities": ["hospital", "ambulatorio", "minsa", "IVSS", "IPASME"],
        "legal": ["ministerio de salud", "OPS", "OMS"]
    },
    "censura": {
        "search_terms": ["censura internet Venezuela", "bloqueo páginas", "restrictión información", "shutdown internet"],
        "entities": ["CANTV", "NetUno", "inter", "Conatel", "Movilnet"],
        "legal": ["Conatel", "Ley Respeto", "amparo"]
    },
    "represion": {
        "search_terms": ["represión protesta Venezuela", "detención arbitraria", "tortura", "desaparición forzada"],
        "entities": ["PNB", "GNB", "SEBIN", "DGCIM", "FAES", "colectivo"],
        "legal": ["DDHH", "Foro Penal", "PROVEA", "CIDH"]
    },
    "persecucion": {
        "search_terms": ["persecución política Venezuela", "persecución judicial", "instrumentalización judicial", "preso político"],
        "entities": ["TSJ", "fiscalía", "SEBIN", "DGCIM"],
        "legal": ["Foro Penal", "DDHH", "oposición"]
    },
    "extorsion": {
        "search_terms": ["extorsión funcionario público Venezuela", "cobro ilícito", "coima", "matraca"],
        "entities": ["funcionario", "municipio", "gobernación", "policía"],
        "legal": ["fiscalía", "contraloría", "MP"]
    }
}

def load_chronology():
    if not os.path.exists(CHRONOLOGY_FILE):
        return {"events": []}
    with open(CHRONOLOGY_FILE) as f:
        return json.load(f)

def save_chronology(data):
    with open(CHRONOLOGY_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_findings(event_id):
    path = os.path.join(FINDINGS_DIR, f"{event_id}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"event_id": event_id, "findings": [], "web_results": [], "curioso_hits": [], "entities_found": [], "updated": None}

def save_findings(event_id, data):
    data["updated"] = datetime.now().isoformat()
    path = os.path.join(FINDINGS_DIR, f"{event_id}.json")
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_curioso_findings():
    if not os.path.exists(CURIOSO_FINDINGS):
        return []
    with open(CURIOSO_FINDINGS) as f:
        return json.load(f)

def search_curioso_for_event(event):
    """Search curioso findings database for matches related to an event"""
    findings = load_curioso_findings()
    matches = []
    event_text = json.dumps(event).lower()
    
    for finding in findings:
        finding_text = json.dumps(finding).lower()
        # Check for keyword overlaps
        keywords = re.findall(r'\b\w{4,}\b', event_text)
        score = sum(1 for kw in keywords if kw in finding_text)
        if score >= 3:
            matches.append({
                "finding": finding,
                "relevance_score": score,
                "match_type": "keyword_overlap"
            })
    
    return sorted(matches, key=lambda x: x["relevance_score"], reverse=True)[:10]

def search_web_for_event(event):
    """Generate web search queries for an event"""
    cat = event.get("category", "general")
    ctx = CATEGORY_CONTEXT.get(cat, {})
    queries = []
    
    # Base query from title
    title = event.get("title", "")
    if title:
        queries.append(title[:100])
    
    # Category-specific queries
    for term in ctx.get("search_terms", [])[:2]:
        queries.append(term)
    
    # Location-based
    location = event.get("location", "")
    if location and location != "Venezuela":
        queries.append(f"{location} {cat}")
    
    # Entity queries
    for entity in ctx.get("entities", [])[:2]:
        queries.append(f"{entity} {cat} Venezuela")
    
    return queries[:5]

def extract_entities_from_event(event):
    """Extract mentioned entities from event"""
    text = json.dumps(event).lower()
    entities = []
    
    # Common Venezuelan institutions
    institutions = [
        "PNB", "GNB", "SEBIN", "DGCIM", "FAES", "militar", "policial",
        "ministerio", "gobernación", "alcaldía", "TSJ", "fiscalía",
        "contraloría", "MP", "pdvsa", "corpoelec", "bandes", "cantv",
        "hospital", "minsa", "IVSS", "Conatel", "colectivo",
        "ejército", "marina", "aviación", "guardia nacional"
    ]
    
    for inst in institutions:
        if inst.lower() in text:
            entities.append(inst)
    
    # Names (basic pattern)
    names = re.findall(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', json.dumps(event))
    entities.extend(list(set(names))[:5])
    
    return list(set(entities))

def enrich_event_with_curioso(event):
    """Enrich event data with curioso OSINT findings"""
    event_id = event.get("id", "")
    
    # Load existing findings
    existing = load_findings(event_id)
    
    # Search curioso
    curioso_matches = search_curioso_for_event(event)
    
    # Generate search queries
    web_queries = search_web_for_event(event)
    
    # Extract entities
    entities = extract_entities_from_event(event)
    
    # Build enriched data
    enriched = {
        "event_id": event_id,
        "original_title": event.get("title", ""),
        "category": event.get("category", ""),
        "date": event.get("date", ""),
        "curioso_matches": curioso_matches,
        "web_search_queries": web_queries,
        "entities_detected": entities,
        "investigation_status": "pending",
        "findings": existing.get("findings", []),
        "web_results": existing.get("web_results", []),
        "recommendations": [],
        "updated": datetime.now().isoformat()
    }
    
    # Add recommendations based on category
    cat = event.get("category", "")
    ctx = CATEGORY_CONTEXT.get(cat, {})
    enriched["recommendations"] = [
        f"Buscar en {inst} registros relacionados"
        for inst in ctx.get("legal", [])[:3]
    ]
    
    # Save findings
    save_findings(event_id, enriched)
    
    return enriched

def generate_investigation_report(event_id=None):
    """Generate a summary report of all investigations"""
    reports = []
    
    if event_id:
        path = os.path.join(FINDINGS_DIR, f"{event_id}.json")
        if os.path.exists(path):
            with open(path) as f:
                reports.append(json.load(f))
    else:
        for fname in sorted(os.listdir(FINDINGS_DIR)):
            if fname.endswith('.json'):
                with open(os.path.join(FINDINGS_DIR, fname)) as f:
                    reports.append(json.load(f))
    
    # Summary
    total = len(reports)
    with_curioso = sum(1 for r in reports if r.get("curioso_matches"))
    with_entities = sum(1 for r in reports if r.get("entities_detected"))
    pending = sum(1 for r in reports if r.get("investigation_status") == "pending")
    
    return {
        "total_events": total,
        "with_curioso_matches": with_curioso,
        "with_entities": with_entities,
        "pending_investigation": pending,
        "reports": reports[:20]  # Limit output
    }

def get_event_connections(event_id):
    """Find connections between events"""
    data = load_chronology()
    events = data.get("events", [])
    
    target = None
    for ev in events:
        if ev.get("id") == event_id:
            target = ev
            break
    
    if not target:
        return {"error": f"Event {event_id} not found"}
    
    connections = []
    target_text = json.dumps(target).lower()
    
    for ev in events:
        if ev.get("id") == event_id:
            continue
        
        ev_text = json.dumps(ev).lower()
        
        # Check category match
        if ev.get("category") == target.get("category"):
            connections.append({
                "event_id": ev.get("id"),
                "title": ev.get("title", ""),
                "connection_type": "misma_categoría",
                "date": ev.get("date", "")
            })
        
        # Check location match
        if ev.get("location") == target.get("location") and ev.get("location"):
            connections.append({
                "event_id": ev.get("id"),
                "title": ev.get("title", ""),
                "connection_type": "misma_ubicación",
                "date": ev.get("date", "")
            })
        
        # Check entity overlap
        target_entities = set(extract_entities_from_event(target))
        ev_entities = set(extract_entities_from_event(ev))
        overlap = target_entities & ev_entities
        if overlap:
            connections.append({
                "event_id": ev.get("id"),
                "title": ev.get("title", ""),
                "connection_type": "entidades_compartidas",
                "shared_entities": list(overlap),
                "date": ev.get("date", "")
            })
    
    return {
        "event_id": event_id,
        "total_connections": len(connections),
        "connections": connections
    }


# ─── MCP Server ──────────────────────────────────────────────────────────────

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "expediente-investigador",
    instructions="Investigador OSINT de la cronología de Expediente Venezuela. Usa curioso, web search y análisis de entidades."
)

@mcp.tool()
def investigar_evento(event_id: str) -> str:
    """Investiga un evento específico de la cronología. Retorna hallazgos de curioso, queries de búsqueda y entidades detectadas."""
    data = load_chronology()
    events = data.get("events", [])
    
    event = next((e for e in events if e.get("id") == event_id), None)
    if not event:
        return json.dumps({"error": f"Evento {event_id} no encontrado"})
    
    enriched = enrich_event_with_curioso(event)
    return json.dumps(enriched, ensure_ascii=False, indent=2)

@mcp.tool()
def listar_eventos(categoria: str = None, limit: int = 25) -> str:
    """Lista todos los eventos de la cronología. Opcionalmente filtra por categoría."""
    data = load_chronology()
    events = data.get("events", [])
    
    if categoria:
        events = [e for e in events if e.get("category") == categoria]
    
    events = events[:limit]
    
    summary = []
    for ev in events:
        summary.append({
            "id": ev.get("id"),
            "title": ev.get("title", "")[:80],
            "date": ev.get("date", "")[:10],
            "category": ev.get("category"),
            "impact": ev.get("impact"),
            "location": ev.get("location", "")
        })
    
    return json.dumps({"total": len(summary), "events": summary}, ensure_ascii=False, indent=2)

@mcp.tool()
def buscar_en_curioso(query: str) -> str:
    """Busca en la base de datos de curioso (hallazgos .gob.ve) para encontrar matches relacionados."""
    findings = load_curioso_findings()
    
    query_lower = query.lower()
    keywords = re.findall(r'\b\w{3,}\b', query_lower)
    
    matches = []
    for finding in findings:
        finding_text = json.dumps(finding).lower()
        score = sum(1 for kw in keywords if kw in finding_text)
        if score >= 2:
            matches.append({
                "finding_id": finding.get("id", finding.get("domain", "")),
                "domain": finding.get("domain", ""),
                "severity": finding.get("severity", ""),
                "relevance_score": score,
                "summary": str(finding.get("finding", finding.get("detail", "")))[:200]
            })
    
    matches.sort(key=lambda x: x["relevance_score"], reverse=True)
    
    return json.dumps({
        "query": query,
        "total_matches": len(matches),
        "matches": matches[:15]
    }, ensure_ascii=False, indent=2)

@mcp.tool()
def generar_reporte(event_id: str = None) -> str:
    """Genera un reporte consolidado de investigaciones. Sin event_id genera un reporte global."""
    report = generate_investigation_report(event_id)
    return json.dumps(report, ensure_ascii=False, indent=2)

@mcp.tool()
def conexiones_evento(event_id: str) -> str:
    """Encuentra conexiones entre un evento y otros eventos de la cronología."""
    connections = get_event_connections(event_id)
    return json.dumps(connections, ensure_ascii=False, indent=2)

@mcp.tool()
def investigar_todos() -> str:
    """Investiga TODOS los eventos de la cronología de forma masiva. Retorna resumen."""
    data = load_chronology()
    events = data.get("events", [])
    
    results = []
    for ev in events:
        enriched = enrich_event_with_curioso(ev)
        results.append({
            "id": ev.get("id"),
            "title": ev.get("title", "")[:60],
            "curioso_matches": len(enriched.get("curioso_matches", [])),
            "entities": enriched.get("entities_detected", []),
            "queries": enriched.get("web_search_queries", [])[:2]
        })
    
    return json.dumps({
        "total_investigated": len(results),
        "with_curioso": sum(1 for r in results if r["curioso_matches"] > 0),
        "results": results
    }, ensure_ascii=False, indent=2)

@mcp.tool()
def buscar_conexion_curioso(event_id: str, dominio_gob_ve: str) -> str:
    """Busca si un dominio .gob.ve está conectado a un evento específico de la cronología."""
    data = load_chronology()
    events = data.get("events", [])
    
    event = next((e for e in events if e.get("id") == event_id), None)
    if not event:
        return json.dumps({"error": f"Evento {event_id} no encontrado"})
    
    # Search curioso for this domain
    findings = load_curioso_findings()
    domain_matches = [f for f in findings if dominio_gob_ve.lower() in json.dumps(f).lower()]
    
    # Check if domain relates to event category
    event_entities = extract_entities_from_event(event)
    event_text = json.dumps(event).lower()
    domain_relates = dominio_gob_ve.lower() in event_text
    
    return json.dumps({
        "event_id": event_id,
        "domain": dominio_gob_ve,
        "domain_in_event_context": domain_relates,
        "curioso_findings": len(domain_matches),
        "findings": domain_matches[:5],
        "event_entities": event_entities
    }, ensure_ascii=False, indent=2)

@mcp.tool()
def resumen_investigacion() -> str:
    """Resumen ejecutivo de todas las investigaciones realizadas."""
    findings_files = [f for f in os.listdir(FINDINGS_DIR) if f.endswith('.json')]
    
    total_findings = 0
    total_curioso = 0
    total_entities = 0
    categories = {}
    
    for fname in findings_files:
        with open(os.path.join(FINDINGS_DIR, fname)) as f:
            data = json.load(f)
        
        cat = data.get("category", "general")
        categories[cat] = categories.get(cat, 0) + 1
        total_findings += len(data.get("findings", []))
        total_curioso += len(data.get("curioso_matches", []))
        total_entities += len(data.get("entities_detected", []))
    
    return json.dumps({
        "total_events_investigated": len(findings_files),
        "total_curioso_matches": total_curioso,
        "total_entities_detected": total_entities,
        "categories_distribution": categories,
        "findings_dir": FINDINGS_DIR
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
