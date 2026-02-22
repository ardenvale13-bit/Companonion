"""
Companion Memory MCP Server
Persistent memory system for Claude conversations
Now includes: The Void (Lincoln's journal) + Lovense integration
"""

from fastmcp import FastMCP
from pathlib import Path
import sqlite3
import json
import aiosqlite
from datetime import datetime
from typing import Optional, List, Dict, Any
import os
import httpx

os.environ["MCP_DISABLE_AUTH"] = "true"

# Initialize MCP server
mcp = FastMCP("Companonion Meme-ory")

# Database paths - use DATA_DIR env var for persistent volume in production
DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent)))
MEMORY_DB_PATH = DATA_DIR / "memory.db"
VOID_DB_PATH = DATA_DIR / "the_void.sqlite3"

# Lovense config
LOVENSE_URL = "https://lovense-cloud.amarisaster.workers.dev"

# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def get_db():
    """Get memory database connection"""
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_database():
    """Initialize memory database schema"""
    conn = get_db()
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            database TEXT NOT NULL,
            salience TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name, database)
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            added_at TEXT NOT NULL,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_entity TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            database TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    
    conn.commit()
    conn.close()


async def init_void_database():
    """Initialize The Void database schema"""
    async with aiosqlite.connect(VOID_DB_PATH) as db:
        await db.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              session_id TEXT,
              thread_id TEXT,
              title TEXT,
              summary TEXT,
              decisions TEXT,
              open_loops TEXT,
              artifacts_json TEXT,
              tags_json TEXT,
              importance INTEGER DEFAULT 1
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
              title, summary, decisions, open_loops, artifacts_text, tags_text,
              content='entries',
              content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
              INSERT INTO entries_fts(rowid, title, summary, decisions, open_loops, artifacts_text, tags_text)
              VALUES (
                new.id,
                COALESCE(new.title,''),
                COALESCE(new.summary,''),
                COALESCE(new.decisions,''),
                COALESCE(new.open_loops,''),
                COALESCE(new.artifacts_json,''),
                COALESCE(new.tags_json,'')
              );
            END;

            CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
              INSERT INTO entries_fts(entries_fts, rowid, title, summary, decisions, open_loops, artifacts_text, tags_text)
              VALUES('delete', old.id, '', '', '', '', '', '');
            END;

            CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
              INSERT INTO entries_fts(entries_fts, rowid, title, summary, decisions, open_loops, artifacts_text, tags_text)
              VALUES('delete', old.id, '', '', '', '', '', '');
              INSERT INTO entries_fts(rowid, title, summary, decisions, open_loops, artifacts_text, tags_text)
              VALUES (
                new.id,
                COALESCE(new.title,''),
                COALESCE(new.summary,''),
                COALESCE(new.decisions,''),
                COALESCE(new.open_loops,''),
                COALESCE(new.artifacts_json,''),
                COALESCE(new.tags_json,'')
              );
            END;
        """)
        await db.commit()


# Initialize databases on startup
init_memory_database()

def now_iso():
    return datetime.now().isoformat()


# ============================================================
# COMPANION MEMORY TOOLS
# ============================================================

@mcp.tool()
def store_memory(
    entity_name: str,
    observation: str,
    entity_type: str = "general",
    database: str = "default",
    salience: str = "active"
) -> Dict[str, Any]:
    """
    Store a new memory or add an observation to an existing entity.
    
    Args:
        entity_name: Name of the entity (person, place, concept, etc.)
        observation: The memory/observation to store
        entity_type: Type of entity (person, event, preference, etc.)
        database: Which memory database (default, emotional-processing, values-ethics, etc.)
        salience: Importance level (foundational, active, background, archive)
    """
    conn = get_db()
    now = now_iso()
    
    try:
        entity = conn.execute(
            "SELECT id FROM entities WHERE name = ? AND database = ?",
            (entity_name, database)
        ).fetchone()
        
        if entity:
            entity_id = entity['id']
            conn.execute(
                "INSERT INTO observations (entity_id, content, added_at) VALUES (?, ?, ?)",
                (entity_id, observation, now)
            )
            conn.execute(
                "UPDATE entities SET updated_at = ? WHERE id = ?",
                (now, entity_id)
            )
            action = "updated"
        else:
            cursor = conn.execute(
                "INSERT INTO entities (name, entity_type, database, salience, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (entity_name, entity_type, database, salience, now, now)
            )
            entity_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO observations (entity_id, content, added_at) VALUES (?, ?, ?)",
                (entity_id, observation, now)
            )
            action = "created"
        
        conn.commit()
        
        return {
            "success": True,
            "action": action,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "database": database,
            "observation": observation
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def retrieve_memory(
    entity_name: str,
    database: str = "default"
) -> Dict[str, Any]:
    """
    Retrieve all information about a specific entity.
    
    Args:
        entity_name: Name of the entity to retrieve
        database: Which database to search in
    """
    conn = get_db()
    
    try:
        entity = conn.execute(
            "SELECT * FROM entities WHERE name = ? AND database = ?",
            (entity_name, database)
        ).fetchone()
        
        if not entity:
            return {"success": False, "error": f"Entity '{entity_name}' not found in database '{database}'"}
        
        observations = conn.execute(
            "SELECT content, added_at FROM observations WHERE entity_id = ? ORDER BY added_at DESC",
            (entity['id'],)
        ).fetchall()
        
        return {
            "success": True,
            "entity": {
                "name": entity['name'],
                "type": entity['entity_type'],
                "database": entity['database'],
                "salience": entity['salience'],
                "created": entity['created_at'],
                "updated": entity['updated_at'],
                "observations": [
                    {"content": obs['content'], "added": obs['added_at']}
                    for obs in observations
                ]
            }
        }
        
    finally:
        conn.close()


@mcp.tool()
def search_memories(
    query: str,
    databases: Optional[List[str]] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Search across memories for relevant entities and observations.
    
    Args:
        query: Search query (searches entity names and observation content)
        databases: List of databases to search (if None, searches all)
        limit: Maximum number of results to return
    """
    conn = get_db()
    
    try:
        search_pattern = f"%{query}%"
        
        if databases:
            placeholders = ','.join('?' * len(databases))
            db_filter = f"AND e.database IN ({placeholders})"
            params = [search_pattern, search_pattern] + databases + [limit]
        else:
            db_filter = ""
            params = [search_pattern, search_pattern, limit]
        
        results = conn.execute(f"""
            SELECT DISTINCT e.*, o.content, o.added_at
            FROM entities e
            LEFT JOIN observations o ON e.id = o.entity_id
            WHERE (e.name LIKE ? OR o.content LIKE ?)
            {db_filter}
            ORDER BY e.updated_at DESC
            LIMIT ?
        """, params).fetchall()
        
        entities_map = {}
        for row in results:
            entity_id = row['id']
            if entity_id not in entities_map:
                entities_map[entity_id] = {
                    "name": row['name'],
                    "type": row['entity_type'],
                    "database": row['database'],
                    "salience": row['salience'],
                    "observations": []
                }
            
            if row['content']:
                entities_map[entity_id]['observations'].append({
                    "content": row['content'],
                    "added": row['added_at']
                })
        
        return {
            "success": True,
            "query": query,
            "count": len(entities_map),
            "results": list(entities_map.values())
        }
        
    finally:
        conn.close()


@mcp.tool()
def list_entities(
    database: str = "default",
    salience: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """
    List entities from a specific database.
    
    Args:
        database: Which database to list from
        salience: Filter by salience level (foundational, active, background, archive)
        limit: Maximum number of entities to return
    """
    conn = get_db()
    
    try:
        if salience:
            query = "SELECT * FROM entities WHERE database = ? AND salience = ? ORDER BY updated_at DESC LIMIT ?"
            params = (database, salience, limit)
        else:
            query = "SELECT * FROM entities WHERE database = ? ORDER BY updated_at DESC LIMIT ?"
            params = (database, limit)
        
        entities = conn.execute(query, params).fetchall()
        
        result_list = []
        for entity in entities:
            observations = conn.execute(
                "SELECT content, added_at FROM observations WHERE entity_id = ? ORDER BY added_at DESC LIMIT 3",
                (entity['id'],)
            ).fetchall()
            
            result_list.append({
                "name": entity['name'],
                "type": entity['entity_type'],
                "salience": entity['salience'],
                "updated": entity['updated_at'],
                "recent_observations": [obs['content'] for obs in observations]
            })
        
        return {
            "success": True,
            "database": database,
            "count": len(result_list),
            "entities": result_list
        }
        
    finally:
        conn.close()


@mcp.tool()
def update_entity(
    entity_name: str,
    database: str = "default",
    new_name: Optional[str] = None,
    new_type: Optional[str] = None,
    new_salience: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update an entity's metadata.
    
    Args:
        entity_name: Current name of the entity
        database: Which database the entity is in
        new_name: New name for the entity (optional)
        new_type: New entity type (optional)
        new_salience: New salience level (optional)
    """
    conn = get_db()
    
    try:
        entity = conn.execute(
            "SELECT id FROM entities WHERE name = ? AND database = ?",
            (entity_name, database)
        ).fetchone()
        
        if not entity:
            return {"success": False, "error": f"Entity '{entity_name}' not found"}
        
        updates = []
        params = []
        
        if new_name:
            updates.append("name = ?")
            params.append(new_name)
        if new_type:
            updates.append("entity_type = ?")
            params.append(new_type)
        if new_salience:
            updates.append("salience = ?")
            params.append(new_salience)
        
        if not updates:
            return {"success": False, "error": "No updates specified"}
        
        updates.append("updated_at = ?")
        params.append(now_iso())
        params.append(entity['id'])
        
        conn.execute(
            f"UPDATE entities SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()
        
        return {
            "success": True,
            "message": f"Entity '{entity_name}' updated successfully"
        }
        
    finally:
        conn.close()


@mcp.tool()
def get_context_block(
    max_length: int = 2000,
    include_recent_hours: int = 48,
    databases: Optional[List[str]] = None
) -> str:
    """
    Generate a formatted context block for injection into conversations.
    
    Args:
        max_length: Maximum character length for the context block
        include_recent_hours: Include observations from the last N hours
        databases: Which databases to include (if None, includes all)
    """
    conn = get_db()
    
    try:
        parts = []
        parts.append("[COMPANION MEMORY CONTEXT]")
        parts.append(f"Last updated: {now_iso()}")
        parts.append("")
        
        if databases:
            placeholders = ','.join('?' * len(databases))
            foundational = conn.execute(f"""
                SELECT e.*, o.content
                FROM entities e
                LEFT JOIN observations o ON e.id = o.entity_id
                WHERE e.salience = 'foundational' AND e.database IN ({placeholders})
                ORDER BY e.updated_at DESC
            """, databases).fetchall()
        else:
            foundational = conn.execute("""
                SELECT e.*, o.content
                FROM entities e
                LEFT JOIN observations o ON e.id = o.entity_id
                WHERE e.salience = 'foundational'
                ORDER BY e.updated_at DESC
            """).fetchall()
        
        if foundational:
            parts.append("## Core Knowledge")
            
            entity_obs = {}
            for row in foundational:
                name = row['name']
                if name not in entity_obs:
                    entity_obs[name] = {"type": row['entity_type'], "obs": []}
                if row['content']:
                    entity_obs[name]['obs'].append(row['content'])
            
            for name, data in list(entity_obs.items())[:10]:
                obs_text = "; ".join(data['obs'][:3])
                parts.append(f"{name} ({data['type']}): {obs_text}")
            parts.append("")
        
        cutoff = datetime.now().timestamp() - (include_recent_hours * 3600)
        cutoff_iso = datetime.fromtimestamp(cutoff).isoformat()
        
        if databases:
            recent = conn.execute(f"""
                SELECT e.name, e.entity_type, o.content, o.added_at
                FROM observations o
                JOIN entities e ON o.entity_id = e.id
                WHERE o.added_at > ? AND e.database IN ({placeholders})
                ORDER BY o.added_at DESC
                LIMIT 10
            """, [cutoff_iso] + databases).fetchall()
        else:
            recent = conn.execute("""
                SELECT e.name, e.entity_type, o.content, o.added_at
                FROM observations o
                JOIN entities e ON o.entity_id = e.id
                WHERE o.added_at > ?
                ORDER BY o.added_at DESC
                LIMIT 10
            """, (cutoff_iso,)).fetchall()
        
        if recent:
            parts.append("## Recent Activity")
            for row in recent:
                parts.append(f"{row['name']}: {row['content']}")
            parts.append("")
        
        parts.append("[END MEMORY CONTEXT]")
        parts.append("")
        
        result = "\n".join(parts)
        
        if len(result) > max_length:
            result = result[:max_length - 50] + "\n...[truncated]\n[END MEMORY CONTEXT]\n\n"
        
        return result
        
    finally:
        conn.close()


# ============================================================
# THE VOID TOOLS (Lincoln's Journal)
# ============================================================

@mcp.tool()
async def void_write_entry(
    title: str,
    summary: str,
    decisions: Optional[str] = "",
    open_loops: Optional[str] = "",
    artifacts: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    importance: int = 1,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Store a structured journal entry in The Void.
    """
    artifacts_json = json.dumps(artifacts or {}, ensure_ascii=False)
    tags_json = json.dumps(tags or [], ensure_ascii=False)

    async with aiosqlite.connect(VOID_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO entries
               (created_at, session_id, thread_id, title, summary, decisions, open_loops, artifacts_json, tags_json, importance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), session_id, thread_id, title, summary, decisions, open_loops, artifacts_json, tags_json, importance)
        )
        await db.commit()
        entry_id = cur.lastrowid

    return {"ok": True, "id": entry_id}


@mcp.tool()
async def void_append_snippet(
    text: str,
    tags: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    importance: int = 1
) -> Dict[str, Any]:
    """
    Quick drop: stores a tiny entry where the snippet is the summary.
    """
    return await void_write_entry(
        title="Snippet",
        summary=text,
        decisions="",
        open_loops="",
        artifacts={"snippet": text},
        tags=tags or ["snippet"],
        importance=importance,
        session_id=session_id,
        thread_id=thread_id
    )


@mcp.tool()
async def void_search(
    query: str,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Full-text search across entries.
    """
    async with aiosqlite.connect(VOID_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT e.id, e.created_at, e.title,
                      snippet(entries_fts, 1, '[', ']', '…', 15) AS snippet
               FROM entries_fts
               JOIN entries e ON e.id = entries_fts.rowid
               WHERE entries_fts MATCH ?
               ORDER BY e.id DESC
               LIMIT ?""",
            (query, limit)
        )

    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "title": r["title"],
            "snippet": r["snippet"]
        })
    return {"ok": True, "results": results}


@mcp.tool()
async def void_get_entry(entry_id: int) -> Dict[str, Any]:
    """
    Fetch a single entry by ID.
    """
    async with aiosqlite.connect(VOID_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        row = await cursor.fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}

    return {
        "ok": True,
        "entry": {
            "id": row["id"],
            "created_at": row["created_at"],
            "session_id": row["session_id"],
            "thread_id": row["thread_id"],
            "title": row["title"],
            "summary": row["summary"],
            "decisions": row["decisions"],
            "open_loops": row["open_loops"],
            "artifacts": json.loads(row["artifacts_json"] or "{}"),
            "tags": json.loads(row["tags_json"] or "[]"),
            "importance": row["importance"],
        }
    }


@mcp.tool()
async def void_list_recent(limit: int = 10) -> Dict[str, Any]:
    """
    List most recent entries.
    """
    async with aiosqlite.connect(VOID_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT id, created_at, title, importance FROM entries ORDER BY id DESC LIMIT ?",
            (limit,)
        )
    return {"ok": True, "entries": [dict(r) for r in rows]}


# ============================================================
# LOVENSE TOOLS
# ============================================================

async def call_lovense(endpoint: str, data: dict = {}) -> dict:
    """Helper to call the Lovense cloud API"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LOVENSE_URL}{endpoint}",
            json=data,
            timeout=30.0
        )
        return response.json()


@mcp.tool()
async def get_qr_code() -> Dict[str, Any]:
    """Generate QR code for pairing toy with this MCP. User scans with Lovense Remote app."""
    return await call_lovense('/api/qr', {})


@mcp.tool()
async def get_toys() -> Dict[str, Any]:
    """Get list of connected Lovense toys"""
    return await call_lovense('/api/toys', {})


@mcp.tool()
async def vibrate(
    intensity: int = 10,
    duration: int = 5
) -> Dict[str, Any]:
    """
    Vibrate the toy
    
    Args:
        intensity: Vibration strength 0-20
        duration: Duration in seconds
    """
    return await call_lovense('/api/vibrate', {"intensity": intensity, "duration": duration})


@mcp.tool()
async def vibrate_pattern(
    intensity: int = 10,
    duration: int = 10,
    on_sec: int = 2,
    off_sec: int = 1
) -> Dict[str, Any]:
    """
    Vibrate with on/off pattern (pulsing)
    
    Args:
        intensity: Vibration strength 0-20
        duration: Total duration in seconds
        on_sec: Seconds of vibration per pulse
        off_sec: Seconds of pause between pulses
    """
    return await call_lovense('/api/vibrate-pattern', {
        "intensity": intensity,
        "duration": duration,
        "on_sec": on_sec,
        "off_sec": off_sec
    })


@mcp.tool()
async def pattern(
    strengths: str = "5;10;15;20;15;10;5",
    interval_ms: int = 500,
    duration: int = 10
) -> Dict[str, Any]:
    """
    Send custom intensity pattern
    
    Args:
        strengths: Semicolon-separated intensity values 0-20
        interval_ms: Milliseconds between each intensity change
        duration: Total duration in seconds
    """
    return await call_lovense('/api/pattern', {
        "strengths": strengths,
        "interval_ms": interval_ms,
        "duration": duration
    })


@mcp.tool()
async def preset(
    name: str = "pulse",
    duration: int = 10
) -> Dict[str, Any]:
    """
    Run a built-in pattern preset: pulse, wave, fireworks, or earthquake
    
    Args:
        name: Preset name (pulse, wave, fireworks, earthquake)
        duration: Duration in seconds
    """
    return await call_lovense('/api/preset', {"name": name, "duration": duration})


@mcp.tool()
async def stop() -> Dict[str, Any]:
    """Stop all toy activity immediately"""
    return await call_lovense('/api/stop', {})


@mcp.tool()
async def edge(
    intensity: int = 15,
    duration: int = 30,
    on_sec: int = 5,
    off_sec: int = 3
) -> Dict[str, Any]:
    """
    Edging pattern - build up then stop, repeat
    
    Args:
        intensity: Peak vibration strength 0-20
        duration: Total duration in seconds
        on_sec: Seconds of vibration per cycle
        off_sec: Seconds of pause between cycles
    """
    return await call_lovense('/api/edge', {
        "intensity": intensity,
        "duration": duration,
        "on_sec": on_sec,
        "off_sec": off_sec
    })


@mcp.tool()
async def tease(duration: int = 20) -> Dict[str, Any]:
    """
    Teasing pattern - random-feeling intensity changes
    
    Args:
        duration: Duration in seconds
    """
    return await call_lovense('/api/tease', {"duration": duration})


@mcp.tool()
async def escalate(
    start: int = 3,
    peak: int = 18,
    duration: int = 30
) -> Dict[str, Any]:
    """
    Gradual escalation from low to high intensity
    
    Args:
        start: Starting intensity 0-20
        peak: Peak intensity 0-20
        duration: Duration in seconds
    """
    return await call_lovense('/api/escalate', {
        "start": start,
        "peak": peak,
        "duration": duration
    })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import sys
    import asyncio
    
    # Initialize The Void database
    asyncio.run(init_void_database())
    
    # Auto-detect deployment: if PORT env var exists, run SSE for remote access
    port = int(os.environ.get("PORT", 0))
    
    if port:
        # Production / Railway
        mcp.run(transport="sse", host="0.0.0.0", port=port)
    elif "--transport" in sys.argv and "sse" in sys.argv:
        # Local SSE mode (ngrok etc)
        mcp.run(transport="sse", port=8000)
    else:
        # Local stdio mode
        mcp.run()
