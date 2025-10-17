from pathlib import Path
p=Path('templates/tc_dashboard.html')
s=p.read_text(encoding='utf-8')
old = (
"          <div id=\"status-{{ ct.id }}\" class=\"status-pill status-off\">\n"
"            <span class=\"status-dot\"></span><span id=\"status-text-{{ ct.id }}\">Parada</span>\n"
"          </div>\n"
)
new = (
"          <div style=\"display:flex; gap:8px; align-items:center;\">\n"
"            <div class=\"status-pill {{ 'status-on' if ct.agent_online else 'status-off' }}\" title=\"{{ ct.agent_hostname or '' }}\">\n"
"              <span class=\"status-dot\"></span><span>{{ 'Agente Online' if ct.agent_online else 'Agente Offline' }}</span>\n"
"            </div>\n"
"            <div id=\"status-{{ ct.id }}\" class=\"status-pill status-off\">\n"
"              <span class=\"status-dot\"></span><span id=\"status-text-{{ ct.id }}\">Parada</span>\n"
"            </div>\n"
"          </div>\n"
)
if old in s:
    s = s.replace(old, new)
    p.write_text(s, encoding='utf-8')
    print('updated')
else:
    print('pattern not found')
