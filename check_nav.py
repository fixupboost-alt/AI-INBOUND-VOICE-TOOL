import sys, asyncio, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from ui_server import get_dashboard

async def test():
    resp = await get_dashboard()
    html = resp.body.decode('utf-8', errors='replace')
    
    checks = [
        ("data-page nav items",   html.count('data-page=')),
        ("goTo() defined",        'function goTo' in html),
        ("initNav() defined",     'function initNav' in html),
        ("PAGE_LOADERS",          'PAGE_LOADERS' in html),
        ("addEventListener",      'addEventListener' in html),
        ("initNav() boot call",   'initNav();' in html),
        ("page-dashboard exists", 'id="page-dashboard"' in html),
        ("page-calendar exists",  'id="page-calendar"' in html),
        ("page-agent exists",     'id="page-agent"' in html),
        ("page-logs exists",      'id="page-logs"' in html),
        ("NO inline onclick nav", 'onclick="goTo(' not in html),
    ]
    for name, result in checks:
        icon = "OK" if result else "BROKEN"
        print(f"[{icon}] {name}: {result}")

asyncio.run(test())
