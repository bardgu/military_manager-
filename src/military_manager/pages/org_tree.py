"""Organization Tree — auto-built hierarchical org chart from period soldiers,
with manual editing and drag-and-drop soldier reassignment."""

from __future__ import annotations

import json
from collections import defaultdict

import streamlit as st
import streamlit.components.v1 as components
from streamlit_sortables import sort_items

from military_manager.components.navigation import render_page_header
from military_manager.components.auth import get_current_user
from military_manager.services.auth_service import is_mefaked
from military_manager.services.soldier_service import get_period_soldiers
from military_manager.services.stats_service import get_setting, set_setting

# ── Role hierarchy config ──────────────────────────────────────

# Roles considered "commander" positions in the hierarchy.
COMMANDER_ROLES: set[str] = {
    'מ"פ', 'סמ"פ', 'רס"פ', 'מ"מ', 'סמל מחלקה', "מ\"כ א'", "מ\"כ ב'", 'חפ"ק',
}

# Icons for regular soldiers by task_role
TASK_ROLE_ICONS: dict[str, str] = {
    "מחלץ": "🛡️",
    "חובש": "🏥",
    "נהג": "🚗",
    "קשר עורף": "📡",
    "מהנדס": "🔧",
    'אנו"ח': "📋",
    "מחסנאי": "📦",
    "לוחם": "👤",
}

_SETTING_KEY = "org_tree_json"


# ── Persistence helpers ────────────────────────────────────────

def _load_tree(period_id: int) -> list[dict] | None:
    """Load saved org tree JSON from AppSetting."""
    raw = get_setting(period_id, _SETTING_KEY, "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) and data else None
    except (json.JSONDecodeError, TypeError):
        return None


def _save_tree(period_id: int, nodes: list[dict]) -> None:
    """Persist org tree JSON to AppSetting."""
    # Strip 'children' key if accidentally included
    clean = []
    for n in nodes:
        clean.append({k: v for k, v in n.items() if k != "children"})
    set_setting(period_id, _SETTING_KEY, json.dumps(clean, ensure_ascii=False))


# ── Auto-build algorithm ──────────────────────────────────────

def _auto_build(period_id: int) -> list[dict]:
    """Build org-chart node list from current period soldiers.

    Hierarchy:
      מ"פ
      ├── סמ"פ
      │   └── רס"פ
      ├── חפ"ק מ"פ  (HQ soldiers)
      ├── מ"מ מחלקה 1
      │   ├── סמל מחלקה / מ"כ
      │   └── soldiers …
      ├── מ"מ מחלקה 2
      │   └── …
      └── מ"מ מחלקה 3
          └── …
    """
    soldiers = get_period_soldiers(period_id, exclude_irrelevant_unit=True)
    if not soldiers:
        return []

    nodes: list[dict] = []
    _nid = 0

    def _next_id() -> int:
        nonlocal _nid
        _nid += 1
        return _nid

    def _make_node(parent_id, title, soldier=None, icon="👤", sort=0, role=""):
        nid = _next_id()
        nodes.append({
            "id": nid,
            "parent_id": parent_id,
            "title": title,
            "soldier_id": soldier["soldier_id"] if soldier else None,
            "soldier_name": soldier["full_name"] if soldier else "",
            "phone": (soldier.get("phone") or "") if soldier else "",
            "role": role or ((soldier.get("role") or "") if soldier else role),
            "task_role": (soldier.get("task_role") or "") if soldier else "",
            "icon": icon,
            "sort_order": sort,
        })
        return nid

    # ---- group soldiers by sub_unit ----
    by_unit: dict[str, list[dict]] = defaultdict(list)
    for s in soldiers:
        by_unit[s["sub_unit"]].append(s)

    # Determine platoon sub-units (מחלקות) vs HQ
    hq_name = "מפקדת הפלוגה"
    platoon_units = sorted([u for u in by_unit if u != hq_name])

    # ---- Level 0: root = מ"פ ----
    hq_soldiers = list(by_unit.get(hq_name, []))
    mefaked = _pop_role(hq_soldiers, 'מ"פ')
    root_id = _make_node(None, 'מ"פ', mefaked, "⭐", 0, 'מ"פ')

    # ---- Level 1a: סמ"פ ----
    samal_mf = _pop_role(hq_soldiers, 'סמ"פ')
    smp_id = _make_node(root_id, 'סמ"פ', samal_mf, "🎖️", 1, 'סמ"פ')

    # ---- Level 2: רס"פ under סמ"פ ----
    rasap = _pop_role(hq_soldiers, 'רס"פ')
    if rasap:
        _make_node(smp_id, 'רס"פ', rasap, "🎖️", 0, 'רס"פ')

    # ---- Level 1b: חפ"ק (remaining HQ soldiers) ----
    hafak_id = _make_node(root_id, 'חפ"ק מ"פ', None, "📡", 2, 'חפ"ק')
    for idx, s in enumerate(hq_soldiers):
        icon = _icon_for(s)
        _make_node(hafak_id, s.get("task_role") or s.get("role") or "חייל", s, icon, idx)

    # ---- Level 1c: מ"מ per platoon (מחלקה) ----
    for pi, unit_name in enumerate(platoon_units):
        unit_soldiers = list(by_unit[unit_name])

        # Find מ"מ
        mm = _pop_role(unit_soldiers, 'מ"מ')
        mm_title = f'מ"מ {unit_name}'
        mm_id = _make_node(root_id, mm_title, mm, "🪖", 10 + pi, 'מ"מ')

        # Find squad-level commanders
        squad_commanders: list[tuple[int, dict | None, str]] = []

        sm = _pop_role(unit_soldiers, 'סמל מחלקה')
        if sm:
            sm_id = _make_node(mm_id, 'סמל מחלקה', sm, "🎖️", 0, 'סמל מחלקה')
            squad_commanders.append((sm_id, sm, 'סמל מחלקה'))

        mk_a = _pop_role(unit_soldiers, "מ\"כ א'")
        if mk_a:
            mka_id = _make_node(mm_id, "מ\"כ א'", mk_a, "🪖", 1, "מ\"כ א'")
            squad_commanders.append((mka_id, mk_a, "מ\"כ א'"))

        mk_b = _pop_role(unit_soldiers, "מ\"כ ב'")
        if mk_b:
            mkb_id = _make_node(mm_id, "מ\"כ ב'", mk_b, "🪖", 2, "מ\"כ ב'")
            squad_commanders.append((mkb_id, mk_b, "מ\"כ ב'"))

        # Distribute remaining soldiers among squad leaders
        if squad_commanders:
            for idx, s in enumerate(unit_soldiers):
                parent = squad_commanders[idx % len(squad_commanders)][0]
                icon = _icon_for(s)
                _make_node(parent, s.get("task_role") or s.get("role") or "חייל", s, icon, idx)
        else:
            # No squad leaders — put directly under מ"מ
            for idx, s in enumerate(unit_soldiers):
                icon = _icon_for(s)
                _make_node(mm_id, s.get("task_role") or s.get("role") or "חייל", s, icon, idx)

    return nodes


def _pop_role(soldiers: list[dict], role: str) -> dict | None:
    """Remove and return the first soldier matching the given role."""
    for i, s in enumerate(soldiers):
        if s.get("role") == role:
            return soldiers.pop(i)
    return None


def _icon_for(soldier: dict) -> str:
    """Pick icon based on task_role."""
    tr = soldier.get("task_role") or ""
    for key, icon in TASK_ROLE_ICONS.items():
        if key in tr:
            return icon
    return "👤"


# ── Tree helpers ───────────────────────────────────────────────

def _build_tree(nodes: list[dict]) -> list[dict]:
    """Convert flat node list → nested tree."""
    by_id = {n["id"]: {**n, "children": []} for n in nodes}
    roots = []
    for n in sorted(nodes, key=lambda x: x.get("sort_order", 0)):
        node = by_id[n["id"]]
        pid = n.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)
    # sort children
    for node in by_id.values():
        node["children"].sort(key=lambda c: c.get("sort_order", 0))
    return roots


def _get_container_nodes(nodes: list[dict]) -> dict[int, dict]:
    """Return {node_id: node} for every node that can hold children (commander / structural)."""
    containers = {}
    for n in nodes:
        # A node is a container if it has no soldier_id (structural) or is a commander role
        if not n.get("soldier_id") or n.get("role") in COMMANDER_ROLES:
            containers[n["id"]] = n
    return containers


# ── Graphviz visualization ─────────────────────────────────────

_ROLE_COLORS = {
    'מ"פ':       "#388E3C",   # dark green
    'סמ"פ':      "#43A047",
    'רס"פ':      "#66BB6A",
    'חפ"ק':      "#42A5F5",   # blue
    'מ"מ':       "#5C6BC0",   # indigo
    'סמל מחלקה': "#AB47BC",   # purple
    "מ\"כ א'": "#7E57C2",
    "מ\"כ ב'": "#9575CD",
}

_CLUSTER_COLORS = [
    "#E8F5E9",  # green-50
    "#E3F2FD",  # blue-50
    "#EDE7F6",  # purple-50
    "#FFF3E0",  # orange-50
    "#FCE4EC",  # pink-50
]


def _count_children(node: dict) -> int:
    """Count non-commander soldier children (direct + nested)."""
    count = 0
    for ch in node.get("children", []):
        if ch.get("soldier_id") and ch.get("role") not in COMMANDER_ROLES:
            count += 1
        count += _count_children(ch)
    return count


def _build_dot(roots: list[dict]) -> str:
    """Build DOT source — commanders as individual boxes, soldiers as
    compact table-rows attached to their squad leader."""
    lines = [
        'digraph OrgChart {',
        '  rankdir=TB;',
        '  compound=true;',
        '  bgcolor="transparent";',
        '  ranksep=0.55;',
        '  nodesep=0.35;',
        '  node [shape=box, style="filled,rounded", fontname="Arial",',
        '        fontsize=11, margin="0.2,0.1", penwidth=1.2];',
        '  edge [color="#78909C", arrowsize=0.55, penwidth=1.2];',
    ]

    def _esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def _is_cmd(n: dict) -> bool:
        return (not n.get("soldier_id")) or (n.get("role") in COMMANDER_ROLES)

    def _get_leaf_soldiers(n: dict) -> list[dict]:
        """Get direct non-commander children."""
        return [ch for ch in n.get("children", [])
                if ch.get("soldier_id") and ch.get("role") not in COMMANDER_ROLES]

    def _cmd_label(n: dict) -> str:
        parts = [f"<b>{_esc(n['icon'])} {_esc(n['title'])}</b>"]
        if n.get("soldier_name"):
            parts.append(_esc(n["soldier_name"]))
        return "<" + "<br/>".join(parts) + ">"

    def _soldiers_table(soldiers: list[dict], parent_id: str) -> str:
        """Build an HTML-label table node listing all soldiers compactly."""
        tid = f"tbl_{parent_id}"
        rows = []
        for s in soldiers:
            name = _esc(s.get("soldier_name") or "")
            tr = _esc(s.get("task_role") or s.get("role") or "חייל")
            icon = _esc(s.get("icon", "👤"))
            rows.append(
                f'<tr><td align="right"><font point-size="9">{icon} {name}</font></td>'
                f'<td align="right"><font point-size="8" color="#666">{tr}</font></td></tr>'
            )
        table = (
            '<<table border="0" cellborder="0" cellspacing="1" cellpadding="2">'
            + "".join(rows)
            + '</table>>'
        )
        lines.append(
            f'  {tid} [label={table}, shape=plaintext, '
            f'style="filled,rounded", fillcolor="#FAFAFA", '
            f'margin="0.05,0.05"];'
        )
        lines.append(f'  {parent_id} -> {tid};')
        return tid

    def _add(n: dict, parent_dot_id: str | None = None):
        nid = f'n{n["id"]}'
        role = n.get("role", "")
        fill = _ROLE_COLORS.get(role, "#E8F5E9")
        fc = "white" if role in ('מ"פ', 'סמ"פ', 'מ"מ', "מ\"כ א'", "מ\"כ ב'") else "#212121"
        fs = "13" if role == 'מ"פ' else "11"
        lbl = _cmd_label(n)

        lines.append(
            f'  {nid} [label={lbl}, fillcolor="{fill}", '
            f'fontcolor="{fc}", fontsize={fs}];'
        )
        if parent_dot_id:
            lines.append(f'  {parent_dot_id} -> {nid};')

        # Get commander children and leaf soldiers
        cmd_children = [ch for ch in n.get("children", []) if _is_cmd(ch)]
        leaf_soldiers = _get_leaf_soldiers(n)

        # Add soldier table if this commander has soldiers
        if leaf_soldiers:
            _soldiers_table(leaf_soldiers, nid)

        # Recurse into commander children
        for ch in cmd_children:
            _add(ch, nid)

    for r in roots:
        _add(r)

    lines.append("}")
    return "\n".join(lines)


def _render_chart(roots: list[dict]):
    """Render an interactive, zoomable org chart using viz.js (client-side)."""
    dot_src = _build_dot(roots)
    # Escape backticks and backslashes for JS template literal
    dot_js = dot_src.replace("\\", "\\\\").replace("`", "\\`")

    html = f"""
    <style>
        .org-wrap {{
            position: relative;
            width: 100%;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            background: #fafafa;
            overflow: hidden;
        }}
        .org-controls {{
            position: absolute;
            top: 8px;
            left: 8px;
            z-index: 10;
            display: flex;
            gap: 4px;
        }}
        .org-controls button {{
            width: 34px; height: 34px;
            border: 1px solid #bbb;
            border-radius: 6px;
            background: white;
            font-size: 17px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 1px 3px rgba(0,0,0,0.12);
        }}
        .org-controls button:hover {{ background: #e8f5e9; }}
        .org-viewport {{
            width: 100%;
            height: 590px;
            overflow: auto;
            cursor: grab;
        }}
        .org-viewport:active {{ cursor: grabbing; }}
        .org-inner {{
            transform-origin: 0 0;
            min-width: max-content;
            padding: 20px;
            transition: transform 0.1s ease;
        }}
        .org-inner svg {{ display: block; }}
        .org-loading {{
            display: flex;
            align-items: center;
            justify-content: center;
            height: 200px;
            color: #888;
            font-family: Arial;
        }}
    </style>
    <div class="org-wrap">
        <div class="org-controls">
            <button onclick="zoomIn()" title="הגדל">➕</button>
            <button onclick="zoomOut()" title="הקטן">➖</button>
            <button onclick="resetZoom()" title="100%">1:1</button>
            <button onclick="fitToView()" title="התאם לחלון">⊞</button>
        </div>
        <div class="org-viewport" id="orgViewport">
            <div class="org-inner" id="orgInner">
                <div class="org-loading">⏳ טוען תרשים...</div>
            </div>
        </div>
    </div>

    <script type="module">
        import {{ instance }} from "https://cdn.jsdelivr.net/npm/@viz-js/viz@3.11.0/lib/viz-standalone.mjs";

        const dotSrc = `{dot_js}`;
        const inner = document.getElementById('orgInner');
        const viewport = document.getElementById('orgViewport');
        let scale = 1.0;

        function setScale(s) {{
            scale = Math.max(0.15, Math.min(3.0, s));
            inner.style.transform = 'scale(' + scale + ')';
        }}
        window.zoomIn  = () => setScale(scale + 0.15);
        window.zoomOut = () => setScale(scale - 0.15);
        window.resetZoom = () => {{ setScale(1.0); viewport.scrollTo(0, 0); }};
        window.fitToView = () => {{
            const svgEl = inner.querySelector('svg');
            if (!svgEl) return;
            const w = svgEl.viewBox?.baseVal?.width || svgEl.scrollWidth || 800;
            const vw = viewport.clientWidth - 40;
            setScale(Math.min(1.2, vw / w));
            viewport.scrollTo(0, 0);
        }};

        // Wheel zoom
        viewport.addEventListener('wheel', e => {{
            e.preventDefault();
            setScale(scale + (e.deltaY < 0 ? 0.06 : -0.06));
        }}, {{passive: false}});

        // Drag-to-pan
        let drag = false, sx, sy, sl, st2;
        viewport.addEventListener('mousedown', e => {{
            drag = true; sx = e.pageX; sy = e.pageY;
            sl = viewport.scrollLeft; st2 = viewport.scrollTop;
        }});
        document.addEventListener('mousemove', e => {{
            if (!drag) return;
            viewport.scrollLeft = sl - (e.pageX - sx);
            viewport.scrollTop  = st2 - (e.pageY - sy);
        }});
        document.addEventListener('mouseup', () => {{ drag = false; }});

        // Render
        try {{
            const viz = await instance();
            const svgStr = viz.renderString(dotSrc, {{ format: "svg" }});
            inner.innerHTML = svgStr;
            setTimeout(window.fitToView, 50);
        }} catch(err) {{
            inner.innerHTML = '<p style="color:red">שגיאה בטעינת התרשים: ' + err.message + '</p>';
        }}
    </script>
    """
    components.html(html, height=650, scrolling=False)


def _render_move_soldier(nodes: list[dict], period_id: int, can_edit: bool):
    """Quick UI to move a soldier to a different parent node."""
    if not can_edit:
        return

    st.markdown("#### 🔀 העבר חייל/תפקיד")

    # Soldiers (leaf nodes that can be moved)
    movable = [n for n in nodes if n.get("soldier_id")]
    # Possible parents (commanders / structural)
    parents = [n for n in nodes if (not n.get("soldier_id")) or n.get("role") in COMMANDER_ROLES]

    if not movable:
        return

    c1, c2, c3 = st.columns([3, 3, 2])

    with c1:
        soldier_options = {
            f"{s['icon']} {s['soldier_name']} ({s.get('task_role') or s.get('role') or 'חייל'})": s["id"]
            for s in sorted(movable, key=lambda x: x.get("soldier_name", ""))
        }
        selected_soldier = st.selectbox("בחר חייל", list(soldier_options.keys()),
                                        key="move_soldier_sel")

    with c2:
        parent_options = {
            f"{p['icon']} {p['title']}" + (f" — {p['soldier_name']}" if p.get('soldier_name') else ""): p["id"]
            for p in sorted(parents, key=lambda x: x.get("sort_order", 0))
        }
        selected_parent = st.selectbox("העבר אל", list(parent_options.keys()),
                                       key="move_parent_sel")

    with c3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔀 העבר", key="move_btn", type="primary"):
            sid = soldier_options[selected_soldier]
            pid = parent_options[selected_parent]
            for n in nodes:
                if n["id"] == sid:
                    n["parent_id"] = pid
                    break
            _save_tree(period_id, nodes)
            st.success("✅ הועבר בהצלחה!")
            st.rerun()


def _render_soldier_details(roots: list[dict]):
    """Show expandable per-unit soldier lists below the chart."""

    def _collect_branch(node: dict) -> list[dict]:
        """Collect all leaf soldiers in this subtree."""
        result = []
        if node.get("soldier_id") and node.get("role") not in COMMANDER_ROLES:
            result.append(node)
        for ch in node.get("children", []):
            result.extend(_collect_branch(ch))
        return result

    # Walk top-level children of root (the branches)
    for root in roots:
        for branch in root.get("children", []):
            soldiers_in_branch = _collect_branch(branch)
            branch_label = f"{branch['icon']} {branch['title']}"
            if branch.get("soldier_name"):
                branch_label += f" — {branch['soldier_name']}"
            branch_label += f"  ({len(soldiers_in_branch)} חיילים)"

            with st.expander(branch_label, expanded=False):
                if not soldiers_in_branch:
                    st.caption("אין חיילים בענף זה")
                    continue

                # Group by their immediate parent (squad leader)
                by_parent: dict[str, list[dict]] = defaultdict(list)
                for s in soldiers_in_branch:
                    # Find parent node title
                    pid = s.get("parent_id")
                    parent_label = "ישיר"
                    for n in _flatten(branch):
                        if n["id"] == pid:
                            parent_label = f"{n['icon']} {n['title']}"
                            if n.get("soldier_name"):
                                parent_label += f" ({n['soldier_name']})"
                            break
                    by_parent[parent_label].append(s)

                for parent_lbl, group in by_parent.items():
                    st.markdown(f"**{parent_lbl}**")
                    for s in group:
                        tr = s.get("task_role") or s.get("role") or "חייל"
                        phone = f" | 📞 {s['phone']}" if s.get("phone") else ""
                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{s['icon']} {s['soldier_name']} — {tr}{phone}")


def _flatten(node: dict) -> list[dict]:
    """Flatten a tree node into a list."""
    result = [node]
    for ch in node.get("children", []):
        result.extend(_flatten(ch))
    return result


# ── Drag-and-drop assignment UI ────────────────────────────────

def _render_dnd_editor(nodes: list[dict], period_id: int, can_edit: bool):
    """Render multi-container sortable for reassigning soldiers."""
    if not can_edit:
        st.warning('רק מ"פ יכול לערוך את המבנה הארגוני')
        return

    containers = _get_container_nodes(nodes)
    # Soldier nodes = those with soldier_id and NOT a commander role
    soldier_nodes = [n for n in nodes if n.get("soldier_id") and n.get("role") not in COMMANDER_ROLES]

    if not soldier_nodes:
        st.info("אין חיילים להצגה. לחץ על 'בנה אוטומטית' ליצירת המבנה.")
        return

    # Group soldiers by their parent container
    groups: dict[int, list[dict]] = defaultdict(list)
    for sn in soldier_nodes:
        groups[sn.get("parent_id", 0)].append(sn)

    # Build sortable containers in tree order
    sortable_containers: list[dict] = []
    container_id_order: list[int] = []
    tree = _build_tree(nodes)

    def _walk_containers(node, depth=0):
        nid = node["id"]
        if nid in containers:
            label = f"{node['icon']} {node['title']}"
            if node.get("soldier_name"):
                label += f" — {node['soldier_name']}"
            items_in = groups.get(nid, [])
            item_labels = [
                f"{s['icon']} {s['soldier_name']} ({s.get('task_role') or s.get('role') or 'חייל'})"
                for s in sorted(items_in, key=lambda x: x.get("sort_order", 0))
            ]
            # Show if container has soldiers or is a leaf node
            has_soldier_children = len(items_in) > 0
            has_container_children = any(ch["id"] in containers for ch in node.get("children", []))
            if has_soldier_children or not has_container_children:
                sortable_containers.append({"header": label, "items": item_labels})
                container_id_order.append(nid)

        for ch in node.get("children", []):
            _walk_containers(ch, depth + 1)

    for r in tree:
        _walk_containers(r)

    if not sortable_containers:
        st.info("אין מיכלים עם חיילים.")
        return

    st.markdown("#### 🔄 גרור חיילים בין יחידות")
    st.caption("גרור ושחרר חיילים מקבוצה לקבוצה כדי להעביר אותם")

    sorted_result = sort_items(sortable_containers, multi_containers=True, direction="vertical")

    # Detect changes and persist
    if sorted_result:
        original = [c["items"] for c in sortable_containers]
        if sorted_result != original:
            label_to_node: dict[str, dict] = {}
            for sn in soldier_nodes:
                lbl = f"{sn['icon']} {sn['soldier_name']} ({sn.get('task_role') or sn.get('role') or 'חייל'})"
                label_to_node[lbl] = sn

            changed = False
            for ci, item_list in enumerate(sorted_result):
                if ci >= len(container_id_order):
                    break
                container_nid = container_id_order[ci]
                for si, item_label in enumerate(item_list):
                    sn = label_to_node.get(item_label)
                    if sn and (sn["parent_id"] != container_nid or sn["sort_order"] != si):
                        sn["parent_id"] = container_nid
                        sn["sort_order"] = si
                        changed = True

            if changed:
                updated_ids = {sn["id"] for sn in soldier_nodes}
                new_nodes = [n for n in nodes if n["id"] not in updated_ids]
                for sn in soldier_nodes:
                    new_nodes.append({k: v for k, v in sn.items() if k != "children"})
                _save_tree(period_id, new_nodes)
                st.success("✅ המבנה עודכן!")
                st.rerun()


# ── Manual node editor ─────────────────────────────────────────

def _render_manual_editor(nodes: list[dict], period_id: int, can_edit: bool):
    """Tree editor with add/edit/delete and parent reassignment."""
    if not can_edit:
        st.warning('רק מ"פ יכול לערוך את המבנה הארגוני')
        return

    tree = _build_tree(nodes)
    if not tree:
        st.info("אין מבנה ארגוני. לחץ על 'בנה אוטומטית' ליצירת המבנה.")
        return

    # Add root-level node
    if st.button("➕ הוסף תפקיד ראשי", key="org_add_root"):
        st.session_state["org_adding_root"] = True

    if st.session_state.get("org_adding_root"):
        with st.container(border=True):
            st.markdown("**➕ תפקיד ראשי חדש**")
            c1, c2 = st.columns(2)
            with c1:
                rt = st.text_input("תפקיד", key="org_rt")
                rn = st.text_input("שם", key="org_rn")
            with c2:
                rp = st.text_input("טלפון", key="org_rp")
                icons = ["👤", "⭐", "🎖️", "🪖", "📡", "🏥", "🔧", "🚗", "🛡️", "📦"]
                ri = st.selectbox("אייקון", icons, key="org_ri")
            bc1, bc2 = st.columns(2)
            with bc1:
                if st.button("💾 הוסף", key="org_rsave", type="primary"):
                    if rt:
                        new_id = max((n["id"] for n in nodes), default=0) + 1
                        nodes.append({
                            "id": new_id, "parent_id": None, "title": rt,
                            "soldier_id": None, "soldier_name": rn, "phone": rp,
                            "role": "", "task_role": "", "icon": ri, "sort_order": 0,
                        })
                        _save_tree(period_id, nodes)
                        st.session_state.pop("org_adding_root", None)
                        st.rerun()
                    else:
                        st.error("חובה למלא שם תפקיד")
            with bc2:
                if st.button("❌ ביטול", key="org_rcanc"):
                    st.session_state.pop("org_adding_root", None)
                    st.rerun()

    st.markdown("---")

    def _render_node(node, depth=0):
        indent = "&nbsp;" * (depth * 6)
        icon = node["icon"]
        title = node["title"]
        name = node.get("soldier_name") or ""
        phone = node.get("phone") or ""

        name_display = f" — **{name}**" if name else ""
        phone_display = f" 📞{phone}" if phone else ""

        col1, col2, col3, col4 = st.columns([8, 1, 1, 1])
        with col1:
            st.markdown(f"{indent}{icon} **{title}**{name_display}{phone_display}",
                        unsafe_allow_html=True)
        with col2:
            if st.button("✏️", key=f"oedit_{node['id']}", help="ערוך"):
                st.session_state["org_editing"] = node["id"]
        with col3:
            if st.button("➕", key=f"oadd_{node['id']}", help="הוסף תחתיו"):
                st.session_state["org_adding_to"] = node["id"]
        with col4:
            if st.button("🗑️", key=f"odel_{node['id']}", help="מחק"):
                st.session_state["org_deleting"] = node["id"]

        # ── Edit form ──
        if st.session_state.get("org_editing") == node["id"]:
            _render_edit_form(node, nodes, period_id)

        # ── Add child form ──
        if st.session_state.get("org_adding_to") == node["id"]:
            _render_add_form(node, nodes, period_id)

        # ── Delete confirmation ──
        if st.session_state.get("org_deleting") == node["id"]:
            ch_cnt = len(node.get("children", []))
            warn = f" (כולל {ch_cnt} תתי-צמתים!)" if ch_cnt else ""
            st.warning(f"למחוק את **{title}**{warn}?")
            dc1, dc2 = st.columns(2)
            with dc1:
                if st.button("✅ כן, מחק", key=f"odelconf_{node['id']}"):
                    _delete_subtree(nodes, node["id"])
                    _save_tree(period_id, nodes)
                    st.session_state.pop("org_deleting", None)
                    st.rerun()
            with dc2:
                if st.button("❌ ביטול", key=f"odelcanc_{node['id']}"):
                    st.session_state.pop("org_deleting", None)
                    st.rerun()

        for ch in node.get("children", []):
            _render_node(ch, depth + 1)

    for root in tree:
        with st.expander(f"{root['icon']} {root['title']}", expanded=True):
            _render_node(root)


def _render_edit_form(node: dict, all_nodes: list[dict], period_id: int):
    """Inline edit form for a node."""
    with st.container(border=True):
        st.markdown(f"**✏️ עריכת: {node['title']}**")
        c1, c2 = st.columns(2)
        with c1:
            new_title = st.text_input("תפקיד", value=node["title"], key=f"oet_{node['id']}")
            new_name = st.text_input("שם", value=node.get("soldier_name") or "",
                                     key=f"oen_{node['id']}")
        with c2:
            new_phone = st.text_input("טלפון", value=node.get("phone") or "",
                                      key=f"oep_{node['id']}")
            icon_options = sorted(list(
                {n.get("icon", "👤") for n in all_nodes}
                | {"👤", "⭐", "🎖️", "🪖", "📡", "🏥", "🔧", "🚗", "🛡️", "📦"}
            ))
            cur_icon = node.get("icon", "👤")
            new_icon = st.selectbox(
                "אייקון", icon_options,
                index=icon_options.index(cur_icon) if cur_icon in icon_options else 0,
                key=f"oei_{node['id']}",
            )

        # Parent selector
        parent_options = {"ללא (שורש)": None}
        for n in all_nodes:
            if n["id"] != node["id"]:
                lbl = f"{n.get('icon', '')} {n['title']}"
                if n.get("soldier_name"):
                    lbl += f" — {n['soldier_name']}"
                parent_options[lbl] = n["id"]

        cur_parent_label = next(
            (k for k, v in parent_options.items() if v == node.get("parent_id")),
            "ללא (שורש)",
        )
        new_parent = st.selectbox(
            "הורה", list(parent_options.keys()),
            index=list(parent_options.keys()).index(cur_parent_label),
            key=f"oepar_{node['id']}",
        )

        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("💾 שמור", key=f"oesave_{node['id']}", type="primary"):
                for n in all_nodes:
                    if n["id"] == node["id"]:
                        n["title"] = new_title
                        n["soldier_name"] = new_name
                        n["phone"] = new_phone
                        n["icon"] = new_icon
                        n["parent_id"] = parent_options[new_parent]
                        break
                _save_tree(period_id, all_nodes)
                st.session_state.pop("org_editing", None)
                st.rerun()
        with bc2:
            if st.button("❌ ביטול", key=f"oecanc_{node['id']}"):
                st.session_state.pop("org_editing", None)
                st.rerun()


def _render_add_form(parent_node: dict, all_nodes: list[dict], period_id: int):
    """Form to add a new child node."""
    with st.container(border=True):
        st.markdown(f"**➕ תפקיד חדש תחת {parent_node['title']}**")
        c1, c2 = st.columns(2)
        with c1:
            ch_title = st.text_input("תפקיד", key=f"oat_{parent_node['id']}")
            ch_name = st.text_input("שם", key=f"oan_{parent_node['id']}")
        with c2:
            ch_phone = st.text_input("טלפון", key=f"oap_{parent_node['id']}")
            icons = ["👤", "⭐", "🎖️", "🪖", "📡", "🏥", "🔧", "🚗", "🛡️", "📦"]
            ch_icon = st.selectbox("אייקון", icons, key=f"oai_{parent_node['id']}")

        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("💾 הוסף", key=f"oasave_{parent_node['id']}", type="primary"):
                if ch_title:
                    new_id = max((n["id"] for n in all_nodes), default=0) + 1
                    all_nodes.append({
                        "id": new_id, "parent_id": parent_node["id"],
                        "title": ch_title, "soldier_id": None,
                        "soldier_name": ch_name, "phone": ch_phone,
                        "role": "", "task_role": "",
                        "icon": ch_icon,
                        "sort_order": len([
                            n for n in all_nodes if n.get("parent_id") == parent_node["id"]
                        ]),
                    })
                    _save_tree(period_id, all_nodes)
                    st.session_state.pop("org_adding_to", None)
                    st.rerun()
                else:
                    st.error("חובה למלא שם תפקיד")
        with bc2:
            if st.button("❌ ביטול", key=f"oacanc_{parent_node['id']}"):
                st.session_state.pop("org_adding_to", None)
                st.rerun()


def _delete_subtree(nodes: list[dict], node_id: int):
    """Remove a node and all descendants from the flat list (in-place)."""
    to_remove = {node_id}
    changed = True
    while changed:
        changed = False
        for n in nodes:
            if n.get("parent_id") in to_remove and n["id"] not in to_remove:
                to_remove.add(n["id"])
                changed = True
    nodes[:] = [n for n in nodes if n["id"] not in to_remove]


# ── Statistics summary ─────────────────────────────────────────

def _render_stats(nodes: list[dict]):
    """Show a quick breakdown of the tree."""
    total = len([n for n in nodes if n.get("soldier_id")])
    commanders = len([
        n for n in nodes if n.get("role") in COMMANDER_ROLES and n.get("soldier_id")
    ])
    soldiers = total - commanders

    tree = _build_tree(nodes)

    # Count per top-level branch
    branch_counts: list[tuple[str, int]] = []
    for r in tree:
        for ch in r.get("children", []):
            cnt = _count_soldiers(ch)
            label = ch["title"]
            if ch.get("soldier_name"):
                label += f" ({ch['soldier_name']})"
            branch_counts.append((label, cnt))

    c1, c2, c3 = st.columns(3)
    c1.metric('סה"כ בעץ', total)
    c2.metric("מפקדים", commanders)
    c3.metric("חיילים", soldiers)

    if branch_counts:
        st.markdown("**פילוח לפי ענף:**")
        cols = st.columns(min(len(branch_counts), 5))
        for i, (label, cnt) in enumerate(branch_counts):
            cols[i % len(cols)].metric(label, cnt)


def _count_soldiers(node: dict) -> int:
    """Count soldiers (nodes with soldier_id) in subtree."""
    count = 1 if node.get("soldier_id") else 0
    for ch in node.get("children", []):
        count += _count_soldiers(ch)
    return count


# ── Main render ──────────────────────────────────────────────

def render():
    render_page_header("🏛️ מבנה ארגוני", "עץ מבנה ארגוני היררכי — נבנה אוטומטית מנתוני החיילים")

    current_user = get_current_user()
    can_edit = is_mefaked(current_user) if current_user else False

    # Get active period
    period = st.session_state.get("active_period")
    if not period:
        st.warning("יש לבחור תקופת מילואים פעילה כדי לצפות במבנה הארגוני.")
        return
    period_id = period["id"]

    # Load existing tree
    nodes = _load_tree(period_id)

    # ── Action bar ──
    bar = st.columns([2, 2, 2, 6])
    with bar[0]:
        if can_edit and st.button("🔄 בנה אוטומטית", type="primary",
                                  help="בנה מחדש מנתוני החיילים"):
            with st.spinner("בונה מבנה ארגוני..."):
                nodes = _auto_build(period_id)
                _save_tree(period_id, nodes)
                st.success(f"✅ נבנה מבנה עם {len(nodes)} צמתים")
                st.rerun()
    with bar[1]:
        if can_edit and nodes and st.button("🗑️ אפס מבנה",
                                            help="מחק את כל המבנה הארגוני"):
            st.session_state["confirm_reset_org"] = True
    with bar[2]:
        if nodes:
            st.download_button(
                "📥 ייצוא JSON",
                data=json.dumps(nodes, ensure_ascii=False, indent=2),
                file_name="org_tree.json",
                mime="application/json",
            )

    # Reset confirmation
    if st.session_state.get("confirm_reset_org"):
        st.warning("האם אתה בטוח שברצונך למחוק את כל המבנה הארגוני?")
        rc1, rc2 = st.columns(2)
        with rc1:
            if st.button("✅ כן, אפס", key="org_reset_yes"):
                _save_tree(period_id, [])
                st.session_state.pop("confirm_reset_org", None)
                st.rerun()
        with rc2:
            if st.button("❌ ביטול", key="org_reset_no"):
                st.session_state.pop("confirm_reset_org", None)
                st.rerun()

    if not nodes:
        st.info("אין מבנה ארגוני. לחץ על **'בנה אוטומטית'** ליצירת מבנה מנתוני התקופה הנוכחית.")
        return

    # ── Tabs ──
    tab_chart, tab_dnd, tab_edit, tab_stats = st.tabs([
        "📊 תרשים",
        "🔄 גרור ושחרר",
        "✏️ עריכה ידנית",
        "📈 סטטיסטיקה",
    ])

    with tab_chart:
        tree = _build_tree(nodes)
        if tree:
            _render_chart(tree)
            st.markdown("---")
            _render_move_soldier(nodes, period_id, can_edit)
        else:
            st.info("אין מבנה להצגה.")

    with tab_dnd:
        fresh = _load_tree(period_id) or []
        _render_dnd_editor(fresh, period_id, can_edit)

    with tab_edit:
        fresh = _load_tree(period_id) or []
        _render_manual_editor(fresh, period_id, can_edit)

    with tab_stats:
        _render_stats(nodes)
