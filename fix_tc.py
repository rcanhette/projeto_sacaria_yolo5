from pathlib import Path
p=Path('routes/tc.py')
text=p.read_text(encoding='utf-8').splitlines()
needle='cp = get_or_create_shadow(tc_id, name=tc_row.get("name"))'
occ=[i for i,l in enumerate(text) if needle in l]
for i in occ[:3]:
    text[i]=text[i].replace(needle,'cp = _ensure_cp(tc_row)')
p.write_text('\n'.join(text), encoding='utf-8')
print('occurrences', occ)
