from pathlib import Path
p=Path('templates/tc_admin_list.html')
s=p.read_text(encoding='utf-8')
# Inject CSS
css_marker = '.name-link:hover{ text-decoration:underline; }'
if css_marker in s and 'status-badge' not in s:
    insert = '\n    .status-badge{ display:inline-flex; align-items:center; gap:6px; font-weight:700; font-size:12px; }\n    .status-badge .dot{ width:8px; height:8px; border-radius:50%; background:#9ca3af; display:inline-block; }\n    .status-badge.on .dot{ background:#10b981; }\n    .status-badge.off .dot{ background:#9ca3af; }'
    s = s.replace(css_marker, css_marker + insert)
# Add header column
s = s.replace('<th>Modelo</th>\n              <th style="text-align:right;">Ações</th>', '<th>Modelo</th>\n              <th>Agente</th>\n              <th style="text-align:right;">Ações</th>')
# Add row cell after model cell
model_cell = '              <td><span class="nowrap" title="{{ ct.model_path }}">{{ ct.model_path }}</span></td>'
agent_cell = (
"              <td>\n"
"                <span class=\"status-badge {{ 'on' if ct.agent_online else 'off' }}\" title=\"{{ (ct.agent_hostname or '') + (', ' + ct.agent_last_seen|string if ct.agent_last_seen else '') }}\">\n"
"                  <span class=\"dot\"></span>\n"
"                  <span>{{ 'Online' if ct.agent_online else 'Offline' }}</span>\n"
"                </span>\n"
"              </td>"
)
if model_cell in s and agent_cell not in s:
    s = s.replace(model_cell, model_cell + "\n" + agent_cell)
# Adjust colspan
s = s.replace('colspan="5"', 'colspan="6"')
p.write_text(s, encoding='utf-8')
print('updated')
